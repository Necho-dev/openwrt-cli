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


def test_bandix_set_hostname_only_writes_name():
    from openwrt_cli.core.errors import DeviceCommandError
    from openwrt_cli.services.bandix import BandixService

    BandixService._absent.clear()

    class FakeUbus:
        def __init__(self):
            self.calls: list[tuple] = []
            self.payload: dict = {"success": True}

        def call(self, obj, method, params=None, timeout=None):
            self.calls.append((obj, method, dict(params or {})))
            if method != "setHostname":
                raise DeviceCommandError(method)
            return self.payload

    class FakeDev:
        def __init__(self):
            self.ubus = FakeUbus()

    device = FakeDev()
    svc = BandixService(device)
    out = svc.set_hostname("AA-BB-CC-DD-EE-FF", "RPA试用")
    assert out["mac"] == "aa:bb:cc:dd:ee:ff"
    assert out["hostname"] == "RPA试用"
    assert out["cleared"] is False
    obj, method, params = device.ubus.calls[0]
    assert obj == "luci.bandix"
    assert method == "setHostname"
    assert params == {"mac": "aa:bb:cc:dd:ee:ff", "hostname": "RPA试用"}
    assert "wide_tx_rate_limit" not in params
    assert "wide_rx_rate_limit" not in params

    cleared = svc.set_hostname("aa:bb:cc:dd:ee:ff", "  ")
    assert cleared["cleared"] is True
    assert device.ubus.calls[-1][2]["hostname"] == ""

    device.ubus.payload = {"success": False, "error": "nope"}
    try:
        svc.set_hostname("aa:bb:cc:dd:ee:ff", "x")
        raise AssertionError("expected fail")
    except DeviceCommandError as exc:
        assert "nope" in str(exc)

    denied = FakeDev()

    def _deny(*_a, **_k):
        raise DeviceCommandError("Access denied")

    denied.ubus.call = _deny  # type: ignore[method-assign]
    try:
        BandixService(denied).set_hostname("aa:bb:cc:dd:ee:ff", "x")
        raise AssertionError("expected deny")
    except DeviceCommandError as exc:
        assert "bandix" in str(exc).lower() or "denied" in str(exc).lower()


def test_network_set_hostname_wraps_bandix():
    from openwrt_cli.core.errors import DeviceCommandError
    from openwrt_cli.services.bandix import BandixService
    from openwrt_cli.services.network import NetworkService

    BandixService._absent.clear()

    class FakeUbus:
        def call(self, obj, method, params=None, timeout=None):
            assert obj == "luci.bandix"
            assert method == "setHostname"
            return {"success": True}

    class FakeDev:
        transport = "http"

        def __init__(self):
            self.ubus = FakeUbus()

    result = NetworkService(FakeDev()).set_neighbor_hostname("AA:BB:CC:DD:EE:FF", "desk")
    assert result.ok
    assert result.data["mac"] == "aa:bb:cc:dd:ee:ff"
    assert result.data["hostname"] == "desk"
    assert result.kind == "neigh_hostname"

    class Deny:
        transport = "http"

        def __init__(self):
            self.ubus = type("U", (), {"call": staticmethod(lambda *_a, **_k: (_ for _ in ()).throw(DeviceCommandError("Access denied")))})()

    failed = NetworkService(Deny()).set_neighbor_hostname("aa:bb:cc:dd:ee:ff", "x")
    assert failed.ok is False
    assert "denied" in (failed.message or "").lower() or "拒绝" in (failed.message or "")
