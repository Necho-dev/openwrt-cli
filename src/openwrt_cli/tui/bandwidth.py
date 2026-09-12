from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Sparkline, Static

from openwrt_cli.i18n import t
from openwrt_cli.tui.charts import fmt_bytes_rate

C_RX, C_TX = "#4c8dff", "#67c23a"
C_RX_DIM, C_TX_DIM = "#1e4a7a", "#2a5a32"
_MAX_IFACES = 4


def _nums(values: list[float] | list[float | None]) -> list[float]:
    return [0.0 if v is None else max(0.0, float(v)) for v in values]


def _chip(text: str, fg: str, bg: str) -> Text:
    return Text(f" {text} ", style=f"bold {fg} on {bg}")


def _iface_chip(name: str) -> Text:
    return _chip(name, "#e8f4ff", "#1e4a7a")


def _role_chip(role: str, name: str) -> Text | None:
    key = (role or name or "").lower()
    if key.startswith("wan"):
        return _chip("WAN", "#1a1204", "#f0a000")
    if key.startswith("lan"):
        return _chip("LAN", "#e8f4ff", "#1565a8")
    if role and role.lower() not in {name.lower(), "loopback"}:
        return _chip(role.upper(), "#e8f4ff", "#2a4660")
    return None


class IfaceBandwidth(Vertical):
    """单块物理网卡：标题 + 入/出 Sparkline + 速率。"""

    DEFAULT_CSS = """
    IfaceBandwidth {
        width: 1fr;
        height: 6;
        padding: 0;
        overflow: hidden;
    }
    IfaceBandwidth.bw-split {
        height: 7;
        border-top: solid #2a4660;
        padding-top: 1;
    }
    IfaceBandwidth .bw-head { height: 1; }
    IfaceBandwidth .bw-spark { width: 1fr; height: 2; }
    IfaceBandwidth .bw-meta { height: 1; }
    """

    def __init__(self, iface: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.iface_name = iface
        self._role = ""
        self._rx: list[float] = []
        self._tx: list[float] = []

    def compose(self) -> ComposeResult:
        yield Static("", classes="bw-head")
        yield Sparkline(
            [],
            summary_function=max,
            min_color=C_RX_DIM,
            max_color=C_RX,
            classes="bw-spark bw-rx",
        )
        yield Sparkline(
            [],
            summary_function=max,
            min_color=C_TX_DIM,
            max_color=C_TX,
            classes="bw-spark bw-tx",
        )
        yield Static("", classes="bw-meta")

    def on_mount(self) -> None:
        if self._rx or self._tx or self._role:
            self._paint()

    def update_data(self, role: str, rx: list[float], tx: list[float]) -> None:
        self._role = role
        self._rx = list(rx)
        self._tx = list(tx)
        if self.is_attached and self.query(Sparkline):
            self._paint()

    def _paint(self) -> None:
        rx_n, tx_n = _nums(self._rx), _nums(self._tx)
        self.query_one(".bw-rx", Sparkline).data = rx_n
        self.query_one(".bw-tx", Sparkline).data = tx_n
        rx_cur = rx_n[-1] if rx_n else 0.0
        tx_cur = tx_n[-1] if tx_n else 0.0
        rx_avg = sum(rx_n) / len(rx_n) if rx_n else 0.0
        tx_avg = sum(tx_n) / len(tx_n) if tx_n else 0.0
        rx_peak = max(rx_n) if rx_n else 0.0
        tx_peak = max(tx_n) if tx_n else 0.0
        t = Text()
        t.append_text(_iface_chip(self.iface_name))
        role = _role_chip(self._role, self.iface_name)
        if role:
            t.append(" ")
            t.append_text(role)
        t.append("   ", style="dim")
        t.append("↓", style=f"bold {C_RX}")
        t.append(fmt_bytes_rate(rx_cur), style=f"bold {C_RX}")
        t.append("  ", style="dim")
        t.append("↑", style=f"bold {C_TX}")
        t.append(fmt_bytes_rate(tx_cur), style=f"bold {C_TX}")
        self.query_one(".bw-head", Static).update(t)
        meta = Text()
        meta.append("avg ", style="dim")
        meta.append(f"↓{fmt_bytes_rate(rx_avg)}", style=C_RX)
        meta.append("  ", style="dim")
        meta.append(f"↑{fmt_bytes_rate(tx_avg)}", style=C_TX)
        meta.append("   peak ", style="dim")
        meta.append(f"↓{fmt_bytes_rate(rx_peak)}", style=C_RX)
        meta.append("  ", style="dim")
        meta.append(f"↑{fmt_bytes_rate(tx_peak)}", style=C_TX)
        self.query_one(".bw-meta", Static).update(meta)


class DualRateChart(Vertical):
    """Download over upload, same sparkline language as Overview bandwidth."""

    DEFAULT_CSS = """
    DualRateChart {
        width: 1fr;
        height: 1fr;
        overflow: hidden;
        layout: vertical;
    }
    DualRateChart .dr-head { height: 1; }
    DualRateChart .dr-spark { width: 1fr; height: 1fr; }
    DualRateChart .dr-axis { height: 1; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rx: list[float] = []
        self._tx: list[float] = []
        self._x_labels = ("1h", "30m", "now")

    def compose(self) -> ComposeResult:
        yield Static("", classes="dr-head dr-rx-head")
        yield Sparkline(
            [],
            summary_function=max,
            min_color=C_RX_DIM,
            max_color=C_RX,
            classes="dr-spark dr-rx",
        )
        yield Static("", classes="dr-head dr-tx-head")
        yield Sparkline(
            [],
            summary_function=max,
            min_color=C_TX_DIM,
            max_color=C_TX,
            classes="dr-spark dr-tx",
        )
        yield Static("", classes="dr-axis")

    def on_mount(self) -> None:
        if self._rx or self._tx:
            self._paint()
        else:
            self._paint_axis()

    def on_resize(self) -> None:
        self._paint_axis()

    def set_rates(self, rx: list[float], tx: list[float]) -> None:
        self._rx = list(rx)
        self._tx = list(tx)
        if self.is_attached and self.query(Sparkline):
            self._paint()

    def _paint(self) -> None:
        rx_n, tx_n = _nums(self._rx), _nums(self._tx)
        self.query_one(".dr-rx", Sparkline).data = rx_n
        self.query_one(".dr-tx", Sparkline).data = tx_n
        self._paint_head(".dr-rx-head", "↓", t("legend.download"), C_RX, rx_n)
        self._paint_head(".dr-tx-head", "↑", t("legend.upload"), C_TX, tx_n)
        self._paint_axis()

    def _paint_head(self, selector: str, arrow: str, name: str, color: str, values: list[float]) -> None:
        cur = values[-1] if values else 0.0
        avg = sum(values) / len(values) if values else 0.0
        peak = max(values) if values else 0.0
        line = Text()
        line.append(f"{arrow} ", style=f"bold {color}")
        line.append(name, style=f"bold {color}")
        line.append("  ", style="dim")
        line.append(fmt_bytes_rate(cur), style=f"bold {color}")
        line.append(f"   {t('legend.average')} ", style="dim")
        line.append(fmt_bytes_rate(avg), style=color)
        line.append(f"   {t('legend.peak')} ", style="dim")
        line.append(fmt_bytes_rate(peak), style=color)
        self.query_one(selector, Static).update(line)

    def _paint_axis(self) -> None:
        left, mid, right = self._x_labels
        width = max(int(self.size.width), len(left) + len(mid) + len(right) + 4)
        axis = Text()
        axis.append(left, style="dim")
        gap = max(width - len(left) - len(right), 2)
        axis.append(" " * (gap // 2))
        axis.append(mid, style="dim")
        axis.append(" " * max(gap - gap // 2 - len(mid), 1))
        axis.append(right, style="dim")
        try:
            self.query_one(".dr-axis", Static).update(axis)
        except Exception:
            pass


class BandwidthPanel(Vertical):
    """物理网卡纵向排布：eth0 WAN 在上，eth1 LAN 在下。"""

    DEFAULT_CSS = """
    BandwidthPanel {
        width: 1fr;
        height: auto;
        overflow: hidden;
        layout: vertical;
    }
    """

    def set_ifaces(self, items: list[tuple[str, str, list[float], list[float]]]) -> None:
        items = items[:_MAX_IFACES]
        wanted = [name for name, *_ in items]
        have = {w.iface_name: w for w in self.query(IfaceBandwidth)}
        for widget in list(have.values()):
            if widget.iface_name not in wanted:
                widget.remove()
        have = {w.iface_name: w for w in self.query(IfaceBandwidth)}
        for index, (name, role, rx, tx) in enumerate(items):
            widget = have.get(name)
            if widget is None:
                widget = IfaceBandwidth(name)
                self.mount(widget)
            widget.set_class(index > 0, "bw-split")
            widget.update_data(role, rx, tx)
        if not items and not have:
            if not self.query("#bw-empty"):
                self.mount(Static(t("empty.nics"), id="bw-empty"))
        elif items:
            for empty in self.query("#bw-empty"):
                empty.remove()
