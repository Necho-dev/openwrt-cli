"""Optional luci-app-bandix probe and per-device traffic/connection reads."""

from __future__ import annotations

import ipaddress
from typing import Any
from weakref import WeakSet

from openwrt_cli.core.device import DeviceClient
from openwrt_cli.core.errors import DeviceCommandError, DeviceConnectionError
from openwrt_cli.core.mac_vendor import lookup_enhanced
from openwrt_cli.i18n import t

_ABSENT_HINTS = (
    "object not found",
    "not found",
    "method not found",
    "unknown object",
    "no such file",
    "access denied",
)


def norm_mac(mac: str | None) -> str:
    return (mac or "").strip().lower().replace("-", ":")


def _is_absent(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _ABSENT_HINTS)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _pick(row: dict[str, Any], *keys, default=None):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _device_list(payload) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict) or payload.get("success") is False:
        return []
    for blob in (payload, payload.get("data")):
        if isinstance(blob, list):
            return [row for row in blob if isinstance(row, dict)]
        if not isinstance(blob, dict):
            continue
        for key in ("devices", "d"):
            value = blob.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def parse_status_devices(payload) -> list[dict[str, Any]]:
    """Accept both long keys (devices/local_rx_*) and short keys (d/l_rx_b)."""
    out: list[dict[str, Any]] = []
    for row in _device_list(payload):
        out.append({
            **row,
            "mac": row.get("mac") or row.get("mac_address") or "",
            "ip": row.get("ip") or row.get("ip4") or row.get("ipaddr") or "",
            "hostname": row.get("hostname") or row.get("host") or "",
            "local_rx_bytes": _pick(row, "local_rx_bytes", "l_rx_b", default=0),
            "local_tx_bytes": _pick(row, "local_tx_bytes", "l_tx_b", default=0),
            "wide_rx_bytes": _pick(row, "wide_rx_bytes", "w_rx_b", default=0),
            "wide_tx_bytes": _pick(row, "wide_tx_bytes", "w_tx_b", default=0),
            "local_rx_rate": _pick(row, "local_rx_rate", "l_rx_r", default=0),
            "local_tx_rate": _pick(row, "local_tx_rate", "l_tx_r", default=0),
            "wide_rx_rate": _pick(row, "wide_rx_rate", "w_rx_r", default=0),
            "wide_tx_rate": _pick(row, "wide_tx_rate", "w_tx_r", default=0),
        })
    return out


def parse_connection_devices(payload) -> dict[str, dict[str, Any]]:
    blob = payload
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return {}
        inner = payload.get("data")
        if isinstance(inner, dict):
            blob = inner
    devices = None
    if isinstance(blob, dict):
        devices = blob.get("devices") if isinstance(blob.get("devices"), list) else blob.get("d")
    out: dict[str, dict[str, Any]] = {}
    for row in devices or []:
        if not isinstance(row, dict):
            continue
        mac = norm_mac(row.get("mac_address") or row.get("mac"))
        if not mac:
            continue
        out[mac] = {
            **row,
            "mac_address": mac,
            "ip_address": row.get("ip_address") or row.get("ip4") or row.get("ip") or "",
            "hostname": row.get("hostname") or row.get("host") or "",
            "tcp_connections": _pick(row, "tcp_connections", "tcp", default=0),
            "udp_connections": _pick(row, "udp_connections", "udp", default=0),
            "total_connections": _pick(row, "total_connections", "total", default=0),
            "established_tcp": _pick(row, "established_tcp", "tcp_est", default=0),
            "time_wait_tcp": _pick(row, "time_wait_tcp", "tcp_tw", default=0),
            "close_wait_tcp": _pick(row, "close_wait_tcp", "tcp_cw", default=0),
        }
    return out


def merge_neighbor_rows(
    arp_rows: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    connections: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge ARP neighbors with Bandix devices/connections by MAC."""
    by_mac: dict[str, dict[str, Any]] = {}
    leftover: list[dict[str, Any]] = []
    for row in arp_rows:
        mac = norm_mac(row.get("mac"))
        if not mac:
            leftover.append(dict(row))
            continue
        item = dict(row)
        item["mac"] = mac
        by_mac[mac] = item
    for dev in devices:
        mac = norm_mac(dev.get("mac"))
        if not mac:
            continue
        dst = by_mac.setdefault(mac, {"mac": mac, "ip": "", "device": "", "hostname": ""})
        ip = dev.get("ip") or ""
        if ip and not dst.get("ip"):
            dst["ip"] = ip
        hostname = dev.get("hostname") or ""
        if hostname:
            dst["hostname"] = hostname
        if "online" in dev:
            dst["online"] = bool(dev.get("online"))
        dst["lan_rx_bytes"] = _as_int(dev.get("local_rx_bytes"))
        dst["lan_tx_bytes"] = _as_int(dev.get("local_tx_bytes"))
        dst["wan_rx_bytes"] = _as_int(dev.get("wide_rx_bytes"))
        dst["wan_tx_bytes"] = _as_int(dev.get("wide_tx_bytes"))
        dst["lan_rx_bps"] = _as_float(dev.get("local_rx_rate"))
        dst["lan_tx_bps"] = _as_float(dev.get("local_tx_rate"))
        dst["wan_rx_bps"] = _as_float(dev.get("wide_rx_rate"))
        dst["wan_tx_bps"] = _as_float(dev.get("wide_tx_rate"))
        dst["rx_bytes"] = dst["lan_rx_bytes"] + dst["wan_rx_bytes"]
        dst["tx_bytes"] = dst["lan_tx_bytes"] + dst["wan_tx_bytes"]
        dst["rx_bps"] = dst["lan_rx_bps"] + dst["wan_rx_bps"]
        dst["tx_bps"] = dst["lan_tx_bps"] + dst["wan_tx_bps"]
    for mac, conn in connections.items():
        dst = by_mac.setdefault(mac, {"mac": mac, "ip": "", "device": "", "hostname": ""})
        if conn.get("ip_address") and not dst.get("ip"):
            dst["ip"] = conn.get("ip_address")
        if conn.get("hostname") and not dst.get("hostname"):
            dst["hostname"] = conn.get("hostname")
        dst["tcp"] = _as_int(conn.get("tcp_connections"))
        dst["udp"] = _as_int(conn.get("udp_connections"))
        dst["conns"] = _as_int(conn.get("total_connections"), dst.get("tcp", 0) + dst.get("udp", 0))
        dst["tcp_established"] = _as_int(conn.get("established_tcp"))
        dst["tcp_time_wait"] = _as_int(conn.get("time_wait_tcp"))
        dst["tcp_close_wait"] = _as_int(conn.get("close_wait_tcp"))
    rows = leftover + list(by_mac.values())
    for row in rows:
        mac = row.get("mac") or ""
        if mac and not row.get("vendor"):
            info = lookup_enhanced(mac)
            row["vendor"] = info["vendor"]
            row["device_type"] = info["type"]
    rows.sort(key=lambda r: _ip_sort_key(r.get("ip", "")))
    return rows


def parse_metrics(payload) -> dict[str, Any]:
    """Parse getMetrics. Current points are [ts, tot_rx_r, tot_tx_r, ...]."""
    blob = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        blob = payload["data"]
    if not isinstance(blob, dict):
        return {"mac": "", "retention_seconds": 0, "points": []}
    points: list[dict[str, Any]] = []
    for item in blob.get("metrics") or blob.get("m") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            points.append({
                "ts": _as_int(item[0]),
                "rx_bps": _as_float(item[1]),
                "tx_bps": _as_float(item[2]),
            })
            continue
        if not isinstance(item, dict):
            continue
        points.append({
            "ts": _as_int(item.get("ts_ms") or item.get("timestamp")),
            "rx_bps": _as_float(
                _pick(item, "total_rx_rate", "rx_rate", "rx_speed", default=0)
            ),
            "tx_bps": _as_float(
                _pick(item, "total_tx_rate", "tx_rate", "tx_speed", default=0)
            ),
        })
    return {
        "mac": str(blob.get("mac") or ""),
        "retention_seconds": _as_int(blob.get("retention_seconds")),
        "points": points,
    }


def downsample_metric_points(points: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """Downsample by taking peak rx/tx per bucket; keep the last timestamp."""
    if not points or len(points) <= size:
        return [dict(p) for p in points]
    out: list[dict[str, Any]] = []
    step = len(points) / size
    for i in range(size):
        start = int(i * step)
        end = max(start + 1, int((i + 1) * step))
        chunk = points[start:end]
        out.append({
            "ts": _as_int(chunk[-1].get("ts")),
            "rx_bps": max((float(p.get("rx_bps") or 0) for p in chunk), default=0.0),
            "tx_bps": max((float(p.get("tx_bps") or 0) for p in chunk), default=0.0),
        })
    return out


def downsample_metrics(points: list[dict[str, Any]], size: int) -> tuple[list[float], list[float]]:
    sampled = downsample_metric_points(points, size)
    return (
        [float(p.get("rx_bps") or 0) for p in sampled],
        [float(p.get("tx_bps") or 0) for p in sampled],
    )


def _ip_sort_key(ip: str) -> tuple:
    try:
        return (0, ipaddress.ip_address(ip).packed)
    except ValueError:
        return (1, (ip or "").encode())


class BandixService:
    """Probe luci.bandix; cache absence per device so we do not retry forever."""

    _absent: WeakSet[Any] = WeakSet()

    def __init__(self, device: DeviceClient):
        self.device = device

    def available(self) -> bool:
        return self.device not in self._absent

    def overlay(self) -> dict[str, Any] | None:
        """Return {devices, connections} when present; None if missing/denied."""
        if not self.available():
            return None
        try:
            raw = self.device.ubus.call("luci.bandix", "getStatus", timeout=5)
        except DeviceCommandError as e:
            if _is_absent(e):
                self._absent.add(self.device)
            return None
        except DeviceConnectionError:
            return None
        devices = parse_status_devices(raw)
        connections: dict[str, dict[str, Any]] = {}
        try:
            connections = parse_connection_devices(
                self.device.ubus.call("luci.bandix", "getConnection", timeout=5)
            )
        except (DeviceCommandError, DeviceConnectionError):
            connections = {}
        if not devices and not connections:
            return None
        return {"devices": devices, "connections": connections}

    def metrics(self, mac: str | None = None) -> dict[str, Any] | None:
        """History for all devices or one MAC. None if the plugin is missing."""
        if not self.available():
            return None
        key = norm_mac(mac)
        params = {"mac": key} if key else {"mac": "all"}
        try:
            raw = self.device.ubus.call("luci.bandix", "getMetrics", params, timeout=6)
        except DeviceCommandError as e:
            if _is_absent(e):
                self._absent.add(self.device)
            return None
        except DeviceConnectionError:
            return None
        return parse_metrics(raw)

    def set_hostname(self, mac: str, hostname: str) -> dict[str, Any]:
        """Bind or clear a Bandix hostname. Empty hostname removes the binding."""
        key = norm_mac(mac)
        if not key:
            raise DeviceCommandError(t("msg.neigh_pick"))
        if not self.available():
            raise DeviceCommandError(t("err.no_bandix"))
        name = str(hostname or "").strip()
        try:
            raw = self.device.ubus.call(
                "luci.bandix",
                "setHostname",
                {"mac": key, "hostname": name},
                timeout=12,
            )
        except DeviceCommandError as e:
            text = str(e).lower()
            if "access denied" in text or "permission denied" in text:
                raise DeviceCommandError(t("err.bandix_write_denied")) from e
            if _is_absent(e):
                self._absent.add(self.device)
                raise DeviceCommandError(t("err.no_bandix")) from e
            raise
        if isinstance(raw, dict) and raw.get("success") in {0, False}:
            raise DeviceCommandError(
                str(raw.get("error") or raw.get("message") or t("err.neigh_rename"))
            )
        return {"mac": key, "hostname": name, "cleared": not name}
