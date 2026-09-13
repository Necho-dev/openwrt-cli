from __future__ import annotations

import re
from typing import Any, Literal

from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.errors import DeviceCommandError, DeviceConnectionError

_CHECK_ERRORS = (DeviceCommandError, DeviceConnectionError, KeyError, ValueError, TypeError)
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult

Status = Literal["ok", "warn", "fail", "skip"]


def _check(id_: str, title: str, status: Status, details: list[str] | None = None, hint: str | None = None) -> dict[str, Any]:
    return {"id": id_, "title": title, "status": status, "details": details or [], "hint": hint}


class DoctorService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def _safe(self, fn) -> dict[str, Any]:
        try:
            return fn()
        except _CHECK_ERRORS as e:
            return _check(fn.__name__.lstrip("_"), fn.__name__, "skip", [str(e)])

    def run(self, quick: bool = False) -> CommandResult:
        checks: list[dict[str, Any]] = []
        checks.append(self._safe(self._conn))
        if checks[0]["status"] == "fail":
            return CommandResult(
                ok=False,
                data={"checks": checks},
                message=t("doctor.abort"),
                transport=self.device.transport,
                kind="checks",
            )
        if not quick:
            checks.append(self._safe(self._firmware))
        checks.append(self._safe(self._load))
        checks.append(self._safe(self._memory))
        if not quick:
            checks.append(self._safe(self._wifi))
        checks.append(self._safe(self._dhcp))
        if not quick:
            checks.append(self._safe(self._firewall))
        checks.append(self._safe(self._zerotier))
        if not quick:
            checks.append(self._safe(self._wan))
            checks.append(self._safe(self._qos))
        checks.append(self._safe(self._passwall2))

        worst = {c["status"] for c in checks}
        ok = "fail" not in worst
        msg = t("doctor.ok") if ok and "warn" not in worst else t("doctor.warn")
        return CommandResult(
            ok=ok,
            data={"checks": checks},
            message=msg,
            transport=self.device.transport,
            kind="checks",
            degraded=self.device.transport == "http",
            warnings=[t("doctor.http_skip")] if self.device.transport == "http" else [],
        )

    def _conn(self) -> dict[str, Any]:
        if self.device.transport == "http":
            return _check("conn", t("doctor.conn"), "ok", [f"HTTP session @ {self.device.host}"])
        try:
            out = self.device.shell.exec("echo ok").strip()
            if out == "ok":
                return _check("conn", "SSH", "ok", ["echo ok"])
            return _check("conn", "SSH", "fail", [out], t("doctor.hint_ssh"))
        except Exception as e:
            return _check("conn", "SSH", "fail", [str(e)], t("doctor.hint_ssh"))

    def _firmware(self) -> dict[str, Any]:
        try:
            board = self.device.ubus.call("system", "board")
            info = self.device.ubus.call("system", "info")
            hours = float(info.get("uptime") or 0) / 3600
            details = [
                t("doctor.fw_model", v=board.get("model", "?")),
                t("doctor.fw_kernel", v=board.get("kernel", "?")),
                t("doctor.fw_system", v=(board.get("release") or {}).get("description", "?")),
                t("doctor.fw_uptime", hours=hours),
            ]
            status: Status = "warn" if hours > 720 else "ok"
            if hours > 720:
                details.append(t("doctor.uptime_long"))
            return _check("firmware", t("doctor.firmware"), status, details)
        except _CHECK_ERRORS as e:
            return _check("firmware", t("doctor.firmware"), "fail", [str(e)])

    def _load(self) -> dict[str, Any]:
        try:
            info = self.device.ubus.call("system", "info")
            loads = [round(x / 65536.0, 2) for x in (info.get("load") or [0, 0, 0])]
            load1 = loads[0] if loads else 0
            status: Status = "ok"
            details = [t("doctor.load_line", loads=loads)]
            if load1 > 4:
                status = "fail"
                details.append(t("doctor.load_high"))
            elif load1 > 2:
                status = "warn"
                details.append(t("doctor.load_elevated"))
            return _check("load", t("doctor.load"), status, details)
        except _CHECK_ERRORS as e:
            return _check("load", t("doctor.load"), "fail", [str(e)])

    def _memory(self) -> dict[str, Any]:
        try:
            mem = (self.device.ubus.call("system", "info").get("memory") or {})
            total = int(mem.get("total") or 0)
            avail = int(mem.get("available") or mem.get("free") or 0)
            if not total:
                return _check("memory", t("doctor.memory"), "fail", [t("doctor.mem_unreadable")])
            used_pct = (1 - avail / total) * 100
            details = [t("doctor.mem_used", pct=used_pct), t("doctor.mem_avail", mb=avail / 1024 / 1024)]
            status: Status = "ok"
            if used_pct > 90:
                status = "fail"
            elif used_pct > 75:
                status = "warn"
            return _check("memory", t("doctor.memory"), status, details)
        except _CHECK_ERRORS as e:
            return _check("memory", t("doctor.memory"), "fail", [str(e)])

    def _wifi(self) -> dict[str, Any]:
        try:
            wireless = self.device.ubus.call("network.wireless", "status")
            ssid = ""
            try:
                ssid = self.device.uci.get("wireless.@wifi-iface[0].ssid")
            except Exception:
                pass
            enabled = bool(wireless)
            details = [f"wireless status keys: {list(wireless)[:6]}"]
            if ssid:
                details.append(f"SSID: {ssid}")
            return _check("wifi", "WiFi", "ok" if enabled else "warn", details)
        except _CHECK_ERRORS as e:
            return _check(
                "wifi",
                "WiFi",
                "skip",
                [t("doctor.wifi_skip"), str(e)],
                t("doctor.wifi_skip_hint"),
            )

    def _dhcp(self) -> dict[str, Any]:
        try:
            values = self.device.uci.show("dhcp")
        except _CHECK_ERRORS as e:
            return _check("dhcp", "DHCP", "skip", [str(e)])
        lan = None
        for name, sec in (values or {}).items():
            if not isinstance(sec, dict):
                continue
            if (sec.get(".type") or sec.get("type")) != "dhcp":
                continue
            if name == "lan" or sec.get("interface") == "lan":
                lan = sec
                break
        if lan and str(lan.get("ignore", "0")) in {"1", "true", "yes"}:
            return _check("dhcp", "DHCP", "ok", [t("doctor.dhcp_off")])
        from openwrt_cli.services.network import NetworkService
        try:
            leases = NetworkService(self.device).leases()
            n = len((leases.data or {}).get("leases") or [])
            return _check("dhcp", "DHCP", "ok" if n or leases.ok else "warn", [t("doctor.dhcp_leases", n=n)])
        except _CHECK_ERRORS as e:
            return _check("dhcp", "DHCP", "skip", [str(e)])

    def _firewall(self) -> dict[str, Any]:
        if Capability.IPTABLES not in self.device.capabilities:
            try:
                values = self.device.uci.show("firewall")
                return _check("firewall", t("doctor.firewall"), "ok", [t("doctor.fw_uci", n=len(values))], t("doctor.fw_ssh"))
            except _CHECK_ERRORS as e:
                return _check("firewall", t("doctor.firewall"), "skip", [str(e)])
        raw = self.device.shell.exec("iptables -L | wc -l")
        return _check("firewall", t("doctor.firewall"), "ok", [t("doctor.fw_rules", n=raw.strip())])

    def _zerotier(self) -> dict[str, Any]:
        if Capability.SHELL not in self.device.capabilities:
            return _check("zerotier", "ZeroTier", "skip", [t("doctor.need_shell")])
        out = self.device.shell.exec("ps | grep zerotier-one | grep -v grep | wc -l").strip()
        n = int(out) if out.isdigit() else 0
        if n == 0:
            return _check("zerotier", "ZeroTier", "ok", [t("doctor.zt_idle")])
        nets = self.device.shell.exec("zerotier-cli listnetworks 2>/dev/null | tail -n +2").strip()
        return _check("zerotier", "ZeroTier", "ok", [nets or t("doctor.zt_running")])

    def _wan(self) -> dict[str, Any]:
        try:
            dump = self.device.ubus.call("network.interface", "dump")
            wan_ip = ""
            wan_gw = ""
            for iface in dump.get("interface") or []:
                if iface.get("interface") == "wan":
                    addrs = iface.get("ipv4-address") or []
                    wan_ip = addrs[0]["address"] if addrs else ""
                    for r in iface.get("route") or []:
                        if r.get("target") == "0.0.0.0":
                            wan_gw = r.get("nexthop", "")
                            break
            details = [
                t("doctor.wan_ip", ip=wan_ip or t("doctor.unset")),
                t("doctor.wan_gw", gw=wan_gw or t("doctor.unset")),
            ]
            return _check("wan", "WAN", "ok" if wan_ip else "warn", details)
        except _CHECK_ERRORS as e:
            return _check("wan", "WAN", "fail", [str(e)])

    def _qos(self) -> dict[str, Any]:
        try:
            sqm = self.device.uci.show("sqm")
            enabled = any(sec.get("enabled") in ("1", "true", True) for sec in sqm.values())
            return _check("qos", "QoS", "ok", [t("doctor.sqm_on") if enabled or sqm else t("doctor.sqm_off")])
        except _CHECK_ERRORS:
            return _check("qos", "QoS", "ok", [t("doctor.sqm_missing")])

    def _passwall2(self) -> dict[str, Any]:
        from openwrt_cli.services.passwall2 import PassWall2Service

        svc = PassWall2Service(self.device)
        if not svc.available():
            return _check("passwall2", t("doctor.passwall2"), "skip", [t("doctor.pw2_missing")])
        data = (svc.status().data or {})
        details = [
            t("doctor.pw2_node", name=data.get("node_remarks") or data.get("node") or "—"),
            t("doctor.pw2_running", v=t("label.yes") if data.get("running") else t("label.no")),
        ]
        return _check("passwall2", t("doctor.passwall2"), "ok", details)
