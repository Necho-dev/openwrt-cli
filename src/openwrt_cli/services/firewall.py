from __future__ import annotations

import ipaddress
import re
from contextlib import nullcontext

from openwrt_cli.core.channels.uci import sections_of_type
from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.errors import DeviceCommandError, DeviceConnectionError
from openwrt_cli.core.http_client import HTTPDevice
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult


_PROTOCOLS = {
    "tcp": {"tcp"}, "6": {"tcp"}, "udp": {"udp"}, "17": {"udp"},
    "tcpudp": {"tcp", "udp"}, "all": {"tcp", "udp"}, "any": {"tcp", "udp"}, "*": {"tcp", "udp"},
}


def _tokens(value):
    return value if isinstance(value, list) else str(value or "").split()


def _protocols(value):
    tokens = [token.lower() for token in _tokens(value or "tcp udp")]
    return set().union(*(_PROTOCOLS.get(token, {token}) for token in tokens))


def _port_range(value):
    match = re.fullmatch(r"([0-9]{1,5})(?:[-:]([0-9]{1,5}))?", str(value))
    if not match:
        return None
    first = int(match[1])
    last = int(match[2] or match[1])
    return (first, last) if 1 <= first <= last <= 65535 else None


def _enabled(section):
    return str(section.get("enabled", "1")).lower() not in {"0", "false", "off", "no"}


def _family(section):
    family = section.get("family")
    if family and family != "auto":
        return {"ip": "ipv4", "ip6": "ipv6", "inet": "any", "*": "any"}.get(family, family)
    try:
        return f"ipv{ipaddress.ip_address(section.get('dest_ip', '')).version}"
    except ValueError:
        return "any"


def _overlaps(section, source, port, protocols, family):
    if not _enabled(section) or str(section.get("target") or "DNAT").upper() != "DNAT":
        return False
    if section.get("src") not in (None, "", "*", source) or _family(section) not in ("any", "*", family):
        return False
    if not (_protocols(section.get("proto")) & protocols) and not any(
        token.startswith("!") for token in _tokens(section.get("proto"))
    ):
        return False
    ports = _tokens(section.get("src_dport"))
    # An unrestricted port or a match we cannot parse may include the requested port.
    ranges = [_port_range(value) for value in ports]
    return not ranges or any(bounds is None or bounds[0] <= port <= bounds[1] for bounds in ranges)


def _redirect_row(section, values):
    keys = ("name", "src", "src_dport", "dest", "dest_ip", "dest_port", "proto")
    return {
        "section": section, **{key: values.get(key, "") for key in keys},
        "enabled": _enabled(values), "family": _family(values),
    }


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
        redirects = [_redirect_row(section, row) for section, row in sections_of_type(values, "redirect")]
        return CommandResult.ok_data({"redirects": redirects}, transport=self.device.transport, kind="redirects")

    def set_redirect(
        self, name: str, src_port: int, dest_ip: str, dest_port: int, *,
        src: str = "wan", dest: str = "lan", proto: str = "tcp", enabled: bool = True,
    ) -> CommandResult:
        name = name.strip()
        proto = proto.strip().lower()
        try:
            address = ipaddress.ip_address(dest_ip.strip())
        except ValueError:
            return self._redirect_error("invalid")
        if (
            not name or any(ord(char) < 32 for char in name)
            or address.is_unspecified or address.is_multicast or address.is_loopback
            or getattr(address, "scope_id", None)
            or not 1 <= src_port <= 65535 or not 1 <= dest_port <= 65535
            or proto not in {"tcp", "udp", "tcpudp"}
        ):
            return self._redirect_error("invalid")

        family = f"ipv{address.version}"
        values = {
            "name": name, "src": src, "src_dport": str(src_port),
            "dest": dest, "dest_ip": str(address), "dest_port": str(dest_port),
            "proto": proto, "enabled": "1" if enabled else "0", "family": family, "target": "DNAT",
        }
        device = self.device
        http = device.transport == "http"
        session = device.hold_session() if isinstance(device, HTTPDevice) else nullcontext()
        with session:
            config = device.uci.show("firewall")
            zones = {zone.get("name") for _, zone in sections_of_type(config, "zone")}
            if src not in zones or dest not in zones:
                return self._redirect_error("zone")
            redirects = list(sections_of_type(config, "redirect"))
            matches = [(key, row) for key, row in redirects if row.get("name") == name]
            if len(matches) > 1:
                return self._redirect_error("ambiguous")
            section, current = matches[0] if matches else (None, {})
            if current and str(current.get("target") or "DNAT").upper() != "DNAT":
                return self._redirect_error("ambiguous")
            for key, row in redirects:
                if key != section and enabled and _overlaps(row, src, src_port, _protocols(proto), family):
                    return self._redirect_error("conflict", name=row.get("name") or key)
            if section and self._redirect_matches(current, values):
                return self._redirect_result(section, current, "unchanged")
            if http:
                pending = device.ubus.call("uci", "changes").get("changes")
            else:
                pending = device.shell.exec("uci changes firewall", check=True).strip()
            if pending:
                return self._redirect_error("pending")

            try:
                if section:
                    device.uci.set_values("firewall", section, values)
                else:
                    section = device.uci.add("firewall", "redirect", values)
                self._verify_redirect(section, values)
                if http:
                    device.ubus.call("uci", "apply", {"rollback": True, "timeout": 60})
                else:
                    device.shell.exec("uci commit firewall", check=True)
            except DeviceCommandError as error:
                try:
                    if http:
                        device.ubus.call("uci", "revert", {"config": "firewall"})
                    else:
                        device.shell.exec("uci revert firewall", check=True)
                except (DeviceCommandError, DeviceConnectionError) as cleanup_error:
                    raise DeviceCommandError(t("err.redirect_cleanup", error=error, cleanup=cleanup_error)) from error
                raise

            if http:
                self._verify_redirect(section, values)
                device.ubus.call("uci", "confirm")
            else:
                try:
                    device.shell.exec("/etc/init.d/firewall reload", check=True)
                except DeviceCommandError as error:
                    raise DeviceCommandError(t("err.redirect_reload", error=error)) from error
            section, row = self._verify_redirect(section, values)
            return self._redirect_result(section, row, "updated" if matches else "created")

    @staticmethod
    def _redirect_matches(row, values):
        return (
            all(row.get(key) == values[key] for key in ("name", "src", "src_dport", "dest", "dest_ip", "dest_port"))
            and _protocols(row.get("proto")) == _protocols(values["proto"])
            and _enabled(row) == _enabled(values)
            and _family(row) == values["family"]
            and str(row.get("target") or "DNAT").upper() == "DNAT"
        )

    def _verify_redirect(self, section, values):
        config = self.device.uci.show("firewall")
        # The SSH CLI fallback uses positional IDs for anonymous UCI sections.
        rows = [(key, row) for key, row in sections_of_type(config, "redirect") if row.get("name") == values["name"]]
        if len(rows) != 1 or not self._redirect_matches(rows[0][1], values):
            raise DeviceCommandError(t("err.redirect_verify"))
        key, row = rows[0]
        if section in config and key != section:
            raise DeviceCommandError(t("err.redirect_verify"))
        return key, row

    def _redirect_error(self, code, **context):
        return CommandResult.fail(
            t(f"err.redirect_{code}", **context), transport=self.device.transport, data={"error": code},
        )

    def _redirect_result(self, section, row, action):
        return CommandResult.ok_data(
            {"redirects": [_redirect_row(section, row)], "action": action},
            transport=self.device.transport, kind="redirects", message=t(f"msg.redirect_{action}"),
        )

    def status(self) -> CommandResult:
        values = self.device.uci.show("firewall")
        zones = [sec.get("name") for _, sec in sections_of_type(values, "zone")]
        data: dict = {"zones": zones, "redirects": len(sections_of_type(values, "redirect"))}
        if Capability.SHELL in self.device.capabilities:
            data["ip_forward"] = self.device.shell.exec("cat /proc/sys/net/ipv4/ip_forward 2>/dev/null").strip()
        else:
            data["ip_forward"] = None
        return CommandResult.ok_data(data, transport=self.device.transport, kind="fwstatus")
