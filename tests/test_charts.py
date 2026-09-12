from __future__ import annotations

from openwrt_cli.tui.charts import Series, fmt_bytes_rate, nice_max, pad_window, render_legend
from openwrt_cli.tui.bandwidth import C_RX, C_TX, DualRateChart


def test_nice_max_and_rate():
    assert nice_max(0) == 1.0
    assert nice_max(977_000) in {1_000_000.0, 1e6}
    assert fmt_bytes_rate(0) == "0KB/s"
    assert "KB/s" in fmt_bytes_rate(977 * 1024)


def test_pad_window():
    padded = pad_window([1.0, 2.0], size=5)
    assert padded == [None, None, None, 1.0, 2.0]


def test_legend_and_dual_rate_colors():
    assert C_RX != C_TX
    series = [
        Series("Download", C_RX, [1000.0, 2000.0]),
        Series("Upload", C_TX, [100.0, 200.0]),
    ]
    table = render_legend(series, kind="bandix")
    assert table.row_count == 2
    chart = DualRateChart()
    assert ".dr-spark" in DualRateChart.DEFAULT_CSS
    assert hasattr(chart, "set_rates")
