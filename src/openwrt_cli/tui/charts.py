from __future__ import annotations

import math
from dataclasses import dataclass, field

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from openwrt_cli.i18n import t

WINDOW = 60
_BLOCKS = " ▁▂▃▄▅▆▇█"


@dataclass
class Series:
    name: str
    color: str
    values: list[float | None] = field(default_factory=list)


def nice_max(n: float) -> float:
    if n <= 0:
        return 1.0
    exp = math.floor(math.log10(n))
    mag = 10 ** exp
    frac = n / mag
    for step in (1.0, 2.0, 2.5, 5.0, 10.0):
        if frac <= step + 1e-9:
            return step * mag
    return 10.0 * mag


def pad_window(values: list[float] | list[float | None], size: int = WINDOW) -> list[float | None]:
    tail = list(values)[-size:]
    return [None] * (size - len(tail)) + tail


def render_bounded_spark(
    values: list[float] | list[float | None],
    width: int,
    height: int = 2,
    *,
    ymax: float,
    color: str,
    ymin: float = 0.0,
) -> Text:
    """Sparkline with a fixed ymin/ymax (not autoscaled to the window)."""
    pw = max(int(width), 4)
    ph = max(int(height), 1)
    padded = pad_window(values)
    span = ymax - ymin
    if span <= 0:
        span = 1.0
    out = Text()
    for row in range(ph):
        lo = (ph - 1 - row) / ph
        hi = (ph - row) / ph
        for col in range(pw):
            idx = min(int(col * WINDOW / pw), WINDOW - 1)
            val = padded[idx]
            if val is None:
                out.append(" ")
                continue
            frac = max(0.0, min(1.0, (float(val) - ymin) / span))
            if frac <= lo + 1e-9:
                out.append(" ")
            elif frac >= hi - 1e-9:
                out.append("█", style=color)
            else:
                part = (frac - lo) / (hi - lo)
                bi = min(8, max(1, int(round(part * 8))))
                out.append(_BLOCKS[bi], style=color)
        if row < ph - 1:
            out.append("\n")
    return out


def fmt_bytes_rate(bytes_per_sec: float, *, compact: bool = False) -> str:
    n = max(0.0, float(bytes_per_sec)) / 1024.0
    if n < 1024:
        if n < 0.05:
            return "0KB/s"
        if compact and n >= 10:
            return f"{n:.0f}KB/s"
        return f"{n:.1f}KB/s"
    n /= 1024.0
    if n < 1024:
        if compact and n >= 10:
            return f"{n:.0f}MB/s"
        return f"{n:.1f}MB/s"
    return f"{n / 1024.0:.2f}GB/s"


def fmt_axis(n: float, kind: str) -> str:
    if kind == "load":
        s = f"{n:.2f}".rstrip("0").rstrip(".")
        return s or "0"
    if kind == "conn":
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return f"{int(n)}"
    return fmt_bytes_rate(n, compact=True)


def _chart_layout(
    series: list[Series],
    width: int,
    height: int,
    kind: str,
    *,
    ymax: float | None = None,
    axis_w: int | None = None,
    reserve_x: bool = True,
):
    axis_w = 8 if kind in {"bw", "bandix"} else 6 if axis_w is None else axis_w
    pw = max(int(width) - axis_w, 10)
    ph = max(int(height) - (1 if reserve_x else 0), 3)
    padded = [(s.name, s.color, pad_window(s.values)) for s in series]
    peak = 0.0
    for _, _, vals in padded:
        for v in vals:
            if v is not None:
                peak = max(peak, float(v))
    return axis_w, pw, ph, padded, nice_max(ymax if ymax is not None else peak)


def _sample(vals: list[float | None], col: int, pw: int) -> float | None:
    idx = min(int(col * WINDOW / max(pw, 1)), WINDOW - 1)
    val = vals[idx]
    if val is None:
        return None
    return float(val)


def _row_of(val: float, ymax: float, ph: int) -> int:
    frac = 0.0 if ymax <= 0 else max(0.0, min(1.0, val / ymax))
    return max(0, min(ph - 1, ph - 1 - int(round(frac * (ph - 1)))))


def _paint_axes(
    grid: list[list[tuple[str, str]]],
    pw: int,
    ph: int,
    ymax: float,
    kind: str,
    axis_w: int,
    *,
    show_x: bool = True,
    y_labels: dict[int, str] | None = None,
) -> Text:
    labels = y_labels if y_labels is not None else {
        0: fmt_axis(ymax, kind),
        max(ph // 2, 1): fmt_axis(ymax / 2, kind),
        ph - 1: fmt_axis(0, kind),
    }
    out = Text()
    for r in range(ph):
        out.append(f"{labels.get(r, ''):>{axis_w - 1}} ", style="dim")
        for col in range(pw):
            ch, st = grid[r][col]
            out.append(ch, style=st or "")
        out.append("\n")
    if show_x:
        out.append(" " * axis_w)
        left, mid, right = ("1h", "30m", "now") if kind == "bandix" else ("3m", "1m", "now")
        out.append(left, style="dim")
        gap = max(pw - len(left) - len(right), 2)
        out.append(" " * (gap // 2))
        out.append(mid, style="dim")
        out.append(" " * max(gap - gap // 2 - len(mid), 1))
        out.append(right, style="dim")
    return out


def render_area(
    series: list[Series],
    width: int,
    height: int,
    *,
    kind: str = "load",
    ymax: float | None = None,
    show_x: bool = True,
    axis_w: int | None = None,
    y_labels: dict[int, str] | None = None,
) -> Text:
    """Filled area; best for one series (later series hide earlier ones)."""
    axis_w, pw, ph, padded, ymax = _chart_layout(
        series, width, height, kind, ymax=ymax, axis_w=axis_w, reserve_x=show_x,
    )

    grid: list[list[tuple[str, str]]] = [[(" ", "")] * pw for _ in range(ph)]
    # Paint later series first so 1m / inbound sit on top (LuCI-style).
    for _, color, vals in reversed(padded):
        for col in range(pw):
            idx = min(int(col * WINDOW / pw), WINDOW - 1)
            val = vals[idx]
            if val is None:
                continue
            frac = 0.0 if ymax <= 0 else max(0.0, min(1.0, float(val) / ymax))
            cells = frac * ph
            full = int(cells)
            rem = cells - full
            for r in range(full):
                grid[ph - 1 - r][col] = ("█", color)
            if full < ph and rem > 0.04:
                bi = min(8, max(1, int(round(rem * 8))))
                top = ph - 1 - full
                grid[top][col] = (_BLOCKS[bi], color)

    return _paint_axes(grid, pw, ph, ymax, kind, axis_w, show_x=show_x, y_labels=y_labels)


def render_line(series: list[Series], width: int, height: int, *, kind: str = "load") -> Text:
    """Polyline: series stay visible together on Load / Bandwidth / Connections."""
    axis_w, pw, ph, padded, ymax = _chart_layout(series, width, height, kind)
    grid: list[list[tuple[str, str]]] = [[(" ", "")] * pw for _ in range(ph)]
    for _, color, vals in reversed(padded):
        prev: int | None = None
        for col in range(pw):
            val = _sample(vals, col, pw)
            if val is None:
                prev = None
                continue
            row = _row_of(val, ymax, ph)
            if prev is None or prev == row:
                grid[row][col] = ("•", color)
            else:
                lo, hi = (row, prev) if row < prev else (prev, row)
                for r in range(lo, hi + 1):
                    if lo < r < hi:
                        grid[r][col] = ("│", color)
                grid[row][col] = ("╱" if row < prev else "╲", color)
            prev = row
    return _paint_axes(grid, pw, ph, ymax, kind, axis_w)


def _legend_num(n: float, kind: str) -> str:
    if kind == "load":
        return f"{n:.2f}"
    if kind == "conn":
        return f"{int(round(n))}"
    return fmt_bytes_rate(n)


def render_legend(series: list[Series], *, kind: str = "load") -> Table:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(ratio=2)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    for s in series:
        nums = [float(v) for v in s.values if v is not None]
        cur = nums[-1] if nums else 0.0
        avg = sum(nums) / len(nums) if nums else 0.0
        peak = max(nums) if nums else 0.0
        name = Text()
        name.append("━ ", style=s.color)
        name.append(s.name)
        table.add_row(
            name,
            Text(_legend_num(cur, kind), style="bold"),
            Text(f"{t('legend.average')} {_legend_num(avg, kind)}"),
            Text(f"{t('legend.peak')} {_legend_num(peak, kind)}"),
        )
    return table


def render_stack(series: list[Series], width: int, height: int, *, kind: str = "conn") -> Text:
    """Stacked area; Connections = UDP + TCP + Other."""
    axis_w, pw, ph, padded, _ = _chart_layout(series, width, height, kind)
    totals = [0.0] * WINDOW
    for _, _, vals in padded:
        for i, v in enumerate(vals):
            if v is not None:
                totals[i] += float(v)
    ymax = nice_max(max(totals) if totals else 0.0)
    grid: list[list[tuple[str, str]]] = [[(" ", "")] * pw for _ in range(ph)]
    for col in range(pw):
        idx = min(int(col * WINDOW / pw), WINDOW - 1)
        acc = 0.0
        layers: list[tuple[float, float, str]] = []
        # 图例仍是 UDP / TCP / Other；堆叠从下往上 Other → TCP → UDP，贴近 LuCI
        for _, color, vals in reversed(padded):
            val = vals[idx]
            if val is None:
                continue
            lo, hi = acc, acc + float(val)
            acc = hi
            layers.append((lo, hi, color))
        for lo, hi, color in layers:
            r0 = _row_of(hi, ymax, ph)
            r1 = _row_of(lo, ymax, ph)
            if r0 > r1:
                r0, r1 = r1, r0
            for r in range(r0, min(ph, r1 + 1)):
                grid[r][col] = ("█", color)
    return _paint_axes(grid, pw, ph, ymax, kind, axis_w)


_BRAILLE = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


def render_braille(series: list[Series], width: int, height: int, *, kind: str = "load") -> Text:
    """Braille 高密折线：每字符 2×4 点，比普通折线细，仍可能重叠。"""
    axis_w, pw, ph, padded, ymax = _chart_layout(series, width, height, kind)
    dots_w, dots_h = pw * 2, ph * 4
    canvas = [[0] * dots_w for _ in range(dots_h)]
    colors = [[""] * dots_w for _ in range(dots_h)]
    for _, color, vals in reversed(padded):
        prev: tuple[int, int] | None = None
        for x in range(dots_w):
            val = _sample(vals, min(x // 2, pw - 1), pw)
            if val is None:
                prev = None
                continue
            frac = 0.0 if ymax <= 0 else max(0.0, min(1.0, val / ymax))
            y = max(0, min(dots_h - 1, dots_h - 1 - int(round(frac * (dots_h - 1)))))
            if prev is None:
                canvas[y][x] |= 1
                colors[y][x] = color
            else:
                x0, y0 = prev
                steps = max(abs(x - x0), abs(y - y0), 1)
                for i in range(steps + 1):
                    xx = x0 + (x - x0) * i // steps
                    yy = y0 + (y - y0) * i // steps
                    canvas[yy][xx] |= 1
                    colors[yy][xx] = color
            prev = (x, y)
    grid: list[list[tuple[str, str]]] = [[(" ", "")] * pw for _ in range(ph)]
    for r in range(ph):
        for c in range(pw):
            bits = 0
            color = ""
            for dy, pair in enumerate(_BRAILLE):
                for dx, bit in enumerate(pair):
                    yy, xx = r * 4 + dy, c * 2 + dx
                    if yy < dots_h and xx < dots_w and canvas[yy][xx]:
                        bits |= bit
                        color = colors[yy][xx] or color
            grid[r][c] = (chr(0x2800 + bits) if bits else " ", color)
    return _paint_axes(grid, pw, ph, ymax, kind, axis_w)


_RIDGE_STRIP = 3


def _ridge_label(name: str, index: int) -> str:
    tag = name.split()[0] if name else str(index + 1)
    if tag.lower() in {"1", "5", "15"} or tag.endswith("m"):
        return tag if tag.endswith("m") else f"{tag}m"
    if "1" in name and "Minute" in name:
        return "1m"
    if "5" in name:
        return "5m"
    if "15" in name:
        return "15m"
    return tag[:3]


def render_ridge(series: list[Series], width: int, height: int, *, kind: str = "load") -> Text:
    """分轨面积：矮条紧贴（不把剩余高度均分），共用纵轴，观感接近堆叠但数值不累加。"""
    if not series:
        return Text()
    n = len(series)
    body = max(int(height) - 1, n * 2)
    strip = _RIDGE_STRIP
    if body < n * strip:
        strip = max(2, body // n)
    out = Text()
    for i, s in enumerate(series):
        # 每轨按自身峰值铺满预留行高，避免共用 ymax 时矮轨上方空一大截
        peak = 0.0
        for v in s.values:
            if v is not None:
                peak = max(peak, float(v))
        out.append_text(render_area(
            [s], width, strip, kind=kind, ymax=nice_max(peak), show_x=False, axis_w=4,
            y_labels={0: _ridge_label(s.name, i)},
        ))
    axis_w = 4
    pw = max(int(width) - axis_w, 10)
    out.append(" " * axis_w)
    out.append("3m", style="dim")
    gap = max(pw - 8, 2)
    out.append(" " * (gap // 2))
    out.append("1m", style="dim")
    out.append(" " * max(gap - gap // 2 - 3, 1))
    out.append("now", style="dim")
    return out


class AreaChart(Static):
    """随容器尺寸重绘。默认叠涂面积（LuCI 风格）。"""

    DEFAULT_CSS = "AreaChart { width: 1fr; height: 1fr; overflow: hidden; }"

    def __init__(self, kind: str = "load", style: str = "area", **kwargs) -> None:
        super().__init__(**kwargs)
        self.kind = kind
        self.style = style
        self._series: list[Series] = []

    def set_series(self, series: list[Series]) -> None:
        self._series = series
        self.refresh()

    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        if w < 12 or h < 4:
            return Text()
        try:
            if self.style == "line":
                return render_line(self._series, w, h, kind=self.kind)
            if self.style == "stack":
                return render_stack(self._series, w, h, kind=self.kind)
            if self.style == "braille":
                return render_braille(self._series, w, h, kind=self.kind)
            if self.style == "ridge":
                return render_ridge(self._series, w, h, kind=self.kind)
            return render_area(self._series, w, h, kind=self.kind)
        except Exception:
            return Text()
