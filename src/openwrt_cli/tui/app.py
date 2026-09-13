from __future__ import annotations

import re
import time
import unicodedata
from collections import deque

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Link, RichLog, Static, TabbedContent, TabPane
from textual.widgets._footer import FooterKey

from openwrt_cli.core.device import DeviceClient
from openwrt_cli.services.monitor import MonitorService
from openwrt_cli.services.bandix import BandixService, downsample_metrics, norm_mac
from openwrt_cli.services.network import NetworkService, _fmt_bytes, _fmt_rate, is_physical_device
from openwrt_cli.services.service import ServiceService
from openwrt_cli.services.system import SystemService, format_log_line
from openwrt_cli.tui.bandwidth import BandwidthPanel, DualRateChart
from openwrt_cli.tui.charts import WINDOW, AreaChart, Series, fmt_bytes_rate, render_legend
from openwrt_cli.tui.load import LoadPanel
from openwrt_cli.tui.keyhint import highlight_keys
from openwrt_cli.tui.optional import probe_optional_apps
from openwrt_cli.tui.screens.passwall2 import (
    PW2_CSS,
    PW2_SUBS,
    AclFormModal,
    AclLogModal,
    NodeFormModal,
    acl_row,
    format_acl_detail,
    format_node_detail,
    format_rule_detail,
    format_rules_geo,
    format_settings,
    format_subscribe_detail,
    node_row,
    setup_passwall2_tables,
    shunt_row,
    subscribe_row,
)
from openwrt_cli.i18n import _, t
from openwrt_cli.ui.logo import render_logo_compact
from openwrt_cli.ui.render import clip_mid, display_iface, display_route_iface
from openwrt_cli.version import GITHUB_ICON, REPO_URL, display_version

# Textual wants CSS/hex colors, not Rich names such as dodger_blue.
# Load palette matches LuCI: 1m coral, 5m apricot, 15m gold.
C_LOAD1, C_LOAD5, C_LOAD15 = "#f2a07a", "#e8b86d", "#d8c56a"
C_RX, C_TX = "#4c8dff", "#67c23a"
# Connections: UDP blue, TCP green, Other rose so stacks stay distinct.
C_UDP, C_TCP, C_OTHER = "#7ea8f8", "#6ecf97", "#f08080"


def _fmt_bytes_total(num_bytes) -> str:
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        return "—"
    return _fmt_bytes(n).replace(" ", "")


def _physical(stats: dict) -> list[dict]:
    out = []
    for i in stats.get("interfaces") or []:
        name = i.get("name") or ""
        if i.get("physical") is True or (i.get("physical") is None and is_physical_device(name)):
            out.append(i)
    out.sort(key=lambda i: (0 if (i.get("name") or "").startswith(("eth", "en")) else 1, i.get("name") or ""))
    return out[:4]


def _sum_bytes(ifaces: list[dict]) -> tuple[int, int]:
    rx = tx = 0
    for i in ifaces:
        rx += int(i.get("rx_bytes") or 0)
        tx += int(i.get("tx_bytes") or 0)
    return rx, tx


def _clip(s: str, n: int = 28) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tag(text: str) -> Text:
    return Text(f" {text} ", style="bold #e8f4ff on #1e4a7a")


def _metric(name: str, value: str | Text, *, tag: str | None = None) -> Text:
    t = Text()
    t.append(name, style="bold #7ec8ff")
    if tag:
        t.append(" ")
        t.append_text(_tag(tag))
    t.append("\n")
    if isinstance(value, Text):
        t.append_text(value)
    else:
        t.append(value or "—", style="bold #f2f6fb")
    return t


def _host_version_value(hostname: str, version: str, kernel: str) -> Text:
    t = Text()
    t.append(hostname or "—", style="bold #f2f6fb")
    if version:
        t.append(" ")
        t.append(version, style="bold #4c8dff")
    if kernel:
        t.append(f"({kernel})", style="bold #9ecbff")
    return t


def _load_value(l1: float, l5: float, l15: float) -> Text:
    t = Text()
    parts = (("1m", l1, C_LOAD1), ("5m", l5, C_LOAD5), ("15m", l15, C_LOAD15))
    for i, (label, val, color) in enumerate(parts):
        if i:
            t.append("    ", style="dim")
        t.append(f"{label} ", style="dim")
        t.append(f"{val:.2f}", style=f"bold {color}")
    return t


def _mem_value(used_pct: float, avail: float, total: float) -> Text:
    if used_pct >= 90:
        pct_style = "bold #ed5c5c"
    elif used_pct >= 75:
        pct_style = "bold #f0a000"
    else:
        pct_style = "bold #67c23a"
    t = Text()
    t.append(f"{used_pct:.0f}%", style=pct_style)
    t.append("   ", style="dim")
    t.append(f"{avail:.0f}/{total:.0f} MB", style="bold #f2f6fb")
    return t


def _plain(cell) -> str:
    return cell.plain if isinstance(cell, Text) else str(cell)


def _status_up(up) -> Text:
    return Text("up", style="#67c23a") if up else Text("down", style="#ed5c5c")


def _yes_no(ok: bool) -> Text:
    return Text(t("label.yes"), style="#67c23a") if ok else Text(t("label.no"), style="dim")


def _svc_rows(services: list[dict]) -> list[tuple]:
    return [
        (
            s.get("name", ""),
            str(s.get("priority") if s.get("priority") is not None else "—"),
            _yes_no(bool(s.get("running"))),
            _yes_no(bool(s.get("enabled"))),
        )
        for s in services
    ]


def _proc_rows(processes: list[dict]) -> list[tuple]:
    rows = []
    for p in processes:
        cpu = float(p.get("cpu_pct") or 0)
        if cpu >= 50:
            cpu_style = "#ed5c5c"
        elif cpu >= 20:
            cpu_style = "#f0a000"
        else:
            cpu_style = "#67c23a"
        cmd = p.get("command") or p.get("comm") or ""
        shown = clip_mid(cmd, 36)
        cmd_cell = Text(shown, justify="center") if len(cmd) > 36 else Text(shown)
        rows.append((
            p.get("name") or "—",
            p.get("pid") or "",
            p.get("user") or "",
            Text(p.get("cpu") or "0.00%", style=cpu_style),
            p.get("mem") or "—",
            cmd_cell,
        ))
    return rows


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


def _pad_label(label: str, width: int = 10) -> str:
    return label + " " * max(1, width - _display_width(label))


def format_proc_detail(data: dict) -> Text:
    out = Text()
    out.append(str(data.get("name") or t("proc.fallback")), style="bold #7ec8ff")
    out.append("\n\n")

    def line(label: str, value, style: str = "bold #f2f6fb") -> None:
        out.append(_pad_label(label), style="dim")
        out.append(str(value if value not in (None, "") else "—"), style=style)
        out.append("\n")

    line("PID", data.get("pid"))
    line("PPID", data.get("ppid"))
    line(t("col.user"), data.get("user"))
    line(t("col.status"), data.get("stat"))
    line("CPU", data.get("cpu"), "#67c23a")
    line(t("col.memory"), data.get("mem"))
    if data.get("vsz"):
        line("VSZ", data.get("vsz"), "dim")
    out.append("\n")
    out.append(t("col.command") + "\n", style="bold #7ec8ff")
    out.append(str(data.get("command") or data.get("comm") or "—"), style="#f2f6fb")
    return out


def _dash(value) -> str:
    return "—" if value in (None, "") else str(value)


def _rule_rows(rules: list[dict]) -> list[tuple]:
    return [
        (
            _dash(r.get("rule")),
            _dash(r.get("priority")),
            _dash(r.get("iif")),
            _dash(r.get("src")),
            _dash(r.get("sport")),
            _dash(r.get("action")),
            _dash(r.get("ipproto")),
            _dash(r.get("oif")),
            _dash(r.get("dest")),
            _dash(r.get("dport")),
            _dash(r.get("table")),
        )
        for r in rules
    ]


def _route_rows(routes: list[dict]) -> list[tuple]:
    return [
        (
            _iface_cell(r.get("dev"), r.get("iface"), r.get("dev"))
            if r.get("iface")
            else Text(display_route_iface(r), style="dim"),
            r.get("dest") or "—",
            r.get("via") or "—",
            r.get("src") or "—",
            str(r.get("metric") if r.get("metric") not in (None, "") else "—"),
            r.get("table") or "—",
            r.get("proto") or "—",
        )
        for r in routes
    ]


_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(\s+)(.*)$")
_LOG_STAMP = re.compile(
    r"^([A-Z][a-z]{2} [A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2} \d{4})(\s+)(.*)$"
)
_LOG_FACILITY = re.compile(
    r"^([a-z]+\.(emerg|alert|crit|err|warning|notice|info|debug))(\s+)(.*)$",
    re.I,
)
_LOG_PROC = re.compile(r"^([^:\s\[]+)(?:\[(\d+)\])?:(\s*)(.*)$")
_LOG_LEAD = re.compile(r"^(emerg|alert|crit|fatal|error|err|warning|warn|notice|info|debug)\s*:\s*", re.I)
_SEV_STYLE = {
    0: "bold #ed5c5c",
    1: "bold #ed5c5c",
    2: "bold #ed5c5c",
    3: "#ed5c5c",
    4: "#f0a000",
    5: "#9ecbff",
    6: "#f2f6fb",
    7: "dim",
}
_FACILITY_SEV = {
    "emerg": 0, "alert": 1, "crit": 2, "fatal": 2, "err": 3, "error": 3,
    "warning": 4, "warn": 4, "notice": 5, "info": 6, "debug": 7,
}


def _log_severity(entry: dict) -> int | None:
    try:
        return int(entry.get("priority")) & 7
    except (TypeError, ValueError):
        return None


def format_log_text(entry: dict) -> Text:
    line = format_log_line(entry)
    out = Text()
    rest = line
    m = _LOG_TS.match(line) or _LOG_STAMP.match(line)
    if m:
        out.append(m.group(1), style="dim #8aa4b8")
        out.append(m.group(2))
        rest = m.group(3)
    sev = _log_severity(entry)
    fac = _LOG_FACILITY.match(rest)
    if fac:
        out.append(fac.group(1), style="dim #9ecbff")
        out.append(fac.group(3))
        rest = fac.group(4)
        if sev is None:
            sev = _FACILITY_SEV.get(fac.group(2).lower())
    proc = _LOG_PROC.match(rest)
    if proc:
        out.append(proc.group(1), style="bold #7ec8ff")
        if proc.group(2):
            out.append("[", style="dim")
            out.append(proc.group(2), style="dim #9ecbff")
            out.append("]", style="dim")
        out.append(":", style="dim")
        out.append(proc.group(3))
        rest = proc.group(4)
    if sev is None:
        lead = _LOG_LEAD.match(rest)
        if lead:
            sev = _FACILITY_SEV.get(lead.group(1).lower())
    out.append(rest, style=_SEV_STYLE.get(sev if sev is not None else 6, "#f2f6fb"))
    return out


def _footer() -> Footer:
    try:
        return Footer(show_command_palette=False)
    except TypeError:
        return Footer()


def _footer_edit_key() -> FooterKey:
    key = FooterKey("e", "e", t("action.edit"), "edit_or_enable")
    key.compact = False
    return key


def _conn_status(device) -> Text:
    scheme = (getattr(device, "scheme", None) or "").lower()
    transport = (getattr(device, "transport", "") or "").lower()
    if scheme in {"https", "http"}:
        proto = scheme.upper()
    elif transport == "ssh":
        proto = "SSH"
    elif transport == "http":
        proto = "HTTP"
    else:
        proto = (transport or "?").upper()
    host = getattr(device, "host", "") or "?"
    user = getattr(device, "user", "") or "root"
    port = getattr(device, "port", None)
    t = Text()
    t.append("● ", style="bold #67c23a")
    t.append(proto, style="bold #7ec8ff")
    t.append(f"  {user}@{host}", style="#f2f6fb")
    if port is not None:
        t.append(f":{port}", style="dim")
    return t


def _pick_wan(stats: dict) -> dict | None:
    for i in stats.get("interfaces") or []:
        label = (i.get("role") or i.get("label") or i.get("name") or "").lower()
        if label == "wan" or label.startswith("wan"):
            return i
    return None


def _first_addr(items, width: int = 22) -> str:
    if isinstance(items, str) and items:
        return clip_mid(items, width)
    values = [str(v) for v in (items or []) if v]
    if not values:
        return "—"
    extra = f" +{len(values) - 1}" if len(values) > 1 else ""
    return clip_mid(values[0] + extra, width)


def _short_refresh_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "timed out" in low or "超时" in msg or "errno 60" in low:
        return t("msg.router_timeout")
    if "无法访问" in msg or "cannot reach" in low or "connection" in low:
        return t("msg.router_down")
    return msg.splitlines()[0][:72]


def _wan_value(stats: dict, rx_rate: float, tx_rate: float) -> Text:
    wan = _pick_wan(stats) or {}
    down = fmt_bytes_rate(wan.get("rx_bps") if wan.get("rx_bps") is not None else rx_rate)
    up = fmt_bytes_rate(wan.get("tx_bps") if wan.get("tx_bps") is not None else tx_rate)
    rx_tot = _fmt_bytes_total(wan.get("rx_bytes"))
    if rx_tot == "—":
        rx_tot = (wan.get("rx_human") or "—").replace(" ", "")
    tx_tot = _fmt_bytes_total(wan.get("tx_bytes"))
    if tx_tot == "—":
        tx_tot = (wan.get("tx_human") or "—").replace(" ", "")
    t = Text()
    t.append("↓", style="bold #4c8dff")
    t.append(down, style="bold #7ec8ff")
    t.append(" ", style="dim")
    t.append("↑", style="bold #67c23a")
    t.append(up, style="bold #9fe1b0")
    t.append(" | ", style="dim")
    t.append("↓", style="bold #4c8dff")
    t.append(rx_tot, style="bold #7ec8ff")
    t.append("/", style="dim")
    t.append("↑", style="bold #67c23a")
    t.append(tx_tot, style="bold #9fe1b0")
    return t


def _iface_cell(name: str | None, role: str | None = None, device: str | None = None) -> Text:
    shown = display_iface(name, role=role, device=device)
    key = (role or name or "").lower()
    if key.startswith("wan"):
        return Text(shown, style="#f0a000")
    if key.startswith("lan"):
        return Text(shown, style="#4c8dff")
    if key in {"lo", "dummy0", "loopback"} or shown.startswith("LOOPBACK"):
        return Text(shown, style="dim")
    return Text(shown)


def _bandix_traffic(rate_bps, total_bytes, color: str) -> Text:
    t = Text()
    t.append(_fmt_rate(rate_bps or 0), style=f"bold {color}")
    human = _fmt_bytes(total_bytes or 0).replace(" ", "")
    if human and human != "—":
        t.append(" ", style="dim")
        t.append(human, style="dim")
    return t


def _bandix_neighbor_row(n: dict) -> tuple:
    ip = str(n.get("ip") or "—")
    ip_cell = Text(ip, style="dim") if n.get("online") is False else ip
    return (
        ip_cell,
        n.get("mac") or "—",
        n.get("hostname") or "—",
        _iface_cell(n.get("device")),
        n.get("vendor") or "?",
        _bandix_traffic(n.get("lan_rx_bps"), n.get("lan_rx_bytes"), "#4c8dff"),
        _bandix_traffic(n.get("lan_tx_bps"), n.get("lan_tx_bytes"), "#67c23a"),
        _bandix_traffic(n.get("wan_rx_bps"), n.get("wan_rx_bytes"), "#f0a000"),
        _bandix_traffic(n.get("wan_tx_bps"), n.get("wan_tx_bytes"), "#67c23a"),
        str(n.get("conns") if n.get("conns") is not None else "—"),
        str(n.get("tcp") if n.get("tcp") is not None else "—"),
        str(n.get("udp") if n.get("udp") is not None else "—"),
    )


def _diff_rates(rows: list[list], rx_i: int = 1, tx_i: int = 3) -> tuple[list[float], list[float]]:
    rx_s: list[float] = []
    tx_s: list[float] = []
    for prev, cur in zip(rows, rows[1:]):
        try:
            dt = max(float(cur[0]) - float(prev[0]), 0.2)
            rx_s.append(max(0.0, (float(cur[rx_i]) - float(prev[rx_i])) / dt))
            tx_s.append(max(0.0, (float(cur[tx_i]) - float(prev[tx_i])) / dt))
        except (TypeError, ValueError, IndexError):
            continue
    return rx_s, tx_s


def _svc_action_label(action: str) -> str:
    return {
        "start": t("action.start"),
        "stop": t("action.stop"),
        "restart": t("action.restart"),
        "enable": t("action.startup_enable"),
        "disable": t("action.startup_disable"),
    }.get(action, action)


def _svc_pick_detail() -> Text:
    out = Text(t("svc.pick"))
    out.append("\n\n")
    out.append_text(highlight_keys(t("svc.keys")))
    return out


def format_svc_detail(data: dict) -> Text:
    out = Text()
    name = str(data.get("service") or "—")
    out.append(name, style="bold #7ec8ff")
    out.append("\n\n")
    def line(label: str, value, style: str = "bold #f2f6fb") -> None:
        out.append(_pad_label(label), style="dim")
        if isinstance(value, Text):
            out.append_text(value)
        else:
            out.append(str(value if value not in (None, "") else "—"), style=style)
        out.append("\n")

    if data.get("priority") is not None:
        line(t("col.priority"), data.get("priority"))
    line(t("col.running"), _yes_no(bool(data.get("running"))))
    if data.get("enabled") is not None:
        line(t("col.startup"), _yes_no(bool(data.get("enabled"))))
    raw = str(data.get("raw_output") or "").strip()
    if raw and raw.lower() not in {"running", "stopped", "active"}:
        out.append("\n")
        out.append(raw[:360], style="dim")
        out.append("\n")
    uci = data.get("uci")
    out.append("\n")
    if uci:
        line("UCI", f"{uci.get('package')}  {t('svc.sections', n=uci.get('sections'))}")
        types = uci.get("types") or {}
        if types:
            line(t("col.type"), "  ".join(f"{k}×{v}" for k, v in types.items()), "#9ecbff")
        note = t("svc.note.summary")
    else:
        line("UCI", t("svc.no_uci"), "dim")
        note = t("svc.note.no_uci")
    out.append("\n")
    out.append(note, style="dim")
    out.append("\n\n")
    out.append_text(highlight_keys(t("svc.keys")))
    return out


class ConfirmModal(ModalScreen[bool]):
    CSS = """
    ConfirmModal { align: center middle; }
    #confirm-box {
        width: 58;
        max-width: 90%;
        height: auto;
        background: #12283f;
        border: tall #4c8dff;
        padding: 1 2;
    }
    #confirm-msg { width: 1fr; padding: 1 0; color: #f2f6fb; text-style: bold; }
    #confirm-hint { padding-bottom: 1; }
    #confirm-btns { height: 3; align: right middle; }
    #confirm-btns Button { margin-left: 1; }
    """
    BINDINGS = [
        Binding("escape", "cancel", _("action.cancel"), show=False),
        Binding("n", "cancel", _("action.cancel"), show=False),
        Binding("y", "ok", _("action.confirm"), show=False),
        Binding("enter", "ok", _("action.confirm"), show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.message, id="confirm-msg", markup=False)
            yield Static(highlight_keys(t("confirm.hint")), id="confirm-hint")
            with Horizontal(id="confirm-btns"):
                yield Button(t("action.cancel"), id="cancel")
                yield Button(t("action.confirm"), id="ok", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_ok(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class HostnameModal(ModalScreen[str | None]):
    """Edit a Bandix device hostname only."""

    CSS = """
    HostnameModal { align: center middle; }
    #hn-box {
        width: 64;
        max-width: 90%;
        height: auto;
        background: #12283f;
        border: tall #4c8dff;
        padding: 1 2;
    }
    #hn-title { height: 2; color: #7ec8ff; text-style: bold; content-align: left middle; }
    #hn-input {
        width: 1fr;
        height: 3;
        border: none;
        padding: 0 1;
        color: #f2f6fb;
        background: #16324d;
    }
    #hn-hint { height: 1; }
    #hn-btns { height: 3; align: right middle; }
    #hn-btns Button { margin-left: 1; min-width: 12; }
    """
    BINDINGS = [
        Binding("escape", "cancel", _("action.cancel"), show=False, priority=True),
        Binding("ctrl+s", "save", _("action.confirm"), show=False, priority=True),
    ]

    def __init__(self, target: str, hostname: str) -> None:
        super().__init__()
        self.target = target
        self.hostname = hostname

    def compose(self) -> ComposeResult:
        with Vertical(id="hn-box"):
            yield Static(t("neigh.rename_title", target=self.target), id="hn-title")
            yield Input(value=self.hostname, placeholder=t("neigh.rename_placeholder"), id="hn-input")
            yield Static(highlight_keys(t("neigh.rename_hint")), id="hn-hint")
            with Horizontal(id="hn-btns"):
                yield Button(t("action.cancel").upper(), id="cancel")
                yield Button(t("action.confirm").upper(), id="ok", variant="success")

    def on_mount(self) -> None:
        self.query_one("#hn-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "hn-input":
            self.action_save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.action_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self.dismiss(self.query_one("#hn-input", Input).value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class OpenWrtTUI(App):
    """Dashboard: logo + KPIs, Overview embeds Load / Bandwidth / Connections."""

    TITLE = "OpenWRT"
    CSS = """
    Screen { background: $surface; overflow: hidden; }
    #brand { height: 8; overflow: hidden; padding: 1 1 0 1; }
    #logo { width: 42; height: 7; overflow: hidden; color: $accent; content-align: left middle; }
    #header-metrics { height: 7; width: 1fr; padding: 0 1 0 2; }
    .kpi-row { height: 3; }
    .kpi { width: 1fr; height: 3; padding: 0 2 0 2; overflow: hidden; }
    .kpi-split { border-left: solid #2a4660; }
    #tabs { height: 1fr; }
    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; overflow: hidden; }
    #overview { height: 1fr; }
    #net-stack { height: 1fr; overflow: hidden; padding: 0 2; }
    .net-block { overflow: hidden; }
    #ifaces-block { height: 2fr; }
    #routes-block { height: 3fr; padding-top: 1; }
    #rules-block { height: 2fr; padding-top: 1; }
    #lease-stack { height: 1fr; overflow: hidden; padding: 0 2; }
    #lease-table { height: 1fr; }
    #bandix-trend { height: 14; display: none; padding-top: 1; overflow: hidden; }
    #bandix-chart { height: 1fr; }
    #rt-stack { height: 1fr; overflow: hidden; padding: 0 2; }
    .chart-block { height: auto; overflow: hidden; padding: 0; }
    #load-block { height: 10; }
    #bw-block { height: 16; padding: 1 0 0 0; }
    #conn-block { height: 1fr; padding: 1 0 0 0; }
    .chart-title { height: 1; color: $accent; text-style: bold; }
    AreaChart { height: 1fr; }
    #load-panel { height: 9; }
    #bw-panel { height: auto; padding-top: 1; }
    .legend { height: 3; overflow: hidden; }
    DataTable { height: 1fr; }
    DataTable > .datatable--header {
        text-style: bold;
        background: #16324d;
        color: #7ec8ff;
    }
    DataTable > .datatable--cursor { background: #1e4a7a; color: #fff; }
    DataTable > .datatable--odd-row { background: #161616; }
    DataTable > .datatable--even-row { background: $surface; }
    #log-view { height: 1fr; padding: 0 1; }
    #svc-split { height: 1fr; }
    #svc-table { width: 3fr; height: 1fr; }
    #svc-detail {
        width: 2fr;
        height: 1fr;
        padding: 1 2;
        overflow-y: auto;
        border-left: solid #2a4660;
    }
    #proc-split { height: 1fr; }
    #proc-table { width: 3fr; height: 1fr; }
    #proc-detail {
        width: 2fr;
        height: 1fr;
        padding: 1 2;
        overflow-y: auto;
        border-left: solid #2a4660;
    }
    #filter-bar { height: 3; padding: 0 1; display: none; }
    #filter-input { width: 1fr; }
    #filter-count { width: 18; height: 3; content-align: center middle; color: $accent; }
    #footer-bar {
        dock: bottom;
        height: 1;
        background: $footer-background;
        color: $footer-description-foreground;
    }
    #footer-edit {
        width: auto;
        height: 1;
        display: none;
        background: $footer-background;
    }
    #footer-edit.-compact FooterKey {
        margin-right: 1;
    }
    Footer {
        dock: none;
        width: 1fr;
        background: $footer-background;
    }
    #conn-status {
        width: auto;
        height: 1;
        content-align: right middle;
        padding: 0 2 0 1;
        background: $footer-background;
        color: $footer-description-foreground;
    }
    #footer-ver {
        width: auto;
        height: 1;
        content-align: right middle;
        padding: 0 1 0 1;
        color: #7ec8ff;
        background: $footer-background;
    }
    #footer-gh {
        width: auto;
        height: 1;
        min-width: 2;
        padding: 0 2 0 0;
        background: $footer-background;
        color: #7ec8ff;
        text-style: none;
    }
    #footer-gh:hover { color: #fff; text-style: underline; }
    """ + PW2_CSS
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", _("action.quit")),
        Binding("r", "refresh", _("action.refresh")),
        Binding("f", "filter", _("action.filter")),
        Binding("escape", "clear_filter", _("action.clear_filter"), show=False),
        Binding("1", "show_tab('overview')", _("tab.overview"), show=False),
        Binding("2", "show_tab('network')", _("tab.network"), show=False),
        Binding("3", "show_tab('leases')", _("tab.neighbors"), show=False),
        Binding("4", "show_tab('services')", _("tab.services"), show=False),
        Binding("5", "show_tab('process')", _("tab.process"), show=False),
        Binding("6", "show_tab('logs')", _("tab.logs"), show=False),
        Binding("7", "show_tab('passwall2')", _("tab.passwall2"), show=False),
        Binding("left_square_bracket", "pw2_prev_sub", _("pw2.sub.nodes"), show=False),
        Binding("right_square_bracket", "pw2_next_sub", _("pw2.sub.subscribe"), show=False),
        Binding("p", "pw2_ping('icmp')", _("col.ping"), show=False),
        Binding("c", "pw2_ping('tcp')", _("col.tcping"), show=False),
        Binding("l", "pw2_acl_log", _("help.pw2.acl.log"), show=False),
        Binding("a", "pw2_node_add", _("pw2.node.add"), show=False),
        Binding("delete", "pw2_node_delete", _("help.pw2.node.delete"), show=False),
        Binding("backspace", "pw2_node_delete", _("help.pw2.node.delete"), show=False),
        Binding("s", "svc_action('start')", _("action.start"), show=False),
        Binding("x", "svc_action('stop')", _("action.stop"), show=False),
        Binding("t", "svc_action('restart')", _("action.restart"), show=False),
        Binding("e", "edit_or_enable", _("action.edit"), show=False),
        Binding("d", "svc_action('disable')", _("action.disable"), show=False),
        Binding("question_mark", "help", _("action.help")),
    ]

    def __init__(self, device: DeviceClient):
        super().__init__()
        self.device = device
        self._l1: deque[float] = deque(maxlen=WINDOW)
        self._l5: deque[float] = deque(maxlen=WINDOW)
        self._l15: deque[float] = deque(maxlen=WINDOW)
        self._rx: deque[float] = deque(maxlen=WINDOW)
        self._tx: deque[float] = deque(maxlen=WINDOW)
        self._udp: deque[float] = deque(maxlen=WINDOW)
        self._tcp: deque[float] = deque(maxlen=WINDOW)
        self._other: deque[float] = deque(maxlen=WINDOW)
        self._last_rx: int | None = None
        self._last_tx: int | None = None
        self._last_ts: float | None = None
        self._neigh_snap: dict[str, tuple[int, int]] = {}
        self._neigh_ts: float | None = None
        self._filter = ""
        self._net_rows: list[tuple] = []
        self._lease_rows: list[tuple] = []
        self._lease_mode = "arp"
        self._neigh_by_ip: dict[str, dict] = {}
        self._neigh_by_mac: dict[str, dict] = {}
        self._bandix_mac = ""
        self._bandix_ip = ""
        self._lease_cursor_ip = ""
        self._bandix_ignore_highlight = False
        self._bandix_metrics_ready = False
        self._svc_rows: list[tuple] = []
        self._proc_rows: list[tuple] = []
        self._route_rows: list[tuple] = []
        self._rule_rows: list[tuple] = []
        self._log_entries: list[dict] = []
        self._log_keys: set = set()
        self._log_ready = False
        self._counts: dict[str, tuple[int, int]] = {}
        self._last_fail_note = 0.0
        self._pane = "overview"
        self._svc_focus = ""
        self._svc_showing = ""
        self._proc_by_pid: dict[str, dict] = {}
        self._proc_focus = ""
        self._proc_detail_pid = ""
        self._session_lost = False
        self._optional = probe_optional_apps(device)
        self._pw2 = any(app.id == "passwall2" for app in self._optional)
        self._pw2_sub = "pw2-nodes"
        self._pw2_node_rows: list[tuple] = []
        self._pw2_nodes: list[dict] = []
        self._pw2_node_by_id: dict[str, dict] = {}
        self._pw2_sub_rows: list[tuple] = []
        self._pw2_sub_by_id: dict[str, dict] = {}
        self._pw2_acl_rows: list[tuple] = []
        self._pw2_acl_by_id: dict[str, dict] = {}
        self._pw2_rule_rows: list[tuple] = []
        self._pw2_rule_by_id: dict[str, dict] = {}
        self._pw2_focus_node = ""
        self._pw2_focus_acl = ""
        self._pw2_focus_sub = ""
        self._pw2_focus_rule = ""
        self._pw2_detection = "off"
        self._pw2_pending_apply = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="brand"):
            yield Static(render_logo_compact(), id="logo")
            with Vertical(id="header-metrics"):
                with Horizontal(classes="kpi-row"):
                    yield Static(t("kpi.connecting"), id="kpi-host", classes="kpi")
                    yield Static("", id="kpi-cpu", classes="kpi kpi-split")
                    yield Static("", id="kpi-load", classes="kpi kpi-split")
                with Horizontal(classes="kpi-row"):
                    yield Static("", id="kpi-wan", classes="kpi")
                    yield Static("", id="kpi-mem", classes="kpi kpi-split")
                    yield Static("", id="kpi-up", classes="kpi kpi-split")
        with TabbedContent(id="tabs"):
            with TabPane(t("tab.overview"), id="overview"):
                with Vertical(id="rt-stack"):
                    with Vertical(id="load-block", classes="chart-block"):
                        yield Static(t("section.load"), classes="chart-title")
                        yield LoadPanel(id="load-panel")
                    with Vertical(id="bw-block", classes="chart-block"):
                        yield Static(t("section.bandwidth"), classes="chart-title")
                        yield BandwidthPanel(id="bw-panel")
                    with Vertical(id="conn-block", classes="chart-block"):
                        yield Static(t("section.connections"), classes="chart-title")
                        yield AreaChart(kind="conn", style="stack", id="conn-chart")
                        yield Static("", id="conn-legend", classes="legend")
            with TabPane(t("tab.network"), id="network"):
                with Vertical(id="net-stack"):
                    with Vertical(id="ifaces-block", classes="net-block"):
                        yield Static(t("section.interfaces"), classes="chart-title")
                        yield DataTable(id="net-table")
                    with Vertical(id="routes-block", classes="net-block"):
                        yield Static(t("section.routes"), classes="chart-title")
                        yield DataTable(id="route-table")
                    with Vertical(id="rules-block", classes="net-block"):
                        yield Static(t("section.rules"), classes="chart-title")
                        yield DataTable(id="rule-table")
            with TabPane(t("tab.neighbors"), id="leases"):
                with Vertical(id="lease-stack"):
                    yield DataTable(id="lease-table")
                    with Vertical(id="bandix-trend"):
                        yield Static(t("trend.all_devices"), id="bandix-title", classes="chart-title")
                        yield DualRateChart(id="bandix-chart")
            with TabPane(t("tab.services"), id="services"):
                with Horizontal(id="svc-split"):
                    yield DataTable(id="svc-table")
                    yield Static(_svc_pick_detail(), id="svc-detail")
            with TabPane(t("tab.process"), id="process"):
                with Horizontal(id="proc-split"):
                    yield DataTable(id="proc-table")
                    yield Static(t("proc.pick"), id="proc-detail")
            with TabPane(t("tab.logs"), id="logs"):
                yield RichLog(id="log-view", highlight=False, markup=False, wrap=True, max_lines=500)
            for extra in self._optional:
                with TabPane(extra.tab_title(), id=extra.id):
                    yield from extra.compose()
        with Horizontal(id="filter-bar"):
            yield Input(placeholder=t("placeholder.filter"), id="filter-input")
            yield Static("", id="filter-count")
        with Horizontal(id="footer-bar"):
            with Horizontal(id="footer-edit"):
                yield _footer_edit_key()
            yield _footer()
            yield Static(_conn_status(self.device), id="conn-status")
            yield Static(display_version(), id="footer-ver")
            yield Link(GITHUB_ICON, url=REPO_URL, tooltip=REPO_URL, id="footer-gh")

    def on_mount(self) -> None:
        for tid, cols in (
            ("net-table", (t("col.interface"), t("col.protocol"), t("col.carrier"), t("col.uptime"), t("col.mac"), t("col.ipv4"), t("col.ipv6"), t("col.rx"), t("col.tx"))),
            ("lease-table", (t("col.ip"), t("col.mac"), t("col.interface"), t("col.vendor"), t("col.connections"), t("col.received"), t("col.sent"), t("col.inbound"), t("col.outbound"))),
            ("svc-table", (t("col.service"), t("col.priority"), t("col.running"), t("col.startup"))),
            ("proc-table", (t("col.process"), "PID", t("col.user"), "CPU", t("col.memory"), t("col.command"))),
            ("route-table", (t("col.device"), t("col.destination"), t("col.gateway"), t("col.source"), t("col.metric"), t("col.table"), t("col.protocol"))),
            ("rule-table", (t("col.rule"), t("col.priority"), t("col.ingress"), t("col.source"), t("col.src_port"), t("col.action"), t("col.ip_protocol"), t("col.egress"), t("col.destination"), t("col.dst_port"), t("col.table"))),
        ):
            table = self.query_one(f"#{tid}", DataTable)
            table.add_columns(*cols)
            table.cursor_type = "row"
            table.zebra_stripes = True
        if self._pw2:
            setup_passwall2_tables(self)
        self.query_one("#filter-bar").display = False
        self._update_footer_edit()
        self.set_interval(3.0, self.refresh_overview)
        self.set_interval(3.0, self.refresh_process)
        self.set_interval(2.0, self.refresh_logs)
        self.refresh_data()
        self.refresh_logs()

    def action_refresh(self) -> None:
        if self._pane == "passwall2":
            self._pw2_reload()
            return
        self.refresh_data()

    def _typing_filter(self) -> bool:
        bar = self.query_one("#filter-bar")
        if not bar.display:
            return False
        return self.query_one("#filter-input", Input).has_focus

    def _on_bandix_neighbors(self) -> bool:
        if self._pane != "leases" or self._lease_mode != "bandix":
            return False
        return not self._typing_filter()

    def _update_footer_edit(self) -> None:
        show = self._pane == "leases" and self._lease_mode == "bandix"
        box = self.query_one("#footer-edit")
        box.display = show
        if show:
            footer = self.query_one(Footer)
            box.set_class(footer.compact, "-compact")
            self.query_one("#footer-edit FooterKey", FooterKey).compact = footer.compact

    async def _rename_neighbor(self) -> None:
        mac, ip = self._selected_neighbor_mac_ip()
        row = self._neigh_by_mac.get(mac) or self._neigh_by_ip.get(ip) or {}
        if not mac:
            self.notify(t("msg.neigh_pick"), severity="warning")
            return
        current = str(row.get("hostname") or "").strip()
        if current in {"—", ip}:
            current = ""
        name = await self.push_screen_wait(HostnameModal(ip or mac, current))
        if name is None:
            return
        self._set_neighbor_hostname(mac, name)

    @work(thread=True, exclusive=True, group="bandix-write")
    def _set_neighbor_hostname(self, mac: str, hostname: str) -> None:
        try:
            BandixService(self.device).set_hostname(mac, hostname)
            message = t("msg.neigh_renamed", name=hostname or "—")
            severity = "information"
        except Exception as e:
            message = str(e)
            severity = "error"
        self.call_from_thread(self.notify, message, severity=severity)
        self.call_from_thread(self.refresh_data)

    def action_show_tab(self, tab_id: str) -> None:
        if tab_id == "passwall2" and not self._pw2:
            self.notify(t("err.pw2_missing"), severity="warning")
            return
        self.query_one("#tabs", TabbedContent).active = tab_id

    def action_filter(self) -> None:
        bar = self.query_one("#filter-bar")
        bar.display = True
        self.query_one("#filter-input", Input).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape" and self.query_one("#filter-bar").display:
            self.action_clear_filter()
            event.stop()

    def action_clear_filter(self) -> None:
        bar = self.query_one("#filter-bar")
        if bar.display:
            self._filter = ""
            self.query_one("#filter-input", Input).value = ""
            bar.display = False
            self._repaint_lists()
            self._paint_logs()
            return
        if self._pane == "leases" and self._lease_mode == "bandix" and self._bandix_mac:
            self._bandix_mac = ""
            self._bandix_ip = ""
            self.refresh_bandix_metrics()

    def action_help(self) -> None:
        if self._pane == "services":
            self.notify(t("help.svc_keys"))
        elif self._pane == "leases" and self._lease_mode == "bandix":
            self.notify(t("help.neigh_keys"))
        elif self._pane == "passwall2":
            self.notify(t("help.pw2.keys"))
        else:
            self.notify(t("help.generic"))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter-input":
            return
        self._filter = (event.value or "").strip()
        self._repaint_lists()
        self._paint_logs()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.tabbed_content.id == "pw2-tabs":
            self._pw2_sub = event.pane.id if event.pane is not None else "pw2-nodes"
            self._set_filter_hint()
            self._pw2_reload()
            return
        if event.tabbed_content.id != "tabs":
            return
        pane = event.pane.id if event.pane is not None else ""
        self._pane = pane or "overview"
        self._set_filter_hint()
        self._update_footer_edit()
        if self._pane == "logs":
            self.refresh_logs()
        elif self._pane == "leases":
            self.refresh_overview()
            if self._lease_mode == "bandix":
                self.refresh_bandix_metrics()
        elif self._pane == "services":
            name = self._selected_service()
            if name:
                self._load_svc_show(name)
        elif self._pane == "process":
            self.refresh_process()
        elif self._pane == "network":
            self.refresh_routes()
            self.refresh_rules()
        elif self._pane == "passwall2":
            self._pw2_reload()

    def _set_filter_hint(self) -> None:
        hints = {
            "leases": (
                t("filter.neighbors_bandix")
                if self._lease_mode == "bandix"
                else t("filter.neighbors")
            ),
            "network": t("filter.network"),
            "services": t("filter.services"),
            "process": t("filter.process"),
            "logs": t("filter.logs"),
            "overview": t("msg.filter_overview"),
            "passwall2": t("filter.passwall2"),
        }
        self.query_one("#filter-input", Input).placeholder = hints.get(self._pane, t("action.filter"))
        self._update_filter_count()

    def _selected_service(self) -> str:
        table = self.query_one("#svc-table", DataTable)
        if table.row_count == 0:
            return ""
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return ""
        return _plain(row[0]).strip() if row else ""

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "lease-table":
            self._follow_neighbor_row(user_pick=not self._bandix_ignore_highlight)
            return
        if event.data_table.id == "proc-table":
            pid = self._selected_process_pid()
            self._proc_focus = pid
            data = self._proc_by_pid.get(pid)
            if data:
                self._proc_detail_pid = pid
                self._paint_proc_detail(data)
            return
        if event.data_table.id == "pw2-node-table":
            nid = self._selected_pw2_id("pw2-node-table")
            self._pw2_focus_node = nid
            self._paint_pw2_node_detail(nid)
            return
        if event.data_table.id == "pw2-acl-table":
            aid = self._selected_pw2_id("pw2-acl-table")
            self._pw2_focus_acl = aid
            self._paint_pw2_acl_detail(aid)
            return
        if event.data_table.id == "pw2-sub-table":
            sid = self._selected_pw2_id("pw2-sub-table")
            self._pw2_focus_sub = sid
            self._paint_pw2_sub_detail(sid)
            return
        if event.data_table.id == "pw2-rule-table":
            rid = self._selected_pw2_id("pw2-rule-table")
            self._pw2_focus_rule = rid
            self._paint_pw2_rule_detail(rid)
            return
        if event.data_table.id != "svc-table" or self._pane != "services":
            return
        name = self._selected_service()
        if not name or name == self._svc_showing:
            return
        self._svc_showing = name
        self._svc_focus = name
        self._load_svc_show(name)

    def _selected_process_pid(self) -> str:
        table = self.query_one("#proc-table", DataTable)
        if table.row_count == 0:
            return ""
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return ""
        return _plain(row[1]).strip() if row and len(row) > 1 else ""

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "lease-table":
            self._follow_neighbor_row(user_pick=True, force=True)

    def _selected_neighbor_mac_ip(self) -> tuple[str, str]:
        table = self.query_one("#lease-table", DataTable)
        if table.row_count == 0:
            return "", ""
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return "", ""
        if not row:
            return "", ""
        ip = _plain(row[0]).strip()
        if ip == "—":
            ip = ""
        mac = norm_mac(_plain(row[1]).strip()) if len(row) > 1 else ""
        if mac in {"", "—"}:
            mac = norm_mac((self._neigh_by_ip.get(ip) or {}).get("mac"))
        return mac, ip

    def _follow_neighbor_row(self, *, user_pick: bool, force: bool = False) -> None:
        mac, ip = self._selected_neighbor_mac_ip()
        prev_ip = self._lease_cursor_ip
        if user_pick and ip:
            self._lease_cursor_ip = ip
        if not user_pick or self._lease_mode != "bandix" or not mac:
            return
        if not self._bandix_mac and not force and (not prev_ip or ip == prev_ip):
            return
        if mac == self._bandix_mac and ip == self._bandix_ip:
            return
        self._bandix_mac = mac
        self._bandix_ip = ip
        self.refresh_bandix_metrics()

    def _set_bandix_trend_visible(self, visible: bool) -> None:
        trend = self.query_one("#bandix-trend")
        if trend.display != visible:
            trend.display = visible

    def _resume_bandix_highlight(self) -> None:
        self._bandix_ignore_highlight = False

    @work(thread=True, exclusive=True, group="bandix-metrics")
    def refresh_bandix_metrics(self) -> None:
        if self._lease_mode != "bandix":
            return
        mac = self._bandix_mac
        ip = self._bandix_ip
        try:
            data = BandixService(self.device).metrics(mac or None)
        except Exception:
            data = None
        self.call_from_thread(self._apply_bandix_metrics, data, mac, ip)

    def _apply_bandix_metrics(self, data: dict | None, mac: str, ip: str) -> None:
        if mac != self._bandix_mac or ip != self._bandix_ip:
            return
        if not data or not data.get("points"):
            self._bandix_metrics_ready = False
            self._set_bandix_trend_visible(False)
            return
        self._bandix_metrics_ready = True
        self._set_bandix_trend_visible(True)
        rx, tx = downsample_metrics(data.get("points") or [], WINDOW)
        if mac:
            hostname = str((self._neigh_by_ip.get(ip) or {}).get("hostname") or "").strip()
            title = t("trend.target", target=ip or mac)
            if hostname and hostname not in {"—", ip}:
                title = t("trend.target", target=f"{ip or mac}  {hostname}")
        else:
            title = t("trend.all_devices")
        self.query_one("#bandix-title", Static).update(title)
        self.query_one("#bandix-chart", DualRateChart).set_rates(rx, tx)

    def _paint_proc_detail(self, data: dict) -> None:
        self.query_one("#proc-detail", Static).update(format_proc_detail(data))

    @work
    async def action_svc_action(self, action: str) -> None:
        if self._pane == "passwall2" and action == "restart":
            await self._pw2_ask_apply(after_save=False)
            return
        await self._svc_confirm_action(action)

    @work(thread=True, exclusive=True, group="svc-show")
    def _load_svc_show(self, name: str) -> None:
        try:
            data = ServiceService(self.device).show(name).data or {}
        except Exception as e:
            self.call_from_thread(self._paint_svc_detail_error, name, str(e))
            return
        self.call_from_thread(self._paint_svc_detail, data)

    def _paint_svc_detail(self, data: dict) -> None:
        self._svc_focus = str(data.get("service") or self._svc_focus)
        self.query_one("#svc-detail", Static).update(format_svc_detail(data))

    def _paint_svc_detail_error(self, name: str, message: str) -> None:
        out = Text()
        out.append(name or t("col.service"), style="bold #7ec8ff")
        out.append("\n\n")
        out.append(message, style="bold #ed5c5c")
        self.query_one("#svc-detail", Static).update(out)

    @work(thread=True, exclusive=True, group="svc-action")
    def _run_svc_action(self, name: str, action: str) -> None:
        try:
            result = ServiceService(self.device).action(name, action)
            if not result.ok:
                self.call_from_thread(self._toast, result.message or t("msg.svc_fail", name=name, action=action), "error")
                return
            listing = ServiceService(self.device).list().data or {}
            show = ServiceService(self.device).show(name).data or {}
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_svc_action, listing, show, name, action)

    def _apply_svc_action(self, listing: dict, show: dict, name: str, action: str) -> None:
        self._svc_rows = _svc_rows(listing.get("services") or [])
        self._svc_focus = name
        self._svc_showing = name
        self._paint_table("svc-table", self._svc_rows)
        self._paint_svc_detail(show)
        self._toast(t("msg.svc_done", name=name, action=_svc_action_label(action)))

    def _toast(self, message: str, severity: str = "information") -> None:
        self.notify(message, severity=severity, timeout=3)

    def _grab(self, errors: list[Exception], fn, default=None):
        try:
            return fn()
        except Exception as e:
            errors.append(e)
            return default

    def _physical_realtime(self, mon: MonitorService, stats: dict) -> dict[str, list[list]]:
        out: dict[str, list[list]] = {}
        for iface in _physical(stats):
            name = iface.get("name") or ""
            if not name:
                continue
            rows = mon.realtime(name)
            if rows:
                out[name] = rows
        return out

    @work(thread=True, exclusive=True, group="refresh")
    def refresh_overview(self) -> None:
        mon = MonitorService(self.device)
        net = NetworkService(self.device)
        errors: list[Exception] = []
        system = self._grab(errors, lambda: mon.system().data or {}, {})
        memory = (system or {}).get("memory") or {}
        stats = self._grab(errors, lambda: (net.stats(with_rates=False).data or {}), {})
        want_usage = self._pane == "leases"
        neighbors = self._grab(
            errors,
            lambda: (net.neighbors(usage=want_usage).data or {}) if want_usage or not self._lease_rows else None,
            None,
        )
        rt_load = self._grab(errors, lambda: mon.realtime("load"))
        rt_conn = self._grab(errors, lambda: mon.realtime("connections"))
        rt_ifaces = self._grab(errors, lambda: self._physical_realtime(mon, stats), {}) or {}
        conn_n = self._grab(errors, mon.conntrack_count)
        if errors:
            self.call_from_thread(self._refresh_failed, errors[0])
        if not system:
            if neighbors is not None:
                self.call_from_thread(self._apply_neighbors, neighbors, time.monotonic())
            return
        sampled_at = time.monotonic()
        self.call_from_thread(
            self._apply_overview,
            system, memory, stats, rt_load, rt_conn, rt_ifaces, conn_n, sampled_at,
            neighbors,
        )

    @work(thread=True, exclusive=True, group="process")
    def refresh_process(self) -> None:
        if self._pane != "process" and self._proc_rows:
            return
        try:
            data = MonitorService(self.device).processes().data or {}
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_processes, data)

    def _apply_processes(self, data: dict) -> None:
        self._store_processes(data.get("processes") or [])
        self._paint_table("proc-table", self._proc_rows)

    def _store_processes(self, processes: list[dict]) -> None:
        self._proc_by_pid = {str(p.get("pid") or ""): p for p in processes if p.get("pid") not in (None, "")}
        self._proc_rows = _proc_rows(processes)
        pid = self._proc_detail_pid or self._proc_focus
        if pid and pid in self._proc_by_pid:
            self._proc_detail_pid = pid
            self._paint_proc_detail(self._proc_by_pid[pid])
        elif processes:
            first = str(processes[0].get("pid") or "")
            if first:
                self._proc_detail_pid = first
                self._paint_proc_detail(self._proc_by_pid[first])

    @work(thread=True, exclusive=True, group="routes")
    def refresh_routes(self) -> None:
        try:
            data = NetworkService(self.device).routes().data or {}
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_routes, data)

    def _apply_routes(self, data: dict) -> None:
        self._route_rows = _route_rows(data.get("routes") or [])
        self._paint_table("route-table", self._route_rows)

    @work(thread=True, exclusive=True, group="rules")
    def refresh_rules(self) -> None:
        try:
            data = NetworkService(self.device).rules().data or {}
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_rules, data)

    def _apply_rules(self, data: dict) -> None:
        self._rule_rows = _rule_rows(data.get("rules") or [])
        self._paint_table("rule-table", self._rule_rows)

    @work(thread=True, exclusive=True, group="full")
    def refresh_data(self) -> None:
        mon = MonitorService(self.device)
        net = NetworkService(self.device)
        svc = ServiceService(self.device)
        errors: list[Exception] = []
        system = self._grab(errors, lambda: mon.system().data or {}, {})
        memory = (system or {}).get("memory") or {}
        stats = self._grab(errors, lambda: (net.stats().data or {}), {})
        leases = self._grab(errors, lambda: (net.neighbors().data or {}), None)
        services = self._grab(errors, lambda: (svc.list().data or {}), {})
        processes = self._grab(errors, lambda: (mon.processes().data or {}), {})
        routes = self._grab(errors, lambda: (net.routes().data or {}), {})
        rules = self._grab(errors, lambda: (net.rules().data or {}), {})
        rt_load = self._grab(errors, lambda: mon.realtime("load"))
        rt_conn = self._grab(errors, lambda: mon.realtime("connections"))
        rt_ifaces = self._grab(errors, lambda: self._physical_realtime(mon, stats), {}) or {}
        conn_n = self._grab(errors, mon.conntrack_count)
        if errors:
            self.call_from_thread(self._refresh_failed, errors[0])
        if not system:
            return
        sampled_at = time.monotonic()
        self.call_from_thread(
            self._apply, system, memory, stats, leases, services, processes, routes, rules,
            rt_load, rt_conn, rt_ifaces, conn_n, sampled_at,
        )

    def _refresh_failed(self, exc: Exception) -> None:
        text = str(exc).lower()
        raw = str(exc)
        if (
            "access denied" in text
            or "session expired" in text
            or "not logged in" in text
            or "login failed" in text
            or "会话已失效" in raw
            or "尚未登录" in raw
            or "登录失败" in raw
        ):
            self._session_lost = True
        now = time.monotonic()
        if now - self._last_fail_note < 15:
            return
        self._last_fail_note = now
        self.notify(_short_refresh_error(exc), severity="warning", timeout=4)

    def _set_header(self, content) -> None:
        ids = ("kpi-host", "kpi-cpu", "kpi-load", "kpi-wan", "kpi-mem", "kpi-up")
        for sid, cell in zip(ids, content):
            self.query_one(f"#{sid}", Static).update(cell)

    def _note_session_restored(self) -> None:
        if self._session_lost:
            self._session_lost = False
            self._toast(t("msg.session_restored"))

    def _apply_overview(
        self, system, memory, stats, rt_load, rt_conn, rt_ifaces, conn_n, sampled_at: float,
        neighbors: dict | None = None,
    ) -> None:
        self._note_session_restored()
        board = system.get("board") or {}
        uptime = (system.get("uptime") or {}).get("human", "?")
        loads = list(system.get("load_average") or [0, 0, 0])
        while len(loads) < 3:
            loads.append(0)
        l1, l5, l15 = (float(loads[0] or 0), float(loads[1] or 0), float(loads[2] or 0))

        if rt_load and len(rt_load[0]) >= 4:
            self._l1.clear()
            self._l5.clear()
            self._l15.clear()
            for row in rt_load[-WINDOW:]:
                try:
                    self._l1.append(float(row[1]) / 100.0)
                    self._l5.append(float(row[2]) / 100.0)
                    self._l15.append(float(row[3]) / 100.0)
                except (TypeError, ValueError, IndexError):
                    continue
            if self._l1:
                l1, l5, l15 = self._l1[-1], self._l5[-1], self._l15[-1]
        else:
            self._l1.append(l1)
            self._l5.append(l5)
            self._l15.append(l15)

        rx_rate = tx_rate = 0.0
        wan = _pick_wan(stats) or {}
        wan_name = wan.get("name") or ""
        wan_from_bwc = False
        bw_items: list[tuple[str, str, list[float], list[float]]] = []
        for iface in _physical(stats):
            name = iface.get("name") or ""
            if not name:
                continue
            rows = (rt_ifaces or {}).get(name)
            if rows:
                rxs, txs = _diff_rates(rows)
                rxs, txs = rxs[-WINDOW:], txs[-WINDOW:]
            else:
                rxs, txs = [], []
            bw_items.append((name, iface.get("label") or name, rxs, txs))
            if rxs and txs and (name == wan_name or (not wan_name and not wan_from_bwc)):
                self._rx.clear()
                self._tx.clear()
                self._rx.extend(rxs)
                self._tx.extend(txs)
                rx_rate, tx_rate = rxs[-1], txs[-1]
                wan_from_bwc = True
        if not wan_from_bwc:
            rx, tx = _sum_bytes([wan] if wan else _physical(stats))
            if self._last_ts is not None and self._last_rx is not None and self._last_tx is not None:
                dt = max(sampled_at - self._last_ts, 0.2)
                rx_rate = max(0.0, (rx - self._last_rx) / dt)
                tx_rate = max(0.0, (tx - self._last_tx) / dt)
                self._rx.append(rx_rate)
                self._tx.append(tx_rate)
            self._last_rx, self._last_tx, self._last_ts = rx, tx, sampled_at

        if rt_conn and len(rt_conn[0]) >= 4:
            self._udp.clear()
            self._tcp.clear()
            self._other.clear()
            for row in rt_conn[-WINDOW:]:
                try:
                    self._udp.append(float(row[1]))
                    self._tcp.append(float(row[2]))
                    self._other.append(float(row[3]))
                except (TypeError, ValueError, IndexError):
                    continue
        elif conn_n is not None:
            self._tcp.append(float(conn_n))

        total = float((memory.get("total") or {}).get("mb") or 0)
        avail = float((memory.get("available") or {}).get("mb") or 0)
        used_pct = (1 - avail / total) * 100 if total else 0.0
        conn_label = "—"
        if self._tcp or self._udp:
            conn_label = str(int((self._tcp[-1] if self._tcp else 0) + (self._udp[-1] if self._udp else 0) + (self._other[-1] if self._other else 0)))
        elif conn_n is not None:
            conn_label = str(conn_n)

        release = board.get("release") or {}
        hostname = board.get("hostname") or "?"
        version = str(release.get("version") or "").strip()
        kernel = str(board.get("kernel") or "").strip()
        arch = str(release.get("target") or "").strip()
        cpu = _clip(str(board.get("system") or ""), 40)
        self._set_header([
            _metric(t("kpi.hostname_version"), _host_version_value(hostname, version, kernel)),
            _metric(t("kpi.arch_cpu"), cpu or "—", tag=arch or None),
            _metric(t("kpi.cpu_load"), _load_value(l1, l5, l15)),
            _metric(t("kpi.wan"), _wan_value(stats, rx_rate, tx_rate)),
            _metric(t("kpi.memory"), _mem_value(used_pct, avail, total)),
            _metric(t("kpi.uptime"), Text(uptime, style="bold #9ecbff")),
        ])

        load_s = [
            Series(t("legend.load_1m"), C_LOAD1, list(self._l1)),
            Series(t("legend.load_5m"), C_LOAD5, list(self._l5)),
            Series(t("legend.load_15m"), C_LOAD15, list(self._l15)),
        ]
        conn_s = [
            Series("UDP", C_UDP, list(self._udp)),
            Series("TCP", C_TCP, list(self._tcp)),
            Series("Other", C_OTHER, list(self._other)),
        ]
        self.query_one("#load-panel", LoadPanel).set_series(load_s)
        self.query_one("#bw-panel", BandwidthPanel).set_ifaces(bw_items)
        self.query_one("#conn-chart", AreaChart).set_series(conn_s)
        self.query_one("#conn-legend", Static).update(render_legend(conn_s, kind="conn"))

        self._net_rows = [
            (
                _iface_cell(
                    i.get("name"),
                    i.get("role") or i.get("label"),
                    i.get("l3_device") or i.get("device"),
                ),
                i.get("proto_human") or i.get("proto") or "—",
                i.get("carrier_human") or "—",
                i.get("uptime_human") or "—",
                i.get("mac") or "—",
                _first_addr(i.get("ipv4") or i.get("ipaddr")),
                _first_addr(i.get("ipv6"), 24),
                i.get("rx_detail") or i.get("rx_human") or "—",
                i.get("tx_detail") or i.get("tx_human") or "—",
            )
            for i in stats.get("interfaces") or []
        ]
        self._paint_table("net-table", self._net_rows)
        if neighbors is not None:
            self._apply_neighbors(neighbors, sampled_at)

    def _ensure_lease_cols(self, mode: str) -> None:
        if self._lease_mode == mode:
            return
        self._lease_mode = mode
        table = self.query_one("#lease-table", DataTable)
        table.clear(columns=True)
        if mode == "bandix":
            table.add_columns(t("col.ip"), t("col.mac"), t("col.hostname_short"), t("col.interface"), t("col.vendor"), t("col.lan_down"), t("col.lan_up"), t("col.wan_down"), t("col.wan_up"), t("col.connections"), "TCP", "UDP")
        else:
            table.add_columns(t("col.ip"), t("col.mac"), t("col.interface"), t("col.vendor"), t("col.connections"), t("col.received"), t("col.sent"), t("col.inbound"), t("col.outbound"))
        self._set_filter_hint()
        self._update_footer_edit()

    def _apply_neighbors(self, payload: dict, sampled_at: float) -> None:
        rows = list(payload.get("neighbors") or [])
        self._neigh_by_ip = {str(n.get("ip") or ""): n for n in rows if n.get("ip")}
        self._neigh_by_mac = {norm_mac(n.get("mac")): n for n in rows if n.get("mac")}
        if payload.get("source") == "bandix":
            self._ensure_lease_cols("bandix")
            self._lease_rows = [_bandix_neighbor_row(n) for n in rows]
            self._paint_table("lease-table", self._lease_rows)
            if self._pane == "leases":
                self.refresh_bandix_metrics()
            elif self._bandix_metrics_ready:
                self._set_bandix_trend_visible(True)
            return
        if self._lease_mode == "bandix":
            self._bandix_mac = ""
            self._bandix_ip = ""
            self._bandix_metrics_ready = False
        self._set_bandix_trend_visible(False)
        self._ensure_lease_cols("arp")
        dt = max(sampled_at - self._neigh_ts, 0.2) if self._neigh_ts is not None else None
        painted: list[tuple] = []
        snap: dict[str, tuple[int, int]] = {}
        for n in rows:
            ip = str(n.get("ip") or "")
            try:
                rx_b = int(n.get("rx_bytes") or 0)
                tx_b = int(n.get("tx_bytes") or 0)
            except (TypeError, ValueError):
                rx_b = tx_b = 0
            snap[ip] = (rx_b, tx_b)
            rx_rate = tx_rate = None
            if dt is not None and ip in self._neigh_snap:
                prx, ptx = self._neigh_snap[ip]
                rx_rate = max(0.0, (rx_b - prx) / dt)
                tx_rate = max(0.0, (tx_b - ptx) / dt)
            elif n.get("rx_bps") is not None:
                rx_rate = float(n.get("rx_bps") or 0)
                tx_rate = float(n.get("tx_bps") or 0)
            painted.append((
                ip,
                n.get("mac", ""),
                _iface_cell(n.get("device")),
                n.get("vendor") or "?",
                str(n.get("conns") if n.get("conns") is not None else "—"),
                n.get("rx_human") or "—",
                n.get("tx_human") or "—",
                Text(_fmt_rate(rx_rate), style="#4c8dff") if rx_rate is not None else "—",
                Text(_fmt_rate(tx_rate), style="#67c23a") if tx_rate is not None else "—",
            ))
        self._neigh_snap = snap
        self._neigh_ts = sampled_at
        self._lease_rows = painted
        self._paint_table("lease-table", self._lease_rows)

    def _apply(
        self, system, memory, stats, leases, services, processes, routes, rules,
        rt_load, rt_conn, rt_ifaces, conn_n, sampled_at: float,
    ) -> None:
        self._apply_overview(
            system, memory, stats, rt_load, rt_conn, rt_ifaces, conn_n, sampled_at, leases,
        )
        self._svc_rows = _svc_rows((services or {}).get("services") or [])
        self._store_processes((processes or {}).get("processes") or [])
        self._route_rows = _route_rows((routes or {}).get("routes") or [])
        self._rule_rows = _rule_rows((rules or {}).get("rules") or [])
        self._repaint_lists()

    def _row_match(self, row: tuple) -> bool:
        if not self._filter:
            return True
        q = self._filter.lower()
        return q in " ".join(_plain(c) for c in row).lower()

    def _paint_table(self, table_id: str, rows: list[tuple]) -> None:
        table = self.query_one(f"#{table_id}", DataTable)
        keep = ""
        keep_col = 0
        if table_id == "svc-table":
            keep, keep_col = self._svc_focus, 0
        elif table_id == "proc-table":
            keep, keep_col = self._proc_focus, 1
        elif table_id == "lease-table":
            keep, keep_col = self._lease_cursor_ip, 0
            self._bandix_ignore_highlight = True
        elif table_id == "pw2-node-table":
            keep, keep_col = self._pw2_focus_node, 0
        elif table_id == "pw2-acl-table":
            keep, keep_col = self._pw2_focus_acl, 0
        elif table_id == "pw2-sub-table":
            keep, keep_col = self._pw2_focus_sub, 0
        table.clear()
        shown = 0
        keep_row = 0
        for row in rows:
            if not self._row_match(row):
                continue
            table.add_row(*row)
            if keep and len(row) > keep_col and _plain(row[keep_col]) == keep:
                keep_row = shown
            shown += 1
        self._counts[table_id] = (shown, len(rows))
        self._update_filter_count()
        if table_id in {"svc-table", "proc-table", "lease-table", "pw2-node-table", "pw2-acl-table", "pw2-sub-table", "pw2-rule-table"} and shown:
            table.move_cursor(row=keep_row)
        if table_id == "lease-table":
            self.call_after_refresh(self._resume_bandix_highlight)

    def _repaint_lists(self) -> None:
        self._paint_table("net-table", self._net_rows)
        self._paint_table("lease-table", self._lease_rows)
        self._paint_table("svc-table", self._svc_rows)
        self._paint_table("proc-table", self._proc_rows)
        self._paint_table("route-table", self._route_rows)
        self._paint_table("rule-table", self._rule_rows)
        if self._pw2:
            self._paint_table("pw2-node-table", self._pw2_node_rows)
            self._paint_table("pw2-sub-table", self._pw2_sub_rows)
            self._paint_table("pw2-rule-table", self._pw2_rule_rows)
            self._paint_table("pw2-acl-table", self._pw2_acl_rows)

    def _active_pane(self) -> str:
        return self._pane or "overview"

    def _update_filter_count(self) -> None:
        mapping = {
            "leases": "lease-table",
            "services": "svc-table",
            "process": "proc-table",
        }
        pane = self._active_pane()
        label = ""
        if pane == "logs":
            q = self._filter.lower()
            total = len(self._log_entries)
            shown = total if not q else sum(1 for e in self._log_entries if q in format_log_line(e).lower())
            label = f"{shown}/{total}"
        elif pane == "network":
            ni, ti = self._counts.get("net-table", (0, 0))
            nr, tr = self._counts.get("route-table", (0, 0))
            nu, tu = self._counts.get("rule-table", (0, 0))
            label = f"{ni}/{ti} · {nr}/{tr} · {nu}/{tu}"
        elif pane == "passwall2":
            sub_map = {
                "pw2-nodes": "pw2-node-table",
                "pw2-subscribe": "pw2-sub-table",
                "pw2-rules": "pw2-rule-table",
                "pw2-acl": "pw2-acl-table",
            }
            tid = sub_map.get(self._pw2_sub)
            if tid:
                shown, total = self._counts.get(tid, (0, 0))
                label = f"{shown}/{total}"
        elif pane in mapping:
            shown, total = self._counts.get(mapping[pane], (0, 0))
            label = f"{shown}/{total}"
        self.query_one("#filter-count", Static).update(label)

    def _paint_logs(self) -> None:
        view = self.query_one("#log-view", RichLog)
        view.clear()
        q = self._filter.lower()
        for entry in self._log_entries:
            line = format_log_line(entry)
            if q and q not in line.lower():
                continue
            view.write(format_log_text(entry))

    def _merge_logs(self, entries: list[dict]) -> None:
        added = []
        for entry in entries:
            key = entry.get("id")
            if key in (None, "", 0, "0"):
                key = ("msg", entry.get("time"), entry.get("msg"))
            if key in self._log_keys:
                continue
            self._log_keys.add(key)
            self._log_entries.append(entry)
            added.append(entry)
        if len(self._log_entries) > 500:
            self._log_entries = self._log_entries[-400:]
            self._log_keys = {
                (e.get("id") if e.get("id") not in (None, "", 0, "0") else ("msg", e.get("time"), e.get("msg")))
                for e in self._log_entries
            }
        if not self._log_ready:
            self._paint_logs()
            self._log_ready = True
        elif added and self._filter:
            self._paint_logs()
        elif added:
            view = self.query_one("#log-view", RichLog)
            for entry in added:
                view.write(format_log_text(entry))
        self._update_filter_count()

    @work(thread=True, exclusive=True, group="logs")
    def refresh_logs(self) -> None:
        if self._pane != "logs" and self._log_ready:
            return
        try:
            result = SystemService(self.device).logs(kernel=False, lines=120)
            entries = list((result.data or {}).get("entries") or [])
        except Exception as e:
            self.call_from_thread(self._merge_logs_error, str(e))
            return
        self.call_from_thread(self._merge_logs, entries)

    def _merge_logs_error(self, message: str) -> None:
        if self._log_ready:
            return
        self._log_ready = True
        self.query_one("#log-view", RichLog).write(Text(t("msg.logs_fail", message=message), style="bold #ed5c5c"))

    def _selected_pw2_id(self, table_id: str) -> str:
        table = self.query_one(f"#{table_id}", DataTable)
        if table.row_count == 0:
            return ""
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return ""
        return _plain(row[0]).strip() if row else ""

    def _pw2_reload(self) -> None:
        if not self._pw2:
            return
        sub = self._pw2_sub
        if sub == "pw2-nodes":
            self._load_pw2_nodes()
        elif sub == "pw2-subscribe":
            self._load_pw2_subscribe()
        elif sub == "pw2-settings-tab":
            self._load_pw2_settings()
        elif sub == "pw2-rules":
            self._load_pw2_rules()
        elif sub == "pw2-acl":
            self._load_pw2_acl()
        elif sub == "pw2-logs":
            self._load_pw2_logs()

    def action_pw2_prev_sub(self) -> None:
        if self._pane != "passwall2" or not self._pw2:
            return
        idx = PW2_SUBS.index(self._pw2_sub) if self._pw2_sub in PW2_SUBS else 0
        self.query_one("#pw2-tabs", TabbedContent).active = PW2_SUBS[(idx - 1) % len(PW2_SUBS)]

    def action_pw2_next_sub(self) -> None:
        if self._pane != "passwall2" or not self._pw2:
            return
        idx = PW2_SUBS.index(self._pw2_sub) if self._pw2_sub in PW2_SUBS else 0
        self.query_one("#pw2-tabs", TabbedContent).active = PW2_SUBS[(idx + 1) % len(PW2_SUBS)]

    def action_pw2_ping(self, mode: str) -> None:
        if self._pane != "passwall2" or self._pw2_sub != "pw2-nodes":
            return
        nid = self._selected_pw2_id("pw2-node-table") or self._pw2_focus_node
        if not nid:
            self.notify(t("msg.pw2_pick"), severity="warning")
            return
        self._pw2_ping_one(nid, "tcping" if mode == "tcp" else "icmp")

    def _on_pw2_nodes(self) -> bool:
        if self._pane != "passwall2" or not self._pw2 or self._pw2_sub != "pw2-nodes":
            return False
        return not self._typing_filter()

    def _on_pw2_acl(self) -> bool:
        if self._pane != "passwall2" or not self._pw2 or self._pw2_sub != "pw2-acl":
            return False
        return not self._typing_filter()

    @work
    async def action_pw2_node_add(self) -> None:
        if self._on_pw2_acl():
            await self._pw2_acl_form()
            return
        if not self._on_pw2_nodes():
            return
        await self._pw2_node_form()

    @work
    async def action_edit_or_enable(self) -> None:
        if self._on_bandix_neighbors():
            await self._rename_neighbor()
            return
        if self._on_pw2_nodes():
            await self._pw2_node_form(self._selected_pw2_id("pw2-node-table") or self._pw2_focus_node)
            return
        if self._on_pw2_acl():
            await self._pw2_acl_form(self._selected_pw2_id("pw2-acl-table") or self._pw2_focus_acl)
            return
        await self._svc_confirm_action("enable")

    async def _svc_confirm_action(self, action: str) -> None:
        if self._pane != "services":
            return
        name = self._selected_service() or self._svc_focus
        if not name:
            self.notify(t("msg.pick_service"), severity="warning")
            return
        label = _svc_action_label(action)
        ok = await self.push_screen_wait(ConfirmModal(t("confirm.svc", name=name, action=label)))
        if ok:
            self._run_svc_action(name, action)

    async def _pw2_node_form(self, node_id: str = "") -> None:
        node = self._pw2_node_by_id.get(node_id) if node_id else None
        if node_id and not node:
            self.notify(t("msg.pw2_pick_node"), severity="warning")
            return
        ok = await self.push_screen_wait(NodeFormModal(self.device, node))
        if not ok:
            return
        self._pw2_reload()
        await self._pw2_ask_apply()

    async def _pw2_acl_form(self, acl_id: str = "") -> None:
        acl = self._pw2_acl_by_id.get(acl_id) if acl_id else None
        if acl_id and not acl:
            self.notify(t("msg.pw2_pick_acl"), severity="warning")
            return
        nodes = self._pw2_nodes
        if not nodes:
            from openwrt_cli.services.passwall2 import PassWall2Service

            try:
                listed = PassWall2Service(self.device).nodes(measure=False)
                nodes = list((listed.data or {}).get("nodes") or [])
                self._pw2_nodes = nodes
                self._pw2_node_by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
            except Exception:
                nodes = []
        ok = await self.push_screen_wait(AclFormModal(self.device, acl, nodes))
        if not ok:
            return
        self._pw2_reload()
        await self._pw2_ask_apply()

    @work
    async def action_pw2_node_delete(self) -> None:
        if self._on_pw2_acl():
            await self._pw2_acl_delete()
            return
        if not self._on_pw2_nodes():
            return
        nid = self._selected_pw2_id("pw2-node-table") or self._pw2_focus_node
        node = self._pw2_node_by_id.get(nid)
        if not node:
            self.notify(t("msg.pw2_pick_node"), severity="warning")
            return
        ok = await self.push_screen_wait(
            ConfirmModal(t("confirm.pw2_node_delete_tui", name=node.get("remarks") or nid, id=nid))
        )
        if not ok:
            return
        self._pw2_delete_node(nid, node.get("remarks") or nid, force=False)

    async def _pw2_acl_delete(self) -> None:
        aid = self._selected_pw2_id("pw2-acl-table") or self._pw2_focus_acl
        item = self._pw2_acl_by_id.get(aid)
        if not item:
            self.notify(t("msg.pw2_pick_acl"), severity="warning")
            return
        ok = await self.push_screen_wait(
            ConfirmModal(t("confirm.pw2_acl_delete_tui", name=item.get("remarks") or aid, id=aid))
        )
        if not ok:
            return
        self._pw2_delete_acl(aid)

    async def _pw2_ask_apply(self, *, after_save: bool = True) -> None:
        ok = await self.push_screen_wait(ConfirmModal(t("confirm.pw2_apply")))
        if ok:
            self._pw2_pending_apply = False
            self._pw2_apply()
            return
        if after_save:
            self._pw2_pending_apply = True
            self.notify(t("msg.pw2_saved_pending"), severity="warning")

    @work
    async def _pw2_ask_apply_work(self) -> None:
        await self._pw2_ask_apply()

    @work(thread=True, exclusive=True, group="pw2-write")
    def _pw2_apply(self) -> None:
        from openwrt_cli.services.service import ServiceService

        try:
            result = ServiceService(self.device).action("passwall2", "restart")
            message = result.message or t("msg.pw2_node_saved")
            severity = "information" if result.ok else "error"
            if result.ok:
                self._pw2_pending_apply = False
        except Exception as e:
            message = str(e)
            severity = "error"
        self.call_from_thread(self.notify, message, severity=severity)
        self.call_from_thread(self._pw2_reload)

    @work(thread=True, exclusive=True, group="pw2-write")
    def _pw2_delete_acl(self, acl_id: str) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            result = PassWall2Service(self.device).acl_delete(acl_id)
        except Exception as e:
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        if not result.ok:
            self.call_from_thread(self.notify, result.message or t("err.pw2_acl_invalid", error=""), severity="error")
            return
        self.call_from_thread(self.notify, result.message or t("msg.pw2_acl_deleted"))
        self.call_from_thread(self._pw2_reload)
        self.call_from_thread(self._pw2_ask_apply_later)

    @work(thread=True, exclusive=True, group="pw2-write")
    def _pw2_delete_node(self, node_id: str, remarks: str, force: bool) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            result = PassWall2Service(self.device).node_delete(node_id, force=force)
        except Exception as e:
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        data = result.data or {}
        if not result.ok and data.get("error") == "pw2_node_in_use" and not force:
            refs = ", ".join(data.get("refs") or [])
            self.call_from_thread(self._pw2_confirm_force_delete, node_id, remarks, refs)
            return
        if not result.ok:
            self.call_from_thread(self.notify, result.message or t("err.pw2_node_invalid", error=""), severity="error")
            return
        self.call_from_thread(self.notify, result.message or t("msg.pw2_node_deleted"))
        self.call_from_thread(self._pw2_reload)
        self.call_from_thread(self._pw2_ask_apply_later)

    def _pw2_confirm_force_delete(self, node_id: str, remarks: str, refs: str) -> None:
        self._pw2_ask_force_delete(node_id, remarks, refs)

    def _pw2_ask_apply_later(self) -> None:
        self._pw2_ask_apply_work()

    @work
    async def _pw2_ask_force_delete(self, node_id: str, remarks: str, refs: str) -> None:
        ok = await self.push_screen_wait(
            ConfirmModal(t("confirm.pw2_node_in_use", name=remarks, refs=refs))
        )
        if ok:
            self._pw2_delete_node(node_id, remarks, force=True)

    def action_pw2_acl_log(self) -> None:
        if self._pane != "passwall2" or self._pw2_sub != "pw2-acl":
            return
        aid = self._selected_pw2_id("pw2-acl-table") or self._pw2_focus_acl
        if not aid:
            self.notify(t("msg.pw2_pick"), severity="warning")
            return
        item = self._pw2_acl_by_id.get(aid) or {}
        from openwrt_cli.services.passwall2 import acl_log_path

        self.push_screen(AclLogModal(
            self.device,
            aid,
            str(item.get("remarks") or aid),
            str(item.get("log_file") or acl_log_path(aid)),
        ))

    def _paint_pw2_node_detail(self, nid: str) -> None:
        self.query_one("#pw2-node-detail", Static).update(format_node_detail(self._pw2_node_by_id.get(nid)))

    def _paint_pw2_acl_detail(self, aid: str) -> None:
        self.query_one("#pw2-acl-detail", Static).update(format_acl_detail(self._pw2_acl_by_id.get(aid)))

    def _paint_pw2_sub_detail(self, sid: str) -> None:
        self.query_one("#pw2-sub-detail", Static).update(format_subscribe_detail(self._pw2_sub_by_id.get(sid)))

    def _paint_pw2_rule_detail(self, rid: str) -> None:
        self.query_one("#pw2-rule-detail", Static).update(format_rule_detail(self._pw2_rule_by_id.get(rid)))

    @work(thread=True, exclusive=True, group="pw2")
    def _load_pw2_nodes(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service, can_measure

        try:
            svc = PassWall2Service(self.device)
            result = svc.nodes(measure=False)
            data = result.data or {}
            rows = list(data.get("nodes") or [])
            mode = data.get("detection") or "off"
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_pw2_nodes, rows, mode)
        for node in rows:
            if not can_measure(node):
                continue
            nid = node.get("id") or ""
            for key, ping_mode in (("ping", "icmp"), ("tcping", "tcping")):
                try:
                    latency = svc.ping_address(node.get("address") or "", node.get("port"), ping_mode)
                except Exception:
                    latency = "—"
                self.call_from_thread(self._apply_pw2_latency, nid, key, latency)

    def _apply_pw2_nodes(self, rows: list[dict], mode: str) -> None:
        self._pw2_detection = mode
        self._pw2_nodes = rows
        self._pw2_node_by_id = {str(n.get("id")): n for n in rows if n.get("id")}
        self._pw2_node_rows = [node_row(n) for n in rows]
        self._paint_table("pw2-node-table", self._pw2_node_rows)
        nid = self._pw2_focus_node or (rows[0].get("id") if rows else "")
        if nid:
            self._pw2_focus_node = str(nid)
            self._paint_pw2_node_detail(str(nid))

    def _apply_pw2_latency(self, nid: str, key: str, latency: str) -> None:
        node = self._pw2_node_by_id.get(nid)
        if not node:
            return
        node[key] = latency
        self._pw2_node_rows = [node_row(n) for n in self._pw2_nodes]
        self._paint_table("pw2-node-table", self._pw2_node_rows)
        if nid == self._pw2_focus_node:
            self._paint_pw2_node_detail(nid)

    @work(thread=True, exclusive=True, group="pw2-one")
    def _pw2_ping_one(self, nid: str, mode: str) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            result = PassWall2Service(self.device).node_ping(nid, mode=mode)
            latency = (result.data or {}).get("latency") or "—"
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        key = "tcping" if mode == "tcping" else "ping"
        self.call_from_thread(self._apply_pw2_latency, nid, key, latency)

    @work(thread=True, exclusive=True, group="pw2")
    def _load_pw2_subscribe(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            data = PassWall2Service(self.device).subscribe().data or {}
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_pw2_subscribe, data.get("subscribe") or [])

    def _apply_pw2_subscribe(self, rows: list[dict]) -> None:
        self._pw2_sub_by_id = {str(s.get("id")): s for s in rows if s.get("id")}
        self._pw2_sub_rows = [subscribe_row(s) for s in rows]
        self._paint_table("pw2-sub-table", self._pw2_sub_rows)
        sid = self._pw2_focus_sub or (rows[0].get("id") if rows else "")
        if sid:
            self._pw2_focus_sub = str(sid)
            self._paint_pw2_sub_detail(str(sid))

    @work(thread=True, exclusive=True, group="pw2")
    def _load_pw2_settings(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            data = PassWall2Service(self.device).settings().data or {}
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_pw2_settings, data)

    def _apply_pw2_settings(self, data: dict) -> None:
        self.query_one("#pw2-settings", Static).update(format_settings(data))

    @work(thread=True, exclusive=True, group="pw2")
    def _load_pw2_rules(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            data = PassWall2Service(self.device).rules().data or {}
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_pw2_rules, data)

    def _apply_pw2_rules(self, data: dict) -> None:
        self.query_one("#pw2-rules-geo", Static).update(format_rules_geo(data.get("global_rules") or {}))
        rows = list(data.get("shunt_rules") or [])
        self._pw2_rule_by_id = {str(r.get("id")): r for r in rows if r.get("id")}
        self._pw2_rule_rows = [shunt_row(r) for r in rows]
        self._paint_table("pw2-rule-table", self._pw2_rule_rows)
        rid = self._pw2_focus_rule or (rows[0].get("id") if rows else "")
        if rid:
            self._pw2_focus_rule = str(rid)
            self._paint_pw2_rule_detail(str(rid))

    @work(thread=True, exclusive=True, group="pw2")
    def _load_pw2_acl(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            data = PassWall2Service(self.device).acl().data or {}
        except Exception as e:
            self.call_from_thread(self._toast, str(e), "error")
            return
        self.call_from_thread(self._apply_pw2_acl, data.get("acl") or [])

    def _apply_pw2_acl(self, rows: list[dict]) -> None:
        self._pw2_acl_by_id = {str(a.get("id")): a for a in rows if a.get("id")}
        self._pw2_acl_rows = [acl_row(a) for a in rows]
        self._paint_table("pw2-acl-table", self._pw2_acl_rows)
        aid = self._pw2_focus_acl or (rows[0].get("id") if rows else "")
        if aid:
            self._pw2_focus_acl = str(aid)
            self._paint_pw2_acl_detail(str(aid))

    @work(thread=True, exclusive=True, group="pw2")
    def _load_pw2_logs(self) -> None:
        from openwrt_cli.services.passwall2 import PassWall2Service

        try:
            data = PassWall2Service(self.device).logs(tail=200).data or {}
            from openwrt_cli.services.passwall2 import log_messages

            lines = log_messages(data.get("entries"))
        except Exception as e:
            self.call_from_thread(self._apply_pw2_log_error, str(e))
            return
        self.call_from_thread(self._apply_pw2_logs, lines)

    def _apply_pw2_logs(self, lines: list[str]) -> None:
        view = self.query_one("#pw2-log", RichLog)
        view.clear()
        q = self._filter.lower()
        for line in lines:
            if q and q not in line.lower():
                continue
            view.write(line)

    def _apply_pw2_log_error(self, message: str) -> None:
        view = self.query_one("#pw2-log", RichLog)
        view.clear()
        view.write(Text(message, style="bold #ed5c5c"))

    def on_unmount(self) -> None:
        self.device.close()
