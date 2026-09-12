from __future__ import annotations

from datetime import datetime

from openwrt_cli.services.bandix import downsample_metric_points, downsample_metrics
from openwrt_cli.services.system import parse_log_time


def test_parse_relative_and_calendar():
    now = datetime(2026, 9, 12, 20, 0, 0)
    assert parse_log_time("10m", now=now) == datetime(2026, 9, 12, 19, 50, 0)
    assert parse_log_time("2h", now=now) == datetime(2026, 9, 12, 18, 0, 0)
    assert parse_log_time("2026-09-11 20:00:00") == datetime(2026, 9, 11, 20, 0, 0)


def test_downsample_keeps_peaks():
    points = [{"ts": i, "rx_bps": float(i), "tx_bps": float(100 - i)} for i in range(100)]
    sampled = downsample_metric_points(points, 10)
    assert len(sampled) == 10
    rx, tx = downsample_metrics(points, 10)
    assert len(rx) == 10
    assert max(rx) == max(p["rx_bps"] for p in points)
    short = downsample_metric_points(points[:3], 10)
    assert len(short) == 3
