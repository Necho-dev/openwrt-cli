"""Settings pages for the connected TUI session."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime

from rich.cells import cell_len
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Label, RichLog, Select, SelectionList, Static, TabbedContent, TabPane
from textual.worker import WorkerCancelled, WorkerFailed, get_current_worker

from openwrt_cli.core.config import McpModeError, ProfileError, find_profile, mcp_mode, profile_target, validate_profile_name
from openwrt_cli.core.sampling_config import parse_interval, sampling_settings
from openwrt_cli.i18n import current_language, t
from openwrt_cli.services.bandix import norm_mac
from openwrt_cli.services.sample_store import SampleWriterBusy
from openwrt_cli.services.sampler import bandix_history, enabled_sources, now_ms, router_key
from openwrt_cli.tui.keyhint import LABEL_STYLE, action_line, highlight_keys
from openwrt_cli.tui.screens.runtime_logs import RuntimeLogsPane

LANGUAGES = {"en": "English", "zh": "简体中文"}
MODES = ("readonly", "readwrite")
PORTS = {"ssh": 22, "http": 80, "https": 443}
TABS = ("settings-languages", "settings-mcp", "settings-profiles", "settings-sample", "settings-logs")


class SettingsTable(DataTable):
    class Activated(Message):
        def __init__(self, table: SettingsTable):
            super().__init__()
            self.table = table

    def action_select_cursor(self) -> None:
        # Mouse clicks highlight a choice. Enter explicitly applies the choice.
        self.post_message(self.Activated(self))

    @property
    def selected(self) -> str | None:
        if self.is_valid_row_index(self.cursor_row):
            return self.ordered_rows[self.cursor_row].key.value
        return None

    def select(self, key: str | None) -> None:
        for index, row in enumerate(self.ordered_rows):
            if row.key.value == key:
                self.move_cursor(row=index)
                break


class SettingsTabs(TabbedContent):
    @on(TabPane.Focused)
    def keep_active_tab(self, event: TabPane.Focused) -> None:
        # A focus event queued by the previous table must not switch the tab back.
        event.prevent_default()
        event.stop()


def _section(page: str, *actions: tuple[tuple[str, str, str], ...], note: bool = False) -> ComposeResult:
    with Horizontal(classes="settings-split"):
        yield SettingsTable(id=f"settings-{page}-table", classes="settings-list", cursor_type="row", zebra_stripes=True)
        with VerticalScroll(classes="settings-detail"):
            yield Static("", id=f"settings-{page}-detail", markup=False)
            for row in actions:
                yield from action_line(*row)
            if note:
                yield Static("", id=f"settings-{page}-note", classes="settings-note", markup=False)


def _detail(heading: str, *items: tuple[str, str], note: str = "", compact: bool = False) -> Text:
    output = Text()
    output.append(heading, style="bold #7ec8ff")
    if not compact:
        output.append("\n")
    width = max((cell_len(label) for label, _ in items), default=0)
    for label, value in items:
        output.append(f"\n{label}" + " " * (width - cell_len(label) + 2), style=LABEL_STYLE)
        output.append(value)
    if note:
        output.append(f"\n\n{note}")
    return output


class ProfileForm(ModalScreen[dict | None]):
    DEFAULT_CSS = """
    ProfileForm { align: center middle; }
    ProfileForm > Vertical { width: 76; max-width: 95%; height: 90%; max-height: 32; padding: 1 2;
        border: solid #4c8dff; background: #12283f; }
    ProfileForm VerticalScroll { height: 1fr; }
    ProfileForm .profile-title { height: 1; color: #7ec8ff; text-style: bold; margin-bottom: 1; }
    ProfileForm .profile-hint { height: auto; }
    ProfileForm .profile-field { height: 2; }
    ProfileForm .profile-field Label { width: 19; height: 1; color: #8aa4b8; }
    ProfileForm .profile-field Input, ProfileForm .profile-field Select, ProfileForm SelectCurrent {
        width: 1fr; height: 1; min-height: 1; border: none; padding: 0 1;
        color: #f2f6fb; background: #16324d;
    }
    ProfileForm Input:focus, ProfileForm Select:focus SelectCurrent { background: #1e4a7a; }
    ProfileForm SelectOverlay { background: #16324d; border: solid #4c8dff; color: #f2f6fb; }
    ProfileForm .settings-buttons { height: 3; align: right middle; }
    ProfileForm .settings-buttons Button { margin-left: 1; width: 14; min-width: 14; }
    ProfileForm .profile-option { margin-left: 19; margin-bottom: 1; height: 1; min-height: 1;
        padding: 0 1; border: none; background: #16324d; }
    ProfileForm .profile-option:focus { background: #1e4a7a; }
    ProfileForm #profile-error { height: auto; color: $error; display: none; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "", show=False, priority=True),
        Binding("ctrl+s", "save", "", show=False, priority=True),
    ]

    def __init__(self, profile: dict | None):
        super().__init__()
        self.profile = profile or {}

    def compose(self) -> ComposeResult:
        profile = self.profile
        mode = (profile.get("mcp") or {}).get("mode", "inherit")
        self._transport = profile.get("scheme", "https") if profile.get("transport") == "http" else "ssh"
        with Vertical():
            yield Label(t("settings.edit_profile" if profile else "settings.add_profile"), classes="profile-title")
            with VerticalScroll():
                for key in ("name", "host"):
                    with Horizontal(classes="profile-field"):
                        yield Label(t(f"settings.profile_{key}"))
                        yield Input(str(profile.get(key) or ""), id=f"profile-{key}")
                with Horizontal(classes="profile-field"):
                    yield Label(t("settings.transport"))
                    yield Select([("SSH", "ssh"), ("HTTPS", "https"), ("HTTP", "http")], value=self._transport,
                                 allow_blank=False, compact=True, id="profile-transport")
                for key, placeholder in (("port", str(PORTS[self._transport])), ("user", "root")):
                    with Horizontal(classes="profile-field"):
                        yield Label(t(f"settings.profile_{key}"))
                        yield Input(str(profile.get(key) or ""), id=f"profile-{key}", placeholder=placeholder)
                with Horizontal(classes="profile-field", id="profile-identity-field"):
                    yield Label(t("settings.identity"))
                    yield Input(str(profile.get("identity_file") or ""), id="profile-identity")
                with Horizontal(classes="profile-field"):
                    yield Label(t("settings.password"))
                    yield Input(password=True, id="profile-password",
                                placeholder=t("settings.keep_password") if profile.get("password") else "")
                if profile.get("password"):
                    yield Checkbox(t("settings.clear_password"), id="profile-clear-password", classes="profile-option")
                yield Checkbox(t("settings.verify_ssl"), value=bool(profile.get("verify_ssl")),
                               id="profile-tls", classes="profile-option")
                with Horizontal(classes="profile-field"):
                    yield Label(t("settings.mcp"))
                    yield Select([(t("settings.inherit"), "inherit"), *[(choice, choice) for choice in MODES]],
                                 value=mode if mode in ("inherit", *MODES) else "inherit",
                                 allow_blank=False, compact=True, id="profile-mcp")
            yield Static("", id="profile-error", markup=False)
            yield Static(highlight_keys(t("settings.form_keys")), classes="profile-hint")
            with Horizontal(classes="settings-buttons"):
                yield Button(t("action.cancel").upper(), id="profile-cancel")
                yield Button(t("settings.save").upper(), id="profile-save", variant="success")

    def on_mount(self) -> None:
        self._transport_fields(self._transport)
        self.query_one("#profile-name", Input).focus()

    @on(Select.Changed, "#profile-transport")
    def transport_changed(self, event: Select.Changed) -> None:
        if event.value in PORTS:
            self._transport_fields(event.value)

    def _transport_fields(self, value: str) -> None:
        self.query_one("#profile-identity-field").display = value == "ssh"
        self.query_one("#profile-tls", Checkbox).display = value == "https"
        port = self.query_one("#profile-port", Input)
        port.placeholder = str(PORTS[value])
        if value != self._transport and port.value == str(PORTS[self._transport]):
            port.value = ""
        self._transport = value

    @on(Button.Pressed, "#profile-cancel")
    def cancel(self) -> None:
        self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#profile-save")
    def save(self) -> None:
        self.action_save()

    def action_save(self) -> None:
        def value(key: str) -> str:
            return self.query_one(f"#profile-{key}", Input).value.strip()

        def invalid(key: str, message: str) -> None:
            error = self.query_one("#profile-error", Static)
            error.update(message)
            error.display = True
            self.query_one(f"#profile-{key}", Input).focus()

        name, host = value("name"), value("host")
        transport = self.query_one("#profile-transport", Select).value
        port = value("port")
        try:
            validate_profile_name(name)
        except ProfileError:
            invalid("name", t("err.profile_name"))
            return
        if name != self.profile.get("name") and find_profile(self.app.cfg, name):
            invalid("name", t("err.profile_exists", name=name))
            return
        if not host:
            invalid("host", t("err.profile_need_host"))
            return
        if port and (len(port) > 5 or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535):
            invalid("port", t("settings.invalid_port"))
            return
        fields = {
            "host": host, "user": value("user") or "root",
            "transport": "ssh" if transport == "ssh" else "http",
            "scheme": None if transport == "ssh" else transport,
            "port": int(port) if port else PORTS[transport],
            "identity_file": value("identity"),
            "verify_ssl": self.query_one("#profile-tls", Checkbox).value,
        }
        password = self.query_one("#profile-password", Input).value
        if password or self.profile.get("password") and self.query_one("#profile-clear-password", Checkbox).value:
            fields["password"] = password
        self.dismiss({"name": name, "fields": fields, "mcp": self.query_one("#profile-mcp", Select).value})


class SettingsPane(Vertical):
    DEFAULT_CSS = """
    SettingsPane { height: 1fr; }
    SettingsPane VerticalScroll { padding: 1 2; }
    SettingsPane #settings-tabs { height: 1fr; }
    SettingsPane .settings-split { height: 1fr; }
    SettingsPane .settings-list { width: 3fr; height: 1fr; }
    SettingsPane .settings-detail { width: 2fr; height: 1fr; border-left: solid #2a4660; }
    SettingsPane.-narrow .settings-split { layout: vertical; }
    SettingsPane.-narrow .settings-list { width: 1fr; height: 2fr; }
    SettingsPane.-narrow #settings-language-table, SettingsPane.-narrow #settings-mcp-table { height: 3; }
    SettingsPane.-narrow .settings-detail {
        width: 1fr; height: 3fr; padding: 0 2; border-left: none; border-top: solid #2a4660;
    }
    SettingsPane .settings-detail > Static { margin-bottom: 1; }
    SettingsPane.-narrow .settings-detail > Static { margin-bottom: 0; }
    SettingsPane .settings-note { color: #8aa4b8; }
    SettingsPane .settings-actions { height: 1; margin-bottom: 1; }
    SettingsPane .settings-actions Button {
        height: 1; min-height: 1; min-width: 0; width: auto;
        margin-right: 2; padding: 0; border: none; background: transparent;
    }
    SettingsPane .settings-actions Button:hover, SettingsPane .settings-actions Button:focus {
        background: #1e4a7a; text-style: underline;
    }
    SettingsPane #sample-form { width: 3fr; height: 1fr; }
    SettingsPane.-narrow #sample-form { width: 1fr; height: 3fr; padding: 0 2; }
    SettingsPane #sample-detail { padding: 1 2; }
    SettingsPane.-narrow #sample-detail { height: 3fr; padding: 0 2; }
    SettingsPane #sample-state { height: 1fr; padding: 0; }
    SettingsPane #sample-actions { height: auto; }
    SettingsPane.-narrow #sample-actions .settings-actions { margin-bottom: 0; }
    SettingsPane .sample-heading { color: #7ec8ff; text-style: bold; margin-top: 1; }
    SettingsPane #sample-form Checkbox {
        height: 1; min-height: 1; border: none; padding: 0;
    }
    SettingsPane .sample-interval { height: 1; }
    SettingsPane .sample-interval Label { margin: 0 1 0 0; width: auto; color: #8aa4b8; }
    SettingsPane .sample-interval Input {
        width: 10; height: 1; min-height: 1; padding: 0 1; border: none; background: #16324d;
    }
    SettingsPane .sample-interval Input:focus { background: #1e4a7a; }
    SettingsPane Input, SettingsPane Select { max-width: 52; }
    SettingsPane SelectionList { height: 8; max-width: 90; }
    SettingsPane #sample-passwall2-box { height: auto; padding-left: 2; }
    SettingsPane Label { margin-top: 1; }
    SettingsPane Static { height: auto; }
    """
    BINDINGS = [
        Binding("a", "profile('add')", "", show=False),
        Binding("e", "profile('edit')", "", show=False),
        Binding("delete", "profile('delete')", "", show=False),
        Binding("backspace", "profile('delete')", "", show=False),
        Binding("ctrl+s", "save_sample", "", show=False),
    ]
    connecting_profile: str | None = None

    def compose(self) -> ComposeResult:
        cfg = self.app.cfg
        settings = sampling_settings(cfg, cfg.get("active"))
        with SettingsTabs(id="settings-tabs"):
            with TabPane(t("settings.languages"), id="settings-languages"):
                yield from _section("language", (("language-apply", "Enter", "settings.apply"),))
            with TabPane(t("settings.mcp"), id="settings-mcp"):
                yield from _section("mcp", (("mcp-save", "Enter", "settings.save"),), note=True)
            with TabPane(t("settings.profiles"), id="settings-profiles"):
                yield from _section(
                    "profile",
                    (("profiles-switch", "Enter", "settings.switch"),),
                    (("profiles-add", "a", "settings.add"), ("profiles-edit", "e", "action.edit"),
                     ("profiles-delete", "Del", "settings.delete")),
                )
            with TabPane(t("settings.sample"), id="settings-sample"):
                with Horizontal(classes="settings-split"):
                    with VerticalScroll(id="sample-form"):
                        yield from self._sample_form(settings)
                    with Vertical(id="sample-detail", classes="settings-detail"):
                        with VerticalScroll(id="sample-state"):
                            yield Static("", id="sample-status", markup=False)
                        with Vertical(id="sample-actions"):
                            yield from action_line(("sample-save", "Ctrl+S", "settings.save"))
                            yield from action_line(("sample-start", "", "action.start"), ("sample-stop", "", "action.stop"),
                                                   ("sample-history", "", "sample.history_short"))
            with TabPane(t("tab.logs"), id="settings-logs"):
                yield RuntimeLogsPane()

    def _sample_form(self, settings: dict) -> ComposeResult:
        logs, metrics = settings["logs"], settings["metrics"]
        passwall, acls = logs["passwall2"], logs["passwall2"]["acls"]
        yield Static(t("sample.general"), classes="sample-heading")
        yield Checkbox(t("sample.enabled"), settings["enabled"], id="sample-enabled")
        yield Checkbox(t("sample.on_start"), settings["on_start"], id="sample-on-start")
        yield Static(t("sample.logs"), classes="sample-heading")
        yield Checkbox(t("sample.logs_enabled"), logs["enabled"], id="sample-logs")
        with Horizontal(classes="sample-interval"):
            yield Label(t("sample.interval"))
            yield Input(f"{logs['interval']:g}", id="sample-log-interval")
        yield Checkbox(t("sample.system"), logs["system"], id="sample-system")
        yield Checkbox(t("sample.kernel"), logs["kernel"], id="sample-kernel")
        with Vertical(id="sample-passwall2-box"):
            yield Checkbox(t("sample.passwall2"), passwall["enabled"], id="sample-passwall2")
            yield Checkbox(t("sample.runtime"), passwall["runtime"], id="sample-runtime")
            yield Checkbox(t("sample.acls"), acls["enabled"], id="sample-acls")
            yield Static(t("sample.no_logged_acls"), id="sample-no-acls", markup=False)
            yield Select([(t("sample.all_acls"), "all"), (t("sample.selected_acls"), "selected")],
                         value="selected" if acls["sections"] else "all", allow_blank=False, id="sample-acl-scope")
            yield SelectionList[str](id="sample-sections")
        yield Static(t("sample.metrics"), classes="sample-heading")
        yield Checkbox(t("sample.metrics_enabled"), metrics["enabled"], id="sample-metrics")
        with Horizontal(classes="sample-interval"):
            yield Label(t("sample.interval"))
            yield Input(f"{metrics['interval']:g}", id="sample-metric-interval")
        yield Checkbox(t("sample.bandix"), metrics["bandix"]["enabled"], id="sample-bandix")

    def on_mount(self) -> None:
        self.refresh_preferences()
        self.refresh_capabilities()
        self.refresh_status()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 78, "-narrow")

    def focus_page(self) -> None:
        active = self.query_one("#settings-tabs", TabbedContent).active
        key = {"settings-languages": "language", "settings-mcp": "mcp", "settings-profiles": "profile"}.get(active)
        if key:
            self.query_one(f"#settings-{key}-table", SettingsTable).focus()
        elif active == "settings-sample":
            self.refresh_status()
            self.query_one("#sample-enabled", Checkbox).focus()
        elif active == "settings-logs":
            if self.app.query_one("#filter-bar").display:
                self.app.action_clear_filter()
            self.query_one(RuntimeLogsPane).open()

    def step_page(self, step: int) -> None:
        tabs = self.query_one("#settings-tabs", TabbedContent)
        if tabs.active in TABS:
            tabs.active = TABS[(TABS.index(tabs.active) + step) % len(TABS)]

    def help_text(self) -> str:
        active = self.query_one("#settings-tabs", TabbedContent).active
        if active == "settings-logs":
            return t("runtime.keys")
        page = {"settings-languages": "language", "settings-mcp": "mcp", "settings-profiles": "profile"}.get(active)
        return t(f"settings.{page or 'sample'}_keys")

    def refresh_preferences(self, *, profile: str | None = None) -> None:
        language = self.query_one("#settings-language-table", SettingsTable)
        mcp = self.query_one("#settings-mcp-table", SettingsTable)
        profiles = self.query_one("#settings-profile-table", SettingsTable)
        for table, columns in (
            (language, (("name", "settings.language"), ("status", "settings.status"))),
            (mcp, (("name", "settings.mode"), ("status", "settings.status"))),
            (profiles, (("name", "settings.name"), ("status", "settings.status"), ("target", "col.target"))),
        ):
            if not table.columns:
                for key, label in columns:
                    table.add_column(t(label), key=key)
        if not language.row_count:
            for key, label in LANGUAGES.items():
                language.add_row(label, t("settings.current") if key == current_language() else "—", key=key)
            language.select(current_language())
        if not mcp.row_count:
            for mode in MODES:
                mcp.add_row(mode, "—", key=mode)
            mcp.select((self.app.cfg.get("mcp") or {}).get("mode"))
        self.refresh_language()
        self.refresh_profiles(profile)

    def refresh_language(self) -> None:
        choice = self.query_one("#settings-language-table", SettingsTable).selected
        saved = current_language()
        self.query_one("#settings-language-detail", Static).update(_detail(
            LANGUAGES.get(choice, "—"),
            (t("settings.current_language"), LANGUAGES[saved]),
            note=t("settings.language_note") if choice != saved else "",
        ))
        self.query_one("#language-apply", Button).disabled = choice is None or choice == saved

    def refresh_mcp(self) -> None:
        table = self.query_one("#settings-mcp-table", SettingsTable)
        choice = table.selected
        cfg = self.app.cfg
        saved = (cfg.get("mcp") or {}).get("mode", "readonly")
        for mode in MODES:
            table.update_cell(mode, "status", t("settings.saved_status") if mode == saved else "—", update_width=True)
        account = find_profile(cfg, cfg.get("active"))
        values = [(t("settings.saved_mode"), str(saved))]
        if account is not None:
            values.extend([
                (t("settings.active_profile"), account["name"]),
                (t("settings.configured_mode"), self._mode_label(account)),
                (t("settings.effective_mode"), self._effective(account)),
            ])
            if choice in MODES and choice != saved:
                values.append((t("settings.after_save"), self._effective(account, candidate=choice)))
        self.query_one("#settings-mcp-detail", Static).update(_detail(
            t("settings.mcp_ceiling"), (t("settings.selected_mode"), choice or "—"), *values,
        ))
        self.query_one("#settings-mcp-note", Static).update(t(f"settings.mode_{choice}") if choice in MODES else "")
        self.query_one("#mcp-save", Button).disabled = choice not in MODES or choice == saved

    @staticmethod
    def _mode_label(profile: dict) -> str:
        return (profile.get("mcp") or {}).get("mode") or t("settings.inherit")

    def _effective(self, profile: dict, *, candidate: str | None = None) -> str:
        cfg = self.app.cfg
        if candidate is not None:
            cfg = {**cfg, "mcp": {**(cfg.get("mcp") or {}), "mode": candidate}}
        try:
            return mcp_mode(cfg, profile)
        except McpModeError:
            return "—"

    def refresh_profiles(self, preferred: str | None = None) -> None:
        table = self.query_one("#settings-profile-table", SettingsTable)
        active = self.app.cfg.get("active")
        items = self.app.cfg.get("profiles") or []
        selected = preferred or table.selected
        table.clear()
        for item in items:
            name = item["name"]
            status = t("settings.active_profile") if name == active else "—"
            table.add_row(Text(name), status, Text(profile_target(item)), key=name)
        table.select(selected if find_profile(self.app.cfg, selected) else active)
        self.refresh_profile_detail()
        self.refresh_mcp()

    def refresh_profile_detail(self) -> None:
        profile = self.selected_profile()
        active = bool(profile and profile["name"] == self.app.cfg.get("active"))
        note = t("settings.connecting_profile", name=self.connecting_profile) if self.connecting_profile else ""
        if profile is not None:
            summary = _detail(profile["name"],
                              (t("col.target"), profile_target(profile)),
                              (t("settings.configured_mode"), self._mode_label(profile)),
                              (t("settings.effective_mode"), self._effective(profile)),
                              note=note or (t("settings.active_locked") if active else ""))
        else:
            summary = Text(t("msg.no_profiles"))
        self.query_one("#settings-profile-detail", Static).update(summary)
        self.query_one("#profiles-add", Button).disabled = bool(self.connecting_profile)
        for button in ("switch", "edit", "delete"):
            self.query_one(f"#profiles-{button}", Button).disabled = not profile or active or bool(self.connecting_profile)

    def selected_profile(self) -> dict | None:
        choice = self.query_one("#settings-profile-table", SettingsTable).selected
        return find_profile(self.app.cfg, choice)

    def refresh_capabilities(self, *, switched: bool = False) -> None:
        app = self.app
        self.query_one("#sample-passwall2-box").display = app._pw2
        self.query_one("#sample-bandix", Checkbox).display = app.sample_bandix
        sections = self.query_one("#sample-sections", SelectionList)
        scope = self.query_one("#sample-acl-scope", Select)
        selected = set(sections.selected)
        if switched or not sections.option_count:
            config = sampling_settings(app.cfg, app.cfg.get("active"))
            selected = set(config["logs"]["passwall2"]["acls"]["sections"])
            scope.value = "selected" if selected else "all"
        sections.clear_options()
        catalog = {row["id"]: row for row in app.sample_acls}
        available = any(row.get("sampling_available") for row in catalog.values())
        self.query_one("#sample-no-acls", Static).display = not available
        self.query_one("#sample-acls", Checkbox).disabled = not (available or selected)
        scope.disabled = not (available or selected)
        for section in sorted(set(catalog) | selected):
            row = catalog.get(section, {})
            if row.get("sampling_available"):
                label = f"{row['remarks'] or section} · {row['node_remarks']} ({section})"
            elif section in selected:
                label = f"{section} · {t('sample.acl_unavailable')}"
            else:
                continue
            sections.add_option((Text(label), section, section in selected))
        sections.display = scope.value == "selected"

    def refresh_status(self) -> None:
        sampler = self.app.sampler
        status = sampler.status
        stamp = datetime.fromtimestamp(status.last_success / 1000).strftime("%H:%M:%S") if status.last_success else "—"
        note = t("sample.tui_busy") if status.error == SampleWriterBusy.__name__ else status.error
        self.query_one("#sample-status", Static).update(_detail(
            t(f"sample.state.{status.state}"),
            (t("sample.last_write"), stamp),
            (t("sample.gaps"), str(status.gaps)),
            (t("sample.database"), f"{sampler.store.size() / 1024**2:.1f} MiB"),
            note=note, compact=True,
        ))
        configured = sampling_settings(self.app.cfg, self.app.cfg.get("active"))
        self.query_one("#sample-start", Button).disabled = (
            status.state in {"running", "starting", "stopping"} or not enabled_sources(configured)
        )
        self.query_one("#sample-stop", Button).disabled = status.state not in {"running", "starting"}

    @on(Select.Changed)
    def selection(self, event: Select.Changed) -> None:
        if event.select.id == "sample-acl-scope":
            self.query_one("#sample-sections", SelectionList).display = event.value == "selected"

    @on(DataTable.RowHighlighted)
    def highlight_setting(self, event: DataTable.RowHighlighted) -> None:
        match event.data_table.id:
            case "settings-language-table":
                self.refresh_language()
            case "settings-mcp-table":
                self.refresh_mcp()
            case "settings-profile-table":
                self.refresh_profile_detail()

    @on(SettingsTable.Activated)
    def activate_setting(self, event: SettingsTable.Activated) -> None:
        match event.table.id:
            case "settings-language-table":
                self.apply_language()
            case "settings-mcp-table":
                self.apply_mcp()
            case "settings-profile-table":
                self.action_profile("switch")

    def apply_language(self) -> None:
        choice = self.query_one("#settings-language-table", SettingsTable).selected
        if choice in LANGUAGES and choice != current_language():
            self.app.confirm_setting(
                t("settings.confirm_language", current=LANGUAGES[current_language()], target=LANGUAGES[choice]),
                lambda: self.app.call_later(self.app.change_language, choice),
            )

    def apply_mcp(self) -> None:
        choice = self.query_one("#settings-mcp-table", SettingsTable).selected
        current = (self.app.cfg.get("mcp") or {}).get("mode", "readonly")
        if choice not in MODES or choice == current:
            return

        def save() -> None:
            self.app.change_mcp_mode(choice)
            self.refresh_mcp()

        message = t("settings.confirm_mcp", current=current, target=choice)
        self.app.confirm_setting(f"{message}\n\n{t(f'settings.mode_{choice}')}", save)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        if action == "profile":
            return self.query_one("#settings-tabs", TabbedContent).active == "settings-profiles"
        if action == "save_sample":
            return self.query_one("#settings-tabs", TabbedContent).active == "settings-sample"
        return True

    def action_profile(self, action: str) -> None:
        if self.connecting_profile:
            return
        app = self.app
        selected = self.selected_profile()
        if action == "add":
            app.push_screen(ProfileForm(None), lambda draft: app.apply_profile(None, draft))
            return
        if selected is None or selected["name"] == app.cfg.get("active"):
            return
        name = selected["name"]
        match action:
            case "edit":
                app.push_screen(ProfileForm(selected), lambda draft: app.apply_profile(name, draft))
            case "delete":
                app.confirm_profile_delete(name)
            case "switch":
                app.confirm_profile_switch(name, self.switch_profile)

    def switch_profile(self, name: str) -> None:
        self.connecting_profile = name
        self.refresh_profile_detail()
        self.wait_for_switch(self.app._switch_account(name))

    def action_save_sample(self) -> None:
        self.save_sampling()

    @work
    async def wait_for_switch(self, worker) -> None:
        try:
            await worker.wait()
        except (WorkerFailed, WorkerCancelled):
            pass
        finally:
            self.connecting_profile = None
            if self.is_mounted:
                self.refresh_profile_detail()

    @on(Button.Pressed)
    def action(self, event: Button.Pressed) -> None:
        app = self.app
        match event.button.id:
            case "language-apply":
                self.apply_language()
            case "mcp-save":
                self.apply_mcp()
            case "profiles-add" | "profiles-edit" | "profiles-delete" | "profiles-switch":
                self.action_profile(event.button.id.removeprefix("profiles-"))
            case "sample-save":
                self.save_sampling()
            case "sample-start":
                app.sampler.start()
                self.refresh_status()
            case "sample-stop":
                app.sampler.stop()
                self.refresh_status()
            case "sample-history":
                app.show_sampling_history()

    def save_sampling(self) -> None:
        def enabled(name: str) -> bool:
            return self.query_one(f"#sample-{name}", Checkbox).value

        try:
            log_interval, metric_interval = (
                parse_interval(self.query_one(f"#sample-{key}-interval", Input).value)
                for key in ("log", "metric")
            )
        except ValueError:
            self.app.notify(t("sample.invalid_interval"), severity="error")
            return
        all_acls = self.query_one("#sample-acl-scope", Select).value == "all"
        selected = [] if all_acls else self.query_one("#sample-sections", SelectionList).selected
        if not all_acls and not selected and self.app._pw2 and enabled("passwall2") and enabled("acls"):
            self.app.notify(t("sample.pick_acl"), severity="error")
            return
        current = self.app.cfg.get("sampling")
        profiles = deepcopy(current.get("profiles") or {}) if isinstance(current, dict) else {}
        if not isinstance(profiles, dict):
            profiles = {}
        settings = sampling_settings(self.app.cfg)
        settings.update(enabled=enabled("enabled"), on_start=enabled("on-start"))
        logs, metrics = settings["logs"], settings["metrics"]
        logs.update(enabled=enabled("logs"), interval=log_interval, system=enabled("system"), kernel=enabled("kernel"))
        metrics.update(enabled=enabled("metrics"), interval=metric_interval)
        if self.app.sample_bandix:
            metrics["bandix"]["enabled"] = enabled("bandix")
        if self.app._pw2:
            passwall = logs["passwall2"]
            passwall.update(enabled=enabled("passwall2"), runtime=enabled("runtime"))
            passwall["acls"]["enabled"] = enabled("acls")
            profile = self.app.cfg.get("active")
            if profile:
                profiles[profile] = {"sections": selected}
            else:
                passwall["acls"]["sections"] = selected
        if profiles:
            settings["profiles"] = profiles
        if self.app.save_settings({"sampling": settings}):
            self.app.sampler.configure(self.app.cfg)
            self.refresh_status()


def _stamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M:%S")


class SamplingHistory(ModalScreen[None]):
    DEFAULT_CSS = """
    SamplingHistory { align: center middle; }
    SamplingHistory > Vertical { width: 95%; height: 90%; padding: 1;
        border: round $accent; background: $surface; }
    SamplingHistory Horizontal { height: auto; }
    SamplingHistory Select { width: 1fr; }
    SamplingHistory Input { width: 22; }
    SamplingHistory RichLog { height: 1fr; margin-top: 1; }
    """
    BINDINGS = [("escape", "close", "")]

    def __init__(self, store, config: dict, device, bandix: bool):
        super().__init__()
        self.store = store
        self.router = router_key(config)
        self.device = device
        self.bandix = bandix

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(t("sample.history"))
            with Horizontal():
                yield Select([(t("sample.history_hours", hours=n), n) for n in (1, 3, 5, 24)],
                             value=1, allow_blank=False, id="history-hours")
                yield Select([(t("sample.history_coverage"), "coverage")], value="coverage",
                             allow_blank=False, id="history-source")
                yield Input(placeholder=t("sample.history_mac"), id="history-mac")
                yield Button(t("action.refresh"), id="history-refresh")
            yield RichLog(wrap=True, markup=False, max_lines=220, id="history-view")
            yield Button(t("action.cancel"), id="history-close")

    def on_mount(self) -> None:
        self.refresh_history()

    def action_close(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#history-close")
    def close_button(self) -> None:
        self.action_close()

    @on(Button.Pressed, "#history-refresh")
    def refresh_history(self) -> None:
        hours = self.query_one("#history-hours", Select).value
        source = self.query_one("#history-source", Select).value
        mac = self.query_one("#history-mac", Input).value.strip()
        if mac and not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", norm_mac(mac)):
            self.app.notify(t("sample.history_mac_invalid"), severity="error")
            return
        self.load_history(hours, source, norm_mac(mac) or "all")

    @work(thread=True, exclusive=True)
    def load_history(self, hours: int, source: str, subject: str) -> None:
        worker = get_current_worker()
        end = now_ms()
        start = end - hours * 3_600_000
        options = [(t("sample.history_coverage"), "coverage")]
        try:
            coverage = self.store.history(self.router, start)
            sources = {name for name, *_ in coverage} - {"connection", "logs", "bandix.history"}
            if self.bandix or "bandix.devices" in sources:
                sources.add("bandix.devices")
            options.extend((name, name) for name in sorted(sources))
            if source == "coverage":
                lines = [t("sample.history_batch", source=name, first=_stamp(first), last=_stamp(last),
                           count=count, gaps=gaps) for name, first, last, count, gaps in coverage]
            elif source == "bandix.devices":
                lines = self.bandix_lines(subject, start, end)
            elif source == "openwrt.metrics":
                rows = self.store.metrics(self.router, source, "router", start)
                lines = self.metric_lines(rows)
            else:
                rows = self.store.logs(self.router, source, start)
                lines = []
                for at, entry, detail in rows:
                    stamp = _stamp(at) if entry.get("time") else t("sample.history_observed", time=_stamp(at))
                    message = entry.get("raw") or entry.get("msg") or entry.get("message") or entry
                    lines.append(f"{stamp}  {message}" + (f"  [{t('sample.history_binding')} {detail}]" if detail else ""))
        except Exception as exc:
            lines = [t("sample.history_error", error=type(exc).__name__)]

        def apply() -> None:
            if worker.is_cancelled or not self.is_mounted:
                return
            select = self.query_one("#history-source", Select)
            select.set_options(options)
            select.value = source if source in dict(options).values() else "coverage"
            log = self.query_one("#history-view", RichLog)
            log.clear()
            for line in lines or [t("sample.history_empty")]:
                log.write(line)

        if not worker.is_cancelled:
            self.app.call_from_thread(apply)

    def bandix_lines(self, subject: str, start: int, end: int) -> list[str]:
        native = sorted(bandix_history(self.device, None if subject == "all" else subject),
                        key=lambda point: point[0]) if self.bandix else []
        if native and native[0][0] <= start and native[-1][0] >= end - 60_000:
            rows = [(stamp, point) for stamp, point in native if stamp >= start]
            return [t("sample.history_native"), *self.metric_lines(rows)]
        # Keep native Bandix points and samples with different units/fields separate.
        saved = self.store.metrics(self.router, "bandix.devices", subject, start)
        native_points = dict(self.store.metrics(self.router, "bandix.history", subject, start))
        native_points.update((stamp, point) for stamp, point in native if stamp >= start)
        lines = []
        if native_points:
            lines.extend([t("sample.history_partial"), *self.metric_lines(sorted(native_points.items()), limit=90)])
        if saved:
            lines.extend([t("sample.history_local"), *self.metric_lines(saved, limit=120)])
        return lines

    @staticmethod
    def metric_lines(rows: list[tuple[int, dict]], limit: int = 200) -> list[str]:
        if not rows:
            return []
        return [
            t("sample.history_points", total=len(rows), shown=min(len(rows), limit),
              first=_stamp(rows[0][0]), last=_stamp(rows[-1][0])),
            *(f"{_stamp(at)}  {json.dumps(point, ensure_ascii=False)}" for at, point in rows[-limit:]),
        ]
