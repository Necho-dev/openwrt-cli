"""PassWall2 TUI pane: nested tabs, row builders, detail text."""

from __future__ import annotations

import re

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, RichLog, Select, Static, TabbedContent, TabPane, TextArea

from openwrt_cli.core.device import DeviceClient
from openwrt_cli.i18n import t
from openwrt_cli.services.passwall2 import format_uci_value, parse_geo_rules, summarize_list, uci_label
from openwrt_cli.tui.keyhint import highlight_keys

_MULTILINE_OPTS = frozenset({"domain_list", "ip_list", "sources"})

PW2_SUBS = (
    "pw2-nodes",
    "pw2-subscribe",
    "pw2-settings-tab",
    "pw2-rules",
    "pw2-acl",
    "pw2-logs",
)

PW2_CSS = """
    #pw2-tabs { height: 1fr; }
    #pw2-node-split, #pw2-sub-split, #pw2-acl-split, #pw2-rule-split { height: 1fr; }
    #pw2-rules-body { height: 1fr; }
    #pw2-node-table, #pw2-sub-table, #pw2-acl-table, #pw2-rule-table { width: 3fr; height: 1fr; }
    #pw2-node-detail, #pw2-sub-detail, #pw2-acl-detail, #pw2-rule-detail {
        width: 2fr;
        height: 1fr;
        padding: 1 2;
        overflow-x: auto;
        overflow-y: auto;
        text-wrap: nowrap;
        border-left: solid #2a4660;
    }
    #pw2-rule-detail { text-wrap: wrap; }
    #pw2-rules-geo {
        height: auto;
        max-height: 16;
        padding: 1 2;
        overflow-x: hidden;
        overflow-y: auto;
        border-top: solid #2a4660;
    }
    #pw2-settings { height: 1fr; padding: 1 2; overflow-x: auto; overflow-y: auto; text-wrap: nowrap; }
    #pw2-log { height: 1fr; padding: 0 1; }
"""


def compose_passwall2() -> ComposeResult:
    with TabbedContent(id="pw2-tabs"):
        with TabPane(t("pw2.sub.nodes"), id="pw2-nodes"):
            with Horizontal(id="pw2-node-split"):
                yield DataTable(id="pw2-node-table")
                yield Static(t("pw2.pick_node"), id="pw2-node-detail")
        with TabPane(t("pw2.sub.subscribe"), id="pw2-subscribe"):
            with Horizontal(id="pw2-sub-split"):
                yield DataTable(id="pw2-sub-table")
                yield Static(t("pw2.pick_sub"), id="pw2-sub-detail")
        with TabPane(t("pw2.sub.settings"), id="pw2-settings-tab"):
            yield Static("", id="pw2-settings")
        with TabPane(t("pw2.sub.rules"), id="pw2-rules"):
            with Vertical(id="pw2-rules-body"):
                with Horizontal(id="pw2-rule-split"):
                    yield DataTable(id="pw2-rule-table")
                    yield Static(t("pw2.pick_rule"), id="pw2-rule-detail")
                yield Static("", id="pw2-rules-geo")
        with TabPane(t("pw2.sub.acl"), id="pw2-acl"):
            with Horizontal(id="pw2-acl-split"):
                yield DataTable(id="pw2-acl-table")
                yield Static(t("pw2.pick_acl"), id="pw2-acl-detail")
        with TabPane(t("pw2.sub.logs"), id="pw2-logs"):
            yield RichLog(id="pw2-log", highlight=False, markup=False, wrap=True, max_lines=400)


def setup_passwall2_tables(app) -> None:
    for tid, cols in (
        ("pw2-node-table", (
            t("col.section"), t("col.remarks"), t("col.group"), t("col.type"),
            t("col.protocol"), t("col.address"), t("col.port"),
            t("col.ping"), t("col.tcping"),
        )),
        ("pw2-sub-table", (t("col.section"), t("col.remarks"), t("col.nodes"), t("col.url"))),
        ("pw2-rule-table", (
            t("col.section"), t("col.remarks"), t("col.network"),
            t("col.domains"), t("col.ips"),
        )),
        ("pw2-acl-table", (t("col.section"), t("col.enabled"), t("col.remarks"), t("col.sources"), t("col.node"))),
    ):
        table = app.query_one(f"#{tid}", DataTable)
        table.add_columns(*cols)
        table.cursor_type = "row"
        table.zebra_stripes = True


_OK = "#67c23a"
_WARN = "#f0a000"
_BAD = "#ed5c5c"
_ACCENT = "#7ec8ff"
_MUTED = "dim #8aa4b8"
_LAT_MS = re.compile(r"([\d.]+)")


def _dash(value) -> str:
    return "—" if value in (None, "") else str(value)


def _id_cell(value) -> Text:
    return Text(_dash(value), style=_MUTED)


def _empty_cell(value) -> Text | str:
    text = _dash(value)
    return Text(text, style="dim") if text == "—" else text


def _yes_no(ok: bool) -> Text:
    return Text(t("label.yes"), style=_OK) if ok else Text(t("label.no"), style="dim")


def _count_cell(value) -> Text:
    if value in (None, ""):
        return Text("—", style="dim")
    text = str(value)
    return Text(text, style="dim") if text == "0" else Text(text, style=_ACCENT)


def _latency_cell(value) -> Text:
    text = _dash(value)
    if text == "—":
        return Text(text, style="dim")
    if text.lower() in {"timeout", "fail", "error"}:
        return Text(text, style=_BAD)
    match = _LAT_MS.search(text)
    if not match:
        return Text(text)
    try:
        ms = float(match.group(1))
    except ValueError:
        return Text(text)
    if ms < 150:
        style = _OK
    elif ms < 400:
        style = _WARN
    else:
        style = _BAD
    return Text(text, style=style)


def _type_cell(value) -> Text | str:
    text = _dash(value)
    if text == "—":
        return Text(text, style="dim")
    low = text.lower().replace("_", "-")
    if low in {"xray", "v2ray"}:
        return Text(text, style=_ACCENT)
    if "sing-box" in low or low == "singbox":
        return Text(text, style="#9fe1b0")
    if "hysteria" in low:
        return Text(text, style=_WARN)
    return text


def _protocol_cell(value) -> Text | str:
    text = _dash(value)
    if text == "—":
        return Text(text, style="dim")
    if text.startswith("_"):
        return Text(text, style="dim #9ecbff")
    return text


def _sources_cell(sources) -> Text:
    text = summarize_list(sources)
    if text == "—":
        return Text(text, style="dim")
    extra = text.rfind(" +")
    if extra > 0 and text[extra + 2 :].isdigit():
        out = Text()
        out.append(text[:extra])
        out.append(text[extra:], style="dim")
        return out
    return Text(text)


def _acl_node_cell(item: dict) -> Text | str:
    text = _dash(item.get("node_remarks") or item.get("node"))
    if not item.get("node"):
        return Text(text, style="dim")
    return text


def node_row(node: dict) -> tuple:
    return (
        _id_cell(node.get("id")),
        _empty_cell(node.get("remarks")),
        _empty_cell(node.get("group")),
        _type_cell(node.get("type")),
        _protocol_cell(node.get("protocol")),
        _empty_cell(node.get("address")),
        _empty_cell(node.get("port")),
        _latency_cell(node.get("ping")),
        _latency_cell(node.get("tcping")),
    )


def subscribe_row(item: dict) -> tuple:
    url = _dash(item.get("url"))
    return (
        _id_cell(item.get("id")),
        _empty_cell(item.get("remarks")),
        _count_cell(item.get("node_count")),
        Text(url, style="dim") if url != "—" else Text("—", style="dim"),
    )


def shunt_row(item: dict) -> tuple:
    return (
        _id_cell(item.get("id")),
        _empty_cell(item.get("remarks")),
        _empty_cell(item.get("network")),
        _count_cell(item.get("domain_count") if item.get("domain_count") is not None else 0),
        _count_cell(item.get("ip_count") if item.get("ip_count") is not None else 0),
    )


def acl_row(item: dict) -> tuple:
    return (
        _id_cell(item.get("id")),
        _yes_no(bool(item.get("enabled"))),
        _empty_cell(item.get("remarks")),
        _sources_cell(item.get("sources")),
        _acl_node_cell(item),
    )


def _kv_table(items: list[tuple[str, object]]) -> Table:
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 2, 0, 0),
        expand=False,
        pad_edge=False,
        collapse_padding=False,
    )
    table.add_column("k", style="dim", no_wrap=True, min_width=20)
    table.add_column("v", style="bold #f2f6fb", overflow="fold")
    for key, value in items:
        text = format_uci_value(key, value)
        if key not in _MULTILINE_OPTS:
            text = text.replace("\n", " · ")
        table.add_row(uci_label(key), text)
    return table


def _titled(title: str, *blocks) -> Group:
    parts: list = [Text(title, style="bold #7ec8ff"), Text("")]
    parts.extend(blocks)
    return Group(*parts)


def format_node_detail(node: dict | None) -> Group | Text:
    if not node:
        return Group(Text(t("pw2.pick_node")), Text(""), highlight_keys(t("pw2.node_keys")))
    keys = ("id", "group", "type", "protocol", "address", "port", "ping", "tcping")
    extra = [
        (k, v) for k, v in (node.get("options") or {}).items()
        if k not in {"remarks", "group", "type", "protocol", "address", "port", "add_from"}
    ]
    blocks: list = [_kv_table([(k, node.get(k)) for k in keys])]
    if extra:
        blocks.extend((Text(""), _kv_table(extra)))
    blocks.extend((Text(""), highlight_keys(t("pw2.node_keys"))))
    return _titled(str(node.get("remarks") or node.get("id") or ""), *blocks)


def format_subscribe_detail(item: dict | None) -> Group | Text:
    if not item:
        return Text(t("pw2.pick_sub"))
    rows = [("id", item.get("id")), ("nodes", item.get("node_count")), ("url", item.get("url"))]
    extra = [
        (k, v) for k, v in (item.get("options") or {}).items()
        if k not in {"remarks", "remark", "url", "subscribe_url"}
    ]
    return _titled(
        str(item.get("remarks") or item.get("id") or ""),
        _kv_table(rows + extra),
    )


def format_rule_detail(item: dict | None) -> Group | Text:
    if not item:
        return Text(t("pw2.pick_rule"))
    keys = ("id", "network", "domain_list", "ip_list")
    extra = [
        (k, v) for k, v in (item.get("options") or {}).items()
        if k not in {"remarks", "network", "domain_list", "ip_list"}
    ]
    return _titled(
        str(item.get("remarks") or item.get("id") or ""),
        _kv_table([(k, item.get(k)) for k in keys] + extra),
    )


def format_rules_geo(fields: dict | None) -> Group | Text:
    rows = parse_geo_rules(fields or {})
    if not rows:
        return Text("")
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 2, 0, 0),
        expand=True,
        pad_edge=False,
        collapse_padding=False,
    )
    table.add_column("k", style="dim", no_wrap=True, min_width=18, max_width=22)
    table.add_column("v", style="bold #f2f6fb", overflow="fold", ratio=1)
    for key, value in rows:
        table.add_row(uci_label(key), format_uci_value(key, value).replace("\n", " "))
    return Group(
        Text(uci_label("global_rules", section=True), style="bold #7ec8ff"),
        table,
    )


def format_acl_detail(item: dict | None) -> Group | Text:
    if not item:
        return Group(Text(t("pw2.pick_acl")), Text(""), highlight_keys(t("pw2.acl_keys")))
    keys = [
        "id", "enabled", "interface", "sources", "node", "node_remarks",
        "tcp_no_redir_ports", "udp_no_redir_ports", "tcp_redir_ports", "udp_redir_ports",
        "direct_dns_query_strategy", "remote_dns_protocol",
    ]
    proto = str(item.get("remote_dns_protocol") or "")
    mode = str(item.get("dns_mode") or "")
    if mode and mode != proto:
        keys.append("dns_mode")
    keys.extend((
        "remote_dns", "remote_dns_detour", "remote_fakedns",
        "remote_dns_query_strategy", "dns_redirect",
        "log", "loglevel", "log_file",
    ))
    return _titled(
        str(item.get("remarks") or item.get("id") or ""),
        _acl_kv_table([(k, item.get(k)) for k in keys]),
        Text(""),
        highlight_keys(t("pw2.acl_keys")),
    )


def _acl_kv_table(items: list[tuple[str, object]]) -> Table:
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 2, 0, 0),
        expand=False,
        pad_edge=False,
        collapse_padding=False,
    )
    table.add_column("k", style="dim", no_wrap=True, min_width=14, max_width=16)
    table.add_column("v", style="bold #f2f6fb", overflow="fold")
    for key, value in items:
        text = format_uci_value(key, value)
        if key not in _MULTILINE_OPTS:
            text = text.replace("\n", " · ")
        table.add_row(_acl_form_label(key), text)
    return table


_ACL_LOG_LINE = re.compile(
    r"^(?P<tz>\+\d+\s+)?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|TRACE)\b(?P<rest>.*)$",
    re.I,
)
_ACL_LOG_LEVEL_STYLE = {
    "trace": "dim",
    "debug": "dim #8aa4b8",
    "info": "#7ec8ff",
    "warn": "bold #f0a000",
    "warning": "bold #f0a000",
    "error": "bold #ed5c5c",
    "fatal": "bold #ed5c5c",
}


def format_acl_log_line(line: str, query: str = "") -> Text:
    text = Text()
    match = _ACL_LOG_LINE.match(line)
    if match:
        if match.group("tz"):
            text.append(match.group("tz"), style="dim")
        text.append(match.group("ts") + " ", style="dim")
        level = match.group("level")
        text.append(level, style=_ACL_LOG_LEVEL_STYLE.get(level.lower(), ""))
        text.append(match.group("rest") or "")
    else:
        text.append(line)
    needle = (query or "").strip()
    if needle:
        text.highlight_words((needle,), style="bold #12283f on #7ec8ff", case_sensitive=False)
    return text


class AclLogModal(ModalScreen[None]):
    """Poll ACL redirect log while open; HTTP has no tail -f."""

    CSS = """
    AclLogModal { align: center middle; }
    #acl-log-box {
        width: 90%;
        height: 80%;
        background: #12283f;
        border: solid #4c8dff;
        padding: 0 1;
    }
    #acl-log-head { height: 1; }
    #acl-log-title { width: auto; max-width: 36; color: #7ec8ff; text-style: bold; padding-right: 1; }
    #acl-log-filter-input {
        width: 1fr;
        height: 1;
        min-height: 1;
        border: none;
        padding: 0 1;
        background: #0d1f33;
    }
    #acl-log-count { width: 8; height: 1; color: #7ec8ff; content-align: right middle; }
    #acl-log-keys { width: auto; padding-left: 1; }
    #acl-log-view { height: 1fr; padding: 0; }
    """
    BINDINGS = [
        Binding("escape", "close", t("action.cancel"), show=False, priority=True),
    ]
    _TAIL = 400

    def __init__(self, device: DeviceClient, acl_id: str, remarks: str, log_file: str) -> None:
        super().__init__()
        self.device = device
        self.acl_id = acl_id
        self.remarks = remarks
        self.log_file = log_file
        self._last_sig = ""
        self._enabled = True
        self._lines: list[str] = []
        self._message = ""
        self._filter = ""
        self._loaded = False

    def compose(self) -> ComposeResult:
        with Vertical(id="acl-log-box"):
            with Horizontal(id="acl-log-head"):
                yield Static(self.remarks or self.acl_id, id="acl-log-title")
                yield Input(placeholder=t("pw2.acl_log_filter"), id="acl-log-filter-input")
                yield Static("", id="acl-log-count")
                yield Static(highlight_keys(t("pw2.acl_log_hint")), id="acl-log-keys")
            yield RichLog(id="acl-log-view", highlight=False, markup=False, wrap=True, max_lines=800)

    def on_mount(self) -> None:
        self.query_one("#acl-log-filter-input", Input).focus()
        self._refresh()
        self.set_interval(3.0, self._refresh)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "acl-log-filter-input":
            return
        self._filter = (event.value or "").strip()
        self._paint_view()

    def _post(self, enabled: bool, lines: list[str], message: str) -> None:
        if not self.is_attached:
            return
        self.app.call_from_thread(self._paint, enabled, lines, message)

    def _post_error(self, message: str) -> None:
        if not self.is_attached:
            return
        self.app.call_from_thread(self._on_error, message)

    @work(thread=True, exclusive=True, group="acl-log")
    def _refresh(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            result = PassWall2Service(self.device).acl_log(self.acl_id, tail=self._TAIL, verify=False)
            from openwrt_cli.services.passwall2 import log_messages

            data = result.data or {}
            lines = log_messages(data.get("entries") or data.get("lines"))
            message = result.message or ""
            enabled = bool(data.get("enabled"))
        except Exception as e:
            self._post_error(str(e))
            return
        self._post(enabled, lines, message)

    def _paint(self, enabled: bool, lines: list[str], message: str) -> None:
        if not self.is_attached:
            return
        sig = f"{int(enabled)}\0{message}\0" + "\n".join(lines)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        self._enabled = enabled
        self._lines = lines
        self._message = message
        self._loaded = True
        self._paint_view()

    def _on_error(self, message: str) -> None:
        if not self.is_attached:
            return
        if self._loaded:
            return
        self._enabled = False
        self._message = message
        self._paint_view()

    def _paint_view(self) -> None:
        if not self.is_attached:
            return
        view = self.query_one("#acl-log-view", RichLog)
        view.clear()
        if not self._enabled:
            view.write(Text(self._message or t("pw2.acl_log_off"), style="bold #f0a000"))
            self.query_one("#acl-log-count", Static).update("")
            return
        query = self._filter
        needle = query.lower()
        shown = 0
        for line in self._lines:
            if needle and needle not in line.lower():
                continue
            view.write(format_acl_log_line(line, query))
            shown += 1
        if shown == 0:
            view.write(Text(t("empty.pw2_logs"), style="dim"))
        self.query_one("#acl-log-count", Static).update(f"{shown}/{len(self._lines)}")

    def action_close(self) -> None:
        if self._filter:
            inp = self.query_one("#acl-log-filter-input", Input)
            inp.value = ""
            self._filter = ""
            self._paint_view()
            return
        self.dismiss(None)


def format_settings(data: dict | None) -> Group | Text:
    if not data:
        return Text("")
    parts: list = []
    for block in (
        "global_delay", "global_forwarding", "global_other",
        "global_haproxy", "global_xray", "global_singbox",
    ):
        fields = data.get(block) or {}
        if not fields:
            continue
        if parts:
            parts.append(Text(""))
        parts.append(Text(uci_label(block, section=True), style="bold #7ec8ff"))
        parts.append(_kv_table(list(fields.items())))
    extra = data.get("extra") or {}
    if extra:
        if parts:
            parts.append(Text(""))
        parts.append(Text(uci_label("extra", section=True), style="bold #7ec8ff"))
        for name, sec in extra.items():
            title = uci_label(str(sec.get("type") or name), section=True) if isinstance(sec, dict) else str(name)
            parts.append(Text(f"{title}  {name}", style="bold #9ecbff"))
            if not isinstance(sec, dict):
                continue
            items = [(k, v) for k, v in sec.items() if k != "type"]
            if items:
                parts.append(_kv_table(items))
    components = data.get("components") or []
    if components:
        if parts:
            parts.append(Text(""))
        parts.append(Text(uci_label("components", section=True), style="bold #7ec8ff"))
        table = Table(box=None, padding=(0, 2, 0, 0), expand=True, pad_edge=False)
        table.add_column(t("col.component"), style="bold #f2f6fb", no_wrap=True, min_width=12)
        table.add_column(t("col.path"), overflow="fold", ratio=1)
        table.add_column(t("col.local"), no_wrap=True)
        for item in components:
            table.add_row(
                item.get("title") or item.get("name") or "—",
                item.get("path") or "—",
                item.get("version") or "—",
            )
        parts.append(table)
    return Group(*parts) if parts else Text("")


_FORM_FIELDS = (
    ("remarks", "text"),
    ("group", "text"),
    ("type", "select"),
    ("protocol", "select"),
    ("address", "text"),
    ("port", "text"),
    ("username", "text"),
    ("password", "secret"),
)
_FORM_KEYS = tuple(key for key, _ in _FORM_FIELDS)
_PROTOCOL_LABELS = {
    "vmess": "VMess",
    "vless": "VLESS",
    "trojan": "Trojan",
    "shadowsocks": "Shadowsocks",
    "socks": "Socks",
    "http": "HTTP",
    "wireguard": "WireGuard",
    "hysteria": "Hysteria",
    "hysteria2": "Hysteria2",
    "tuic": "TUIC",
    "ssh": "SSH",
    "anytls": "AnyTLS",
    "_shunt": "_shunt",
    "_balancing": "_balancing",
    "_urltest": "_urltest",
    "_iface": "_iface",
}


class NodeFormModal(ModalScreen[bool]):
    """Add or edit a PassWall2 node via UCI fields / share URL."""

    CSS = """
    NodeFormModal { align: center middle; }
    #pw2-nf-box {
        width: 88;
        max-width: 94%;
        height: auto;
        max-height: 90%;
        background: #12283f;
        border: solid #4c8dff;
        padding: 1 2;
        overflow: hidden;
    }
    #pw2-nf-title { height: 2; color: #7ec8ff; text-style: bold; content-align: left middle; }
    #pw2-nf-fields { height: auto; overflow-y: auto; }
    .pw2-nf-row { height: 2; margin: 0; }
    .pw2-nf-label { width: 11; height: 2; color: #8aa4b8; content-align: left middle; }
    #pw2-nf-box Input, #pw2-nf-box Select, #pw2-nf-box SelectCurrent {
        width: 1fr;
        height: 1;
        min-height: 1;
        border: none;
        padding: 0 1;
        color: #f2f6fb;
        background: #16324d;
    }
    #pw2-nf-box SelectOverlay {
        background: #16324d;
        border: solid #4c8dff;
        color: #f2f6fb;
    }
    #pw2-nf-error { height: 1; color: #ed5c5c; display: none; }
    #pw2-nf-foot { height: auto; margin-top: 1; }
    #pw2-nf-hint { height: 1; text-wrap: nowrap; }
    #pw2-nf-btns { height: 3; width: 1fr; align: right middle; }
    #pw2-nf-btns Button {
        width: 14;
        min-width: 14;
        margin-left: 1;
        content-align: center middle;
        text-align: center;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", t("action.cancel"), show=False, priority=True),
        Binding("ctrl+s", "save", t("action.confirm"), show=False, priority=True),
        Binding("ctrl+r", "reveal", show=False, priority=True),
    ]

    def __init__(self, device: DeviceClient, node: dict | None = None) -> None:
        super().__init__()
        self.device = device
        self.node = node or {}
        self._reveal = False

    def compose(self) -> ComposeResult:
        title = t("pw2.node.edit") if self.node.get("id") else t("pw2.node.add")
        if self.node.get("id"):
            title = f"{title}  {self.node.get('remarks') or ''}  {self.node.get('id')}"
        with Vertical(id="pw2-nf-box"):
            yield Static(title, id="pw2-nf-title")
            with Vertical(id="pw2-nf-fields"):
                if not self.node.get("id"):
                    yield from self._row("url", "text", placeholder=t("pw2.node.url"))
                for key, kind in _FORM_FIELDS:
                    yield from self._row(key, kind, value=_form_value(self.node, key))
            yield Static("", id="pw2-nf-error")
            with Vertical(id="pw2-nf-foot"):
                yield Static(highlight_keys(t("pw2.node.save")), id="pw2-nf-hint")
                with Horizontal(id="pw2-nf-btns"):
                    yield Button(t("action.cancel").upper(), id="cancel")
                    yield Button(t("action.confirm").upper(), id="ok", variant="success")

    def _row(
        self,
        key: str,
        kind: str,
        *,
        value: str = "",
        placeholder: str = "",
    ) -> ComposeResult:
        with Horizontal(classes="pw2-nf-row"):
            yield Static(_form_label(key), classes="pw2-nf-label")
            if kind == "select":
                yield Select(
                    _select_options(key, value, self._draft_type()),
                    value=_select_value(key, value),
                    allow_blank=_select_allow_blank(key),
                    prompt=_select_prompt(key),
                    compact=True,
                    id=f"pw2-nf-{key}",
                )
            else:
                yield Input(
                    value=value,
                    placeholder=placeholder,
                    password=kind == "secret",
                    id=f"pw2-nf-{key}",
                )

    def _draft_type(self) -> str:
        return _form_value(self.node, "type") or "sing-box"

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "pw2-nf-type":
            return
        try:
            proto = self.query_one("#pw2-nf-protocol", Select)
        except Exception:
            return
        current = "" if proto.value is Select.NULL else str(proto.value or "")
        if not current:
            current = _form_value(self.node, "protocol")
        type_name = "" if event.value is Select.NULL else str(event.value or "")
        options = _select_options("protocol", current, type_name)
        proto.set_options(options)
        allowed = {value for _, value in options}
        proto.value = current if current in allowed else Select.NULL

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            await self.action_save()
        else:
            self.dismiss(False)

    def _show_error(self, message: str) -> None:
        err = self.query_one("#pw2-nf-error", Static)
        err.update(message)
        err.display = True

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_reveal(self) -> None:
        self._reveal = not self._reveal
        self.query_one("#pw2-nf-password", Input).password = not self._reveal

    async def action_save(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service
        from openwrt_cli.services.pw2_share_url import ShareURLError, parse_share_url
        from openwrt_cli.tui.app import ConfirmModal

        fields = self._collect()
        url = fields.pop("_url", "")
        if url:
            try:
                fields = {**parse_share_url(url), **{k: v for k, v in fields.items() if v}}
            except ShareURLError as e:
                self._show_error(str(e))
                return
        node_id = str(self.node.get("id") or "")
        confirm = t("confirm.pw2_node_set", id=node_id) if node_id else t("confirm.pw2_node_add")
        if not await self.app.push_screen_wait(ConfirmModal(confirm)):
            return
        svc = PassWall2Service(self.device)
        result = svc.node_set(node_id, fields) if node_id else svc.node_add(fields)
        if not result.ok:
            self._show_error(result.message or t("err.pw2_node_invalid", error=""))
            return
        self.dismiss(True)

    def _collect(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if not self.node.get("id"):
            out["_url"] = self.query_one("#pw2-nf-url", Input).value.strip()
        kinds = {key: kind for key, kind in _FORM_FIELDS}
        for key in _FORM_KEYS:
            out[key] = _widget_value(self.query_one(f"#pw2-nf-{key}"), kinds[key])
        return {k: v for k, v in out.items() if k == "_url" or v}


def _form_label(key: str) -> str:
    i18n_key = f"pw2.node.lbl.{key}"
    text = t(i18n_key)
    return text if text != i18n_key else uci_label(key)


def _form_value(node: dict, key: str) -> str:
    opts = node.get("options") or {}
    for candidate in (opts.get(key), node.get(key)):
        text = _dash_form(candidate)
        if text:
            return text
    suffix = f"_{key}"
    for name, value in opts.items():
        if str(name).endswith(suffix):
            text = _dash_form(value)
            if text:
                return text
    return ""


def _select_allow_blank(key: str) -> bool:
    return key == "protocol"


def _select_prompt(key: str) -> str:
    return "—"


def _select_value(key: str, value: str):
    if key == "type" and not value:
        return "sing-box"
    if not value:
        return Select.NULL
    return value


def _select_options(key: str, current: str, type_name: str) -> list[tuple[str, str]]:
    from openwrt_cli.services.pw2_node_schema import NODE_TYPES, PROTOCOLS_BY_TYPE

    if key == "type":
        pairs = [(name, name) for name in NODE_TYPES]
    elif key == "protocol":
        pairs = [
            (_PROTOCOL_LABELS.get(name, name), name)
            for name in PROTOCOLS_BY_TYPE.get(type_name, ())
        ]
    else:
        pairs = []
    values = {item for _, item in pairs}
    if current and current not in values:
        pairs = [*pairs, (_PROTOCOL_LABELS.get(current, current), current)]
    return pairs


def _widget_value(widget, kind: str) -> str:
    if kind == "select":
        if widget.value is Select.NULL:
            return ""
        return str(widget.value or "").strip()
    return str(widget.value or "").strip()


def _dash_form(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return " ".join(str(x) for x in value if x not in (None, ""))
    return str(value)


_ACL_FORM_FIELDS = (
    ("enabled", "switch"),
    ("remarks", "text"),
    ("log", "switch"),
    ("loglevel", "select"),
    ("interface", "text"),
    ("sources", "list"),
    ("node", "node"),
    ("tcp_no_redir_ports", "no_redir"),
    ("udp_no_redir_ports", "no_redir"),
    ("tcp_redir_ports", "redir"),
    ("udp_redir_ports", "redir"),
    ("direct_dns_query_strategy", "select"),
    ("remote_dns_protocol", "select"),
    ("remote_dns", "text"),
    ("remote_dns_detour", "select"),
    ("remote_fakedns", "switch"),
    ("remote_dns_query_strategy", "select"),
    ("dns_redirect", "tri"),
)
_ACL_FORM_KEYS = tuple(key for key, _ in _ACL_FORM_FIELDS)
_ACL_RAW_PORTS = frozenset({
    "tcp_no_redir_ports", "udp_no_redir_ports", "tcp_redir_ports", "udp_redir_ports",
})


class AclFormModal(ModalScreen[bool]):
    """Add or edit a PassWall2 acl_rule via raw UCI options."""

    CSS = """
    AclFormModal { align: center middle; }
    #pw2-af-box {
        width: 92;
        max-width: 96%;
        height: 90%;
        max-height: 90%;
        background: #12283f;
        border: solid #4c8dff;
        padding: 1 2;
        overflow: hidden;
    }
    #pw2-af-title { height: 2; color: #7ec8ff; text-style: bold; content-align: left middle; }
    #pw2-af-fields { height: 1fr; overflow-y: auto; }
    .pw2-af-row { height: 2; margin: 0; }
    .pw2-af-row-list { height: 6; margin: 0; }
    .pw2-af-label { width: 16; height: 2; color: #8aa4b8; content-align: left middle; }
    .pw2-af-row-list .pw2-af-label { height: 6; content-align: left top; padding-top: 1; }
    #pw2-af-box Input, #pw2-af-box Select, #pw2-af-box SelectCurrent, #pw2-af-box TextArea {
        width: 1fr;
        height: 1;
        min-height: 1;
        border: none;
        padding: 0 1;
        color: #f2f6fb;
        background: #16324d;
    }
    #pw2-af-box TextArea {
        height: 5;
        min-height: 5;
    }
    #pw2-af-box SelectOverlay {
        background: #16324d;
        border: solid #4c8dff;
        color: #f2f6fb;
    }
    #pw2-af-error { height: 1; color: #ed5c5c; display: none; }
    #pw2-af-foot { height: 5; margin-top: 1; }
    #pw2-af-hint { height: 1; text-wrap: nowrap; }
    #pw2-af-btns { height: 3; width: 1fr; align: right middle; }
    #pw2-af-btns Button {
        width: 14;
        min-width: 14;
        margin-left: 1;
        content-align: center middle;
        text-align: center;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", t("action.cancel"), show=False, priority=True),
        Binding("ctrl+s", "save", t("action.confirm"), show=False, priority=True),
    ]

    def __init__(self, device: DeviceClient, acl: dict | None = None, nodes: list[dict] | None = None) -> None:
        super().__init__()
        self.device = device
        self.acl = acl or {}
        self.nodes = nodes or []
        self._original = _acl_originals(self.acl)
        self._dirty: set[str] = set()

    def compose(self) -> ComposeResult:
        title = t("pw2.acl.edit") if self.acl.get("id") else t("pw2.acl.add")
        if self.acl.get("id"):
            title = f"{title}  {self.acl.get('remarks') or ''}  {self.acl.get('id')}"
        with Vertical(id="pw2-af-box"):
            yield Static(title, id="pw2-af-title")
            with Vertical(id="pw2-af-fields"):
                for key, kind in _ACL_FORM_FIELDS:
                    yield from self._row(key, kind, value=_acl_raw(self.acl, key))
            yield Static("", id="pw2-af-error")
            with Vertical(id="pw2-af-foot"):
                yield Static(highlight_keys(t("pw2.acl.save")), id="pw2-af-hint")
                with Horizontal(id="pw2-af-btns"):
                    yield Button(t("action.cancel").upper(), id="cancel")
                    yield Button(t("action.confirm").upper(), id="ok", variant="success")

    def _row(self, key: str, kind: str, *, value: str = "") -> ComposeResult:
        classes = "pw2-af-row-list" if kind == "list" else "pw2-af-row"
        with Horizontal(classes=classes):
            yield Static(_acl_form_label(key), classes="pw2-af-label")
            if kind == "list":
                yield TextArea(value, id=f"pw2-af-{key}", show_line_numbers=False, soft_wrap=False)
            elif kind in {"switch", "select", "node", "no_redir", "redir", "tri"}:
                yield Select(
                    _acl_select_options(kind, key, value, self.nodes),
                    value=_acl_select_value(kind, key, value),
                    allow_blank=_acl_allow_blank(kind),
                    prompt=_acl_select_prompt(kind),
                    compact=True,
                    id=f"pw2-af-{key}",
                )
            else:
                yield Input(value=value, placeholder=_acl_placeholder(key), id=f"pw2-af-{key}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            await self.action_save()
        else:
            self.dismiss(False)

    def _show_error(self, message: str) -> None:
        err = self.query_one("#pw2-af-error", Static)
        err.update(message)
        err.display = True

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_select_changed(self, event: Select.Changed) -> None:
        key = _acl_field_key(event.select.id)
        if not key:
            return
        value = _widget_value(event.select, "select")
        from openwrt_cli.services.pw2_acl_schema import values_equal

        if values_equal(self._original.get(key), value):
            self._dirty.discard(key)
        else:
            self._dirty.add(key)

    async def action_save(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service
        from openwrt_cli.tui.app import ConfirmModal

        fields = self._collect_patch()
        if self.acl.get("id") and not fields:
            self._show_error(t("err.pw2_acl_no_change"))
            return
        acl_id = str(self.acl.get("id") or "")
        confirm = t("confirm.pw2_acl_set", id=acl_id) if acl_id else t("confirm.pw2_acl_add")
        if not await self.app.push_screen_wait(ConfirmModal(confirm)):
            return
        svc = PassWall2Service(self.device)
        result = svc.acl_set(acl_id, fields) if acl_id else svc.acl_add(fields)
        if not result.ok:
            self._show_error(result.message or t("err.pw2_acl_invalid", error=""))
            return
        self.dismiss(True)

    def _collect(self) -> dict[str, object]:
        from openwrt_cli.services.pw2_acl_schema import parse_sources

        out: dict[str, object] = {}
        kinds = {key: kind for key, kind in _ACL_FORM_FIELDS}
        for key in _ACL_FORM_KEYS:
            widget = self.query_one(f"#pw2-af-{key}")
            kind = kinds[key]
            if kind == "list":
                out[key] = parse_sources(getattr(widget, "text", "") or "")
            elif kind in {"switch", "select", "node", "no_redir", "redir", "tri"}:
                out[key] = _widget_value(widget, "select")
            else:
                out[key] = _widget_value(widget, "text")
        proto = str(out.get("remote_dns_protocol") or "")
        opts = self.acl.get("options") or {}
        if proto and "remote_dns_protocol" not in opts and "dns_mode" in opts:
            out["dns_mode"] = proto
            out.pop("remote_dns_protocol", None)
        return out

    def _collect_patch(self) -> dict[str, object]:
        from openwrt_cli.services.pw2_acl_schema import changed_fields, values_equal

        collected = self._collect()
        if not self.acl.get("id"):
            return {key: value for key, value in collected.items() if value not in ("", [])}
        kinds = {key: kind for key, kind in _ACL_FORM_FIELDS}
        patch: dict[str, object] = {}
        for key, value in changed_fields(self._original, collected).items():
            kind = kinds.get(key, "text")
            if kind in {"switch", "select", "node", "no_redir", "redir", "tri"} and key not in self._dirty:
                continue
            if key == "dns_mode" and values_equal(self._original.get("remote_dns_protocol"), value):
                continue
            patch[key] = value
        return patch


def _acl_form_label(key: str) -> str:
    i18n_key = f"pw2.acl.lbl.{key}"
    text = t(i18n_key)
    return text if text != i18n_key else uci_label(key)


def _acl_field_key(widget_id: str | None) -> str:
    if not widget_id or not widget_id.startswith("pw2-af-"):
        return ""
    return widget_id[7:]


def _acl_originals(acl: dict) -> dict[str, object]:
    from openwrt_cli.services.pw2_acl_schema import parse_sources

    out: dict[str, object] = {}
    for key, kind in _ACL_FORM_FIELDS:
        raw = _acl_raw(acl, key)
        if kind == "list":
            out[key] = parse_sources(raw)
        elif kind == "switch" and key == "enabled" and not raw:
            out[key] = "1"
        else:
            out[key] = raw
    return out


def _acl_raw(acl: dict, key: str) -> str:
    from openwrt_cli.services.pw2_acl_schema import parse_sources

    opts = acl.get("options") or {}
    if key == "remote_dns_protocol":
        raw = opts.get("remote_dns_protocol")
        if raw in (None, ""):
            raw = opts.get("dns_mode")
        return _dash_form(raw)
    if key == "sources":
        items = parse_sources(opts.get("sources") if "sources" in opts else acl.get("sources"))
        return "\n".join(items)
    if key in _ACL_RAW_PORTS or key in {"node", "interface", "enabled", "log", "remarks"}:
        return _dash_form(opts.get(key))
    if key in opts:
        return _dash_form(opts[key])
    return ""


def _acl_placeholder(key: str) -> str:
    if key == "interface":
        return t("pw2.choice.all_ifaces")
    if key == "sources":
        return t("pw2.acl.sources_hint")
    return ""


def _acl_allow_blank(kind: str) -> bool:
    return kind in {"select", "node", "no_redir", "redir", "tri"}


def _acl_select_prompt(kind: str) -> str:
    if kind in {"node", "no_redir", "redir", "tri", "select"}:
        return t("pw2.choice.use_global")
    return "—"


def _acl_select_value(kind: str, key: str, value: str):
    if kind == "switch":
        if key == "enabled" and not value:
            return "1"
        return "1" if str(value).lower() in {"1", "true", "yes", "on"} else "0"
    if not value:
        return Select.NULL
    return value


def _acl_select_options(kind: str, key: str, current: str, nodes: list[dict]) -> list[tuple[str, str]]:
    from openwrt_cli.services.pw2_acl_schema import DNS_DETOUR, DNS_PROTOCOLS, LOGLEVELS, QUERY_STRATEGIES

    if kind == "switch":
        pairs = [(t("label.yes"), "1"), (t("label.no"), "0")]
    elif kind == "tri":
        pairs = [(t("label.yes"), "1"), (t("label.no"), "0")]
    elif kind == "node":
        pairs = [
            (f"{item.get('remarks') or item.get('id')}  {item.get('id')}", str(item.get("id")))
            for item in nodes if item.get("id")
        ]
    elif kind == "no_redir":
        pairs = [
            (t("pw2.choice.no_use"), "disable"),
            (t("pw2.choice.all_ports"), "1:65535"),
        ]
    elif kind == "redir":
        pairs = [(t("pw2.choice.all_ports"), "1:65535")]
    elif key == "loglevel":
        pairs = [(name, name) for name in LOGLEVELS]
    elif key in {"direct_dns_query_strategy", "remote_dns_query_strategy"}:
        pairs = [(name, name) for name in QUERY_STRATEGIES]
    elif key == "remote_dns_protocol":
        pairs = [(name.upper() if name != "doh" else "DoH", name) for name in DNS_PROTOCOLS]
    elif key == "remote_dns_detour":
        pairs = [(name, name) for name in DNS_DETOUR]
    else:
        pairs = []
    values = {item for _, item in pairs}
    if current and current not in values:
        pairs = [*pairs, (current, current)]
    return pairs

