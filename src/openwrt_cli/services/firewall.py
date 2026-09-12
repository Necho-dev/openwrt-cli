from __future__ import annotations

from openwrt_cli.core.channels.uci import sections_of_type
from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult


class FirewallService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def rules(self) -> CommandResult:
        self.device.require(Capability.IPTABLES)
        raw = self.device.shell.exec("iptables -L -n -v --line-numbers 2>/dev/null")
        return CommandResult.ok_data(
            {"raw": raw, "description": t("fw.iptables_filter")},
            transport=self.device.transport,
            kind="rules",
        )

    def nat(self) -> CommandResult:
        self.device.require(Capability.IPTABLES)
        raw = self.device.shell.exec("iptables -t nat -L -n -v --line-numbers 2>/dev/null")
        return CommandResult.ok_data(
            {"raw": raw, "description": t("fw.iptables_nat")},
            transport=self.device.transport,
            kind="nat",
        )

    def zones(self) -> CommandResult:
        values = self.device.uci.show("firewall")
        zones = []
        for name, sec in sections_of_type(values, "zone"):
            zones.append({
                "section": name,
                "name": sec.get("name", ""),
                "network": sec.get("network", ""),
                "input": sec.get("input", ""),
                "output": sec.get("output", ""),
                "forward": sec.get("forward", ""),
            })
        return CommandResult.ok_data({"zones": zones}, transport=self.device.transport, kind="zones")

    def redirects(self) -> CommandResult:
        values = self.device.uci.show("firewall")
        redirects = []
        for name, sec in sections_of_type(values, "redirect"):
            redirects.append({
                "section": name,
                "name": sec.get("name", ""),
                "src": sec.get("src", ""),
                "src_dport": sec.get("src_dport", ""),
                "dest": sec.get("dest", ""),
                "dest_ip": sec.get("dest_ip", ""),
                "dest_port": sec.get("dest_port", ""),
                "proto": sec.get("proto", ""),
            })
        return CommandResult.ok_data({"redirects": redirects}, transport=self.device.transport, kind="redirects")

    def status(self) -> CommandResult:
        values = self.device.uci.show("firewall")
        zones = [sec.get("name") for _, sec in sections_of_type(values, "zone")]
        data: dict = {"zones": zones, "redirects": len(sections_of_type(values, "redirect"))}
        if Capability.SHELL in self.device.capabilities:
            data["ip_forward"] = self.device.shell.exec("cat /proc/sys/net/ipv4/ip_forward 2>/dev/null").strip()
        else:
            data["ip_forward"] = None
        return CommandResult.ok_data(data, transport=self.device.transport, kind="fwstatus")
