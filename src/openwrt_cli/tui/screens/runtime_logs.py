"""TUI view and cleanup controls for local runtime logs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog, Static
from textual.worker import Worker, get_current_worker

from openwrt_cli.i18n import t
from openwrt_cli.services.runtime_logs import (
    MAX_RETENTION_DAYS,
    default_log_path,
    prune_runtime_logs,
    query_runtime_logs,
    retention_settings,
    runtime_log_snapshot,
)
from openwrt_cli.tui.keyhint import KEY_STYLE, LABEL_STYLE, action_line, highlight_keys

_LEVEL_COLOR = {"INFO": "#8aa4b8", "WARNING": "#f0a000", "ERROR": "#ed5c5c"}
_HEADER_FIELDS = frozenset({"time_ms", "level", "source", "event", "profile"})


class RetentionDialog(ModalScreen[int | None]):
    DEFAULT_CSS = """
    RetentionDialog { align: center middle; }
    RetentionDialog > Vertical { width: 60; max-width: 94%; height: auto;
        border: solid #4c8dff; background: #12283f; padding: 1 2; }
    RetentionDialog Label { color: #7ec8ff; text-style: bold; }
    RetentionDialog Input { width: 16; }
    RetentionDialog #runtime-form-error { height: auto; color: $error; display: none; }
    RetentionDialog Horizontal { height: 3; align: right middle; }
    RetentionDialog Button { margin-left: 1; width: 14; min-width: 14; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "", show=False, priority=True),
        Binding("ctrl+s", "submit", "", show=False, priority=True),
    ]

    def __init__(self, days: int):
        super().__init__()
        self.days = days

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(t("runtime.days"))
            yield Input(str(self.days), id="runtime-option-days", select_on_focus=True)
            yield Static("", id="runtime-form-error", markup=False)
            yield Static(highlight_keys(t("settings.form_keys")))
            with Horizontal():
                yield Button(t("action.cancel").upper(), id="runtime-form-cancel")
                yield Button(t("settings.save").upper(), id="runtime-form-save", variant="success")

    @on(Button.Pressed, "#runtime-form-save")
    @on(Input.Submitted, "#runtime-option-days")
    def submit(self) -> None:
        self.action_submit()

    @on(Button.Pressed, "#runtime-form-cancel")
    def cancel(self) -> None:
        self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        field = self.query_one("#runtime-option-days", Input)
        value = field.value.strip()
        if value.isascii() and value.isdecimal() and len(value) <= 4 and 1 <= int(value) <= MAX_RETENTION_DAYS:
            self.dismiss(int(value))
            return
        status = self.query_one("#runtime-form-error", Static)
        status.update(t("runtime.invalid_retention", maximum=MAX_RETENTION_DAYS))
        status.display = True
        field.focus()


def _format_event(row: dict) -> Text:
    stamp = datetime.fromtimestamp(row["time_ms"] / 1000).astimezone().isoformat(timespec="seconds")
    level = str(row.get("level") or "")
    event = Text()
    event.append(stamp, style=LABEL_STYLE)
    event.append(f" · {level}", style=_LEVEL_COLOR.get(level, ""))
    event.append(f" · {row.get('source', '')}", style=KEY_STYLE)
    if row.get("profile"):
        event.append(f"  [{row['profile']}]", style=LABEL_STYLE)
    event.append(f"  {row.get('event', '')}")
    metadata = [key for key in row if key not in _HEADER_FIELDS and key not in {"pid", "session"}]
    metadata.extend(key for key in ("pid", "session") if key in row)
    for key in metadata:
        event.append(f" · {key}=", style=LABEL_STYLE)
        event.append(str(row[key]))
    return event


class RuntimeLogsPane(Vertical):
    DEFAULT_CSS = """
    RuntimeLogsPane { height: 1fr; }
    RuntimeLogsPane #runtime-controls { height: auto; padding: 0 1; }
    RuntimeLogsPane #runtime-status { height: auto; padding: 0 1; color: $warning; display: none; }
    RuntimeLogsPane #runtime-content { height: 1fr; padding: 0 1; }
    """
    BINDINGS = [
        Binding("e", "retention", "", show=False),
        Binding("delete", "clean", "", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._snapshot: tuple[tuple[Path, int, int], ...] | None = None
        self._render_width = 0
        self._reader: Worker | None = None
        self.cleaning = False

    @property
    def root(self) -> Path:
        return self.app.journal.root if self.app.journal is not None else default_log_path()

    def compose(self) -> ComposeResult:
        with Vertical(id="runtime-controls"):
            yield from action_line(("runtime-retention", "e", "runtime.days"), ("runtime-clean", "Del", "runtime.clean"))
        yield Static("", id="runtime-status", markup=False)
        yield RichLog(id="runtime-content", min_width=1, wrap=True, highlight=False, markup=False)

    def on_mount(self) -> None:
        self._update_days()
        self.set_interval(5, self._refresh_visible)

    def open(self) -> None:
        self.query_one(RichLog).focus()
        self.refresh_logs()

    def _refresh_visible(self) -> None:
        if self.app._pane == "settings" and self.parent is not None and self.parent.display:
            self.refresh_logs()

    def refresh_logs(self, *, force: bool = False) -> None:
        if force or self._reader is None or self._reader.is_finished:
            self._reader = self.load_logs(self.root, self.query_one(RichLog).size.width)

    def _update_days(self) -> None:
        days, _ = retention_settings(self.app.cfg)
        caption = t("runtime.days_current", days=days)
        self.query_one("#runtime-retention", Button).label = Text.assemble(("e", KEY_STYLE), (f" · {caption}", LABEL_STYLE))

    def action_retention(self) -> None:
        if not self.cleaning:
            days, _ = retention_settings(self.app.cfg)
            self.app.push_screen(RetentionDialog(days), self._save_retention)

    def _save_retention(self, days: int | None) -> None:
        if days is None:
            return
        configured = self.app.cfg.get("runtime_logs")
        settings = {**configured, "days": days} if isinstance(configured, dict) else {"days": days}
        if self.app.save_settings({"runtime_logs": settings}):
            self._update_days()
            self.app.log_event("tui.log_retention_changed", days=days)
            self._start_cleanup(all_closed=False)

    def action_clean(self) -> None:
        if not self.cleaning:
            self.app.confirm_setting(t("runtime.confirm_clean"), lambda: self._start_cleanup(all_closed=True))

    def _start_cleanup(self, *, all_closed: bool) -> None:
        self.cleaning = True
        self.query_one("#runtime-clean", Button).disabled = True
        self.clean_archives(self.root, retention_settings(self.app.cfg), all_closed)

    @work(thread=True)
    def clean_archives(self, root: Path, policy: tuple[int, int], all_closed: bool) -> None:
        try:
            removed, size = prune_runtime_logs(root, policy, all_closed=all_closed)
            error = None
        except OSError as exc:
            removed, size, error = 0, 0, type(exc).__name__
        if error:
            self.app.log_event("tui.logs_cleanup_failed", level="ERROR", error=error)
        elif all_closed or removed:
            self.app.log_event("tui.logs_cleaned", removed_files=removed, removed_bytes=size)

        def apply() -> None:
            if not self.is_mounted:
                return
            self.cleaning = False
            self.query_one("#runtime-clean", Button).disabled = False
            if error:
                self.app.notify(t("runtime.cleanup_failed", error=error), severity="error")
            elif all_closed or removed:
                self.app.notify(t("runtime.cleaned", count=removed))
            self.refresh_logs(force=True)

        self.app.call_from_thread(apply)

    @work(thread=True, exclusive=True, group="runtime-log-reader")
    def load_logs(self, root: Path, width: int) -> None:
        worker = get_current_worker()
        try:
            snapshot = runtime_log_snapshot(root)
            rows = None
            if snapshot != self._snapshot or width != self._render_width:
                rows, _ = query_runtime_logs(root, cancelled=lambda: worker.is_cancelled, snapshot=snapshot)
            error = None
        except OSError as exc:
            snapshot, rows, error = None, None, type(exc).__name__

        def apply() -> None:
            if worker.is_cancelled or not self.is_mounted:
                return
            self._update_status(error)
            self._snapshot = snapshot
            self._render_width = width
            if rows is None:
                return
            log = self.query_one(RichLog)
            follow, offset = log.is_vertical_scroll_end, log.scroll_y
            log.clear()
            for row in reversed(rows):
                log.write(_format_event(row), scroll_end=False)
            if not rows:
                log.write(t("runtime.no_events"), scroll_end=False)
            if follow:
                log.scroll_end(animate=False)
            else:
                log.scroll_to(y=offset, animate=False)

        if not worker.is_cancelled:
            self.app.call_from_thread(apply)

    def _update_status(self, error: str | None) -> None:
        status = self.query_one("#runtime-status", Static)
        journal = self.app.journal
        message = ""
        if error:
            message = t("runtime.read_failed", error=error)
        elif journal is not None and (journal.problem or journal.dropped):
            message = t("runtime.degraded", error=journal.problem or "—", count=journal.dropped)
        status.update(message)
        status.display = bool(message)

    @on(Button.Pressed, "#runtime-retention")
    def retention_pressed(self) -> None:
        self.action_retention()

    @on(Button.Pressed, "#runtime-clean")
    def clean_pressed(self) -> None:
        self.action_clean()
