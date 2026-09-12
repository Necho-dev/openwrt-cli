from __future__ import annotations

from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.errors import DeviceCommandError
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult


class QoSService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def status(self) -> CommandResult:
        warnings: list[str] = []
        sqm = {}
        try:
            sqm = self.device.uci.show("sqm")
        except (DeviceCommandError, KeyError):
            sqm = {}
        data: dict = {"sqm": sqm}
        if Capability.SHELL in self.device.capabilities:
            sqm_status = self.device.shell.exec("/etc/init.d/sqm status 2>/dev/null || echo sqm_not_found")
            data["sqm_enabled"] = "active" in sqm_status.lower() or "running" in sqm_status.lower()
            data["tc_qdisc"] = self.device.shell.exec("tc qdisc show 2>/dev/null").strip()
            wan_dev = self.device.uci.get("network.wan.device") or self.device.uci.get("network.wan.ifname") or ""
            data["wan_device"] = wan_dev
            if wan_dev:
                data["wan_speed"] = self.device.shell.exec(f"cat /sys/class/net/{wan_dev}/speed 2>/dev/null || echo unknown").strip()
        else:
            warnings.append(t("warn.http_tc"))
        if not sqm:
            warnings.append(t("warn.sqm_empty"))
        return CommandResult.ok_data(
            data,
            transport=self.device.transport,
            kind="qos",
            warnings=warnings,
            degraded=bool(warnings),
        )

    def rules(self) -> CommandResult:
        sqm = {}
        qos = {}
        try:
            sqm = self.device.uci.show("sqm")
        except DeviceCommandError:
            pass
        try:
            qos = self.device.uci.show("qos-gargoyle")
        except DeviceCommandError:
            pass
        return CommandResult.ok_data({"sqm_config": sqm, "qos_config": qos}, transport=self.device.transport, kind="qos")

    def classes(self) -> CommandResult:
        self.device.require(Capability.TC)
        raw = self.device.shell.exec("tc class show 2>/dev/null")
        return CommandResult.ok_data({"classes": raw.strip()}, transport=self.device.transport)

    def stats(self) -> CommandResult:
        self.device.require(Capability.TC)
        raw = self.device.shell.exec("tc -s qdisc show 2>/dev/null")
        return CommandResult.ok_data({"qdisc_stats": raw.strip()}, transport=self.device.transport)

    def interrupts(self) -> CommandResult:
        self.device.require(Capability.SHELL)
        raw = self.device.shell.exec(
            "cat /proc/interrupts 2>/dev/null | grep -E 'eth|wan|lan|ath|wlan' || "
            "cat /proc/interrupts 2>/dev/null | head -40"
        )
        return CommandResult.ok_data({"interrupts": raw}, transport=self.device.transport)
