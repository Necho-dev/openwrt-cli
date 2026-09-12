from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from openwrt_cli.i18n import t
from openwrt_cli.tui.charts import Series, nice_max, render_bounded_spark

C_LOAD1, C_LOAD5, C_LOAD15 = "#f2a07a", "#e8b86d", "#d8c56a"

_TRACKS = (
    ("1m", C_LOAD1),
    ("5m", C_LOAD5),
    ("15m", C_LOAD15),
)
_SPARK_H = 2


class LoadPanel(Static):
    """1m / 5m / 15m sparklines sharing a 0-based Y axis."""

    DEFAULT_CSS = """
    LoadPanel {
        width: 1fr;
        height: 9;
        overflow: hidden;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._series: list[Series] = []

    def set_series(self, series: list[Series]) -> None:
        self._series = series
        self.refresh()

    def render(self) -> Text:
        peak = 0.0
        for s in self._series:
            for v in s.values:
                if v is not None:
                    peak = max(peak, float(v))
        # Load average is a queue length, not 0–1 utilization.
        ymax = max(1.0, nice_max(peak))
        width = max(self.size.width, 12)
        out = Text()
        for i, (key, color) in enumerate(_TRACKS):
            s = self._series[i] if i < len(self._series) else None
            vals = [float(v) for v in (s.values if s else []) if v is not None]
            cur = vals[-1] if vals else 0.0
            avg = sum(vals) / len(vals) if vals else 0.0
            pk = max(vals) if vals else 0.0
            head = Text()
            head.append(f"{key:<4}", style=f"bold {color}")
            head.append(f"{cur:.2f}", style="bold #f2f6fb")
            head.append(f"   {t('legend.average')} ", style="dim")
            head.append(f"{avg:.2f}", style="#f2f6fb")
            head.append(f"   {t('legend.peak')} ", style="dim")
            head.append(f"{pk:.2f}", style="#f2f6fb")
            out.append_text(head)
            out.append("\n")
            out.append_text(render_bounded_spark(
                list(s.values) if s else [], width, _SPARK_H, ymax=ymax, color=color,
            ))
            if i < len(_TRACKS) - 1:
                out.append("\n")
        return out
