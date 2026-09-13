from __future__ import annotations

import ipaddress
import re
import time
from datetime import datetime
from typing import Any

from openwrt_cli.core.channels.uci import sections_of_type
from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError, DeviceConnectionError
from openwrt_cli.core.mac_vendor import lookup_enhanced
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult


def _fmt_bytes(num_bytes: int | float) -> str:
    if num_bytes < 0:
        return "—"
    n = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024.0:
            return f"{abs(n):.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


def _apply_bandix_human(row: dict[str, Any]) -> None:
    row["lan_rx_human"] = _fmt_bytes(row.get("lan_rx_bytes") or 0)
    row["lan_tx_human"] = _fmt_bytes(row.get("lan_tx_bytes") or 0)
    row["wan_rx_human"] = _fmt_bytes(row.get("wan_rx_bytes") or 0)
    row["wan_tx_human"] = _fmt_bytes(row.get("wan_tx_bytes") or 0)
    row["rx_human"] = _fmt_bytes(row.get("rx_bytes") or 0)
    row["tx_human"] = _fmt_bytes(row.get("tx_bytes") or 0)
    row["lan_rx_rate"] = _fmt_rate(row.get("lan_rx_bps") or 0)
    row["lan_tx_rate"] = _fmt_rate(row.get("lan_tx_bps") or 0)
    row["wan_rx_rate"] = _fmt_rate(row.get("wan_rx_bps") or 0)
    row["wan_tx_rate"] = _fmt_rate(row.get("wan_tx_bps") or 0)
    row["rx_rate"] = _fmt_rate(row.get("rx_bps") or 0)
    row["tx_rate"] = _fmt_rate(row.get("tx_bps") or 0)


def _fmt_rate(bytes_per_sec: float) -> str:
    bits = max(0.0, float(bytes_per_sec)) * 8
    if bits < 1_000_000:
        kbps = bits / 1000
        return "0 Kbps" if kbps < 0.05 else f"{kbps:.1f} Kbps"
    if bits < 1_000_000_000:
        return f"{bits / 1_000_000:.2f} Mbps"
    return f"{bits / 1_000_000_000:.2f} Gbps"


_SKIP_DEV = {"lo", "dummy0"}
_SKIP_DEV_PREFIX = (
    "ifb", "teql", "sit", "gre", "tun", "tap", "br-", "pppoe-", "pptp-",
    "l2tp-", "wg", "imq", "veth", "docker", "virbr",
)
_VIRTUAL_TYPE = ("bridge", "vlan", "tunnel", "bonding", "macvlan")


def is_physical_device(name: str, dev: dict | None = None) -> bool:
    """Physical NIC as in LuCI Bandwidth: eth0/wlan*, not lo/bridge/VLAN/tunnels."""
    low = (name or "").lower()
    if not low or low in _SKIP_DEV:
        return False
    if any(low.startswith(p) for p in _SKIP_DEV_PREFIX):
        return False
    if "." in name:
        return False
    typ = str((dev or {}).get("type") or "").lower()
    if any(key in typ for key in _VIRTUAL_TYPE):
        return False
    return True


def _filter_neighbors(
    rows: list[dict[str, Any]],
    *,
    ip: str | None = None,
    mac: str | None = None,
) -> list[dict[str, Any]]:
    from openwrt_cli.services.bandix import norm_mac

    want_ip = (ip or "").strip()
    want_mac = norm_mac(mac) if mac else ""
    out = rows
    if want_ip:
        out = [r for r in out if (r.get("ip") or "") == want_ip]
    if want_mac:
        out = [r for r in out if norm_mac(r.get("mac")) == want_mac]
    return out


def _point_ts_seconds(ts) -> float:
    try:
        value = float(ts or 0)
    except (TypeError, ValueError):
        return 0.0
    if value >= 1e11:
        return value / 1000.0
    return value


def _parse_link_speed(raw) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text or text in ("-1", "0"):
        return {"mbps": None, "duplex": "", "human": "—"}
    duplex = ""
    num = text
    if text[-1:] in ("F", "H", "f", "h"):
        duplex = "full" if text[-1:].upper() == "F" else "half"
        num = text[:-1]
    try:
        mbps = int(float(num))
    except ValueError:
        return {"mbps": None, "duplex": duplex, "human": text}
    label = f"{mbps} Mbps"
    if duplex == "full":
        label += " " + t("link.full")
    elif duplex == "half":
        label += " " + t("link.half")
    return {"mbps": mbps, "duplex": duplex, "human": label}


def _parse_addrs(items) -> list[str]:
    out: list[str] = []
    for item in items or []:
        ip = item.get("address") if isinstance(item, dict) else None
        if not ip:
            continue
        mask = item.get("mask")
        text = f"{ip}/{mask}" if mask is not None else str(ip)
        if text not in out:
            out.append(text)
    return out


def _pick_gateway(routes) -> str:
    fallback = ""
    for route in routes or []:
        via = route.get("nexthop") or ""
        if not via:
            continue
        target = str(route.get("target") or "")
        mask = route.get("mask")
        if target in ("0.0.0.0", "::", "") or mask == 0:
            return via
        if not fallback:
            fallback = via
    return fallback


def _primary_role(roles: list[str]) -> str:
    lower = [r.lower() for r in roles]
    for pref in ("wan", "lan", "wan6", "lan6"):
        if pref in lower:
            return roles[lower.index(pref)]
    return roles[0] if roles else ""


def _new_l3() -> dict[str, Any]:
    return {
        "roles": [],
        "proto": "",
        "up": None,
        "device": "",
        "l3_device": "",
        "ipv4": [],
        "ipv6": [],
        "gateway": "",
        "dns": [],
        "metric": "",
        "uptime": 0,
    }


def _merge_l3(dst: dict[str, Any], iface: dict[str, Any]) -> None:
    name = iface.get("interface") or ""
    if name and name not in dst["roles"]:
        dst["roles"].append(name)
    proto = iface.get("proto") or ""
    if proto and (not dst["proto"] or dst["proto"] in {"dhcpv6", "none"}):
        dst["proto"] = proto
    if iface.get("up"):
        dst["up"] = True
    elif dst["up"] is None:
        dst["up"] = bool(iface.get("up"))
    for key in ("device", "l3_device"):
        if iface.get(key) and not dst.get(key):
            dst[key] = iface[key]
    for addr in _parse_addrs(iface.get("ipv4-address")):
        if addr not in dst["ipv4"]:
            dst["ipv4"].append(addr)
    for addr in _parse_addrs(iface.get("ipv6-address")):
        if addr not in dst["ipv6"]:
            dst["ipv6"].append(addr)
    for prefix in iface.get("ipv6-prefix") or []:
        ip = prefix.get("address")
        if not ip:
            continue
        mask = prefix.get("mask")
        text = f"{ip}/{mask}" if mask is not None else str(ip)
        if text not in dst["ipv6"]:
            dst["ipv6"].append(text)
    gateway = _pick_gateway(iface.get("route"))
    if gateway and not dst["gateway"]:
        dst["gateway"] = gateway
    for dns in iface.get("dns-server") or []:
        if dns and dns not in dst["dns"]:
            dst["dns"].append(dns)
    if iface.get("metric") not in (None, "") and dst.get("metric") in (None, ""):
        dst["metric"] = iface.get("metric")
    try:
        dst["uptime"] = max(int(dst.get("uptime") or 0), int(iface.get("uptime") or 0))
    except (TypeError, ValueError):
        pass


def _ubus_l3_index(dump: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index by logical name / device / l3_device; wan+wan6 on one NIC merge."""
    groups: dict[str, dict[str, Any]] = {}
    for iface in (dump or {}).get("interface") or []:
        key = iface.get("device") or iface.get("l3_device") or iface.get("interface") or ""
        slot = groups.get(key)
        if slot is None:
            slot = _new_l3()
            groups[key] = slot
        _merge_l3(slot, iface)
    index: dict[str, dict[str, Any]] = {}
    for key, slot in groups.items():
        names = {key, *(slot.get("roles") or []), slot.get("device") or "", slot.get("l3_device") or ""}
        for name in names:
            if name:
                index[name] = slot
    return index


def _device_mac_mtu(dev: dict[str, Any] | None) -> tuple[str, Any]:
    info = dev or {}
    mac = str(info.get("macaddr") or info.get("mac") or "").strip()
    mtu = info.get("mtu")
    return mac, mtu


_PROTO_LABEL = {
    "static": "Static",
    "dhcp": "DHCP client",
    "dhcpv6": "DHCPv6 client",
    "pppoe": "PPPoE",
    "pppoa": "PPPoA",
    "pptp": "PPTP",
    "l2tp": "L2TP",
    "wireguard": "WireGuard",
    "none": "Unmanaged",
    "relay": "Relay",
    "6in4": "IPv6-in-IPv4",
    "6to4": "IPv6-over-IPv4",
    "6rd": "IPv6-PD",
    "qmi": "QMI Cellular",
    "ncm": "NCM",
    "mbim": "MBIM",
    "modemmanager": "ModemManager",
    "wwan": "Wireless WAN",
}


_IP_ROUTE_LINE = re.compile(
    r"^(?:(?P<kind>[a-z_]+|\d+) )?(?P<dest>default|[0-9a-fA-F:.\/]+)(?: (?P<rest>.+))?$"
)


def _ip_flag_map(rest: str) -> dict[str, str]:
    parts = (rest or "").split()
    flags: dict[str, str] = {}
    for i in range(0, len(parts) - 1, 2):
        flags[parts[i]] = parts[i + 1]
    return flags


def _masked_addr(addr: str, mask: int) -> str | None:
    try:
        return str(ipaddress.ip_network(f"{addr}/{int(mask)}", strict=False).network_address)
    except (ValueError, TypeError):
        return None


def _route_dest_parts(dest: str, *, ipv6: bool) -> tuple[str, int]:
    if dest in {"default", "0.0.0.0/0", "::/0"}:
        return ("::" if ipv6 else "0.0.0.0", 0)
    if "/" in dest:
        addr, bits = dest.split("/", 1)
        try:
            return addr, int(bits)
        except ValueError:
            return dest, (128 if ipv6 else 32)
    return dest, (128 if ipv6 else 32)


def _lookup_route_iface(networks: list[dict[str, Any]], dev: str, dest: str, *, ipv6: bool) -> str:
    """Same match as LuCI routes.js getNetworkByDevice."""
    addr, mask = _route_dest_parts(dest, ipv6=ipv6)
    best = ""
    best_prefix = -1
    for net in networks:
        if (net.get("l3_device") or "") != dev and (net.get("device") or "") != dev:
            continue
        for key in ("ipv4-address", "ipv6-address", "ipv6-prefix", "ipv6-prefix-assignment", "route"):
            for item in net.get(key) or []:
                cmp_addr = item.get("address") or item.get("target")
                cmp_mask = item.get("mask")
                if not cmp_addr or not isinstance(cmp_mask, int):
                    continue
                if _masked_addr(cmp_addr, cmp_mask) != _masked_addr(addr, cmp_mask):
                    continue
                if mask < cmp_mask:
                    continue
                if cmp_mask > best_prefix:
                    best = str(net.get("interface") or "")
                    best_prefix = cmp_mask
    return best


def parse_ip_routes(raw: str, networks: list[dict[str, Any]] | None = None, *, ipv6: bool = False) -> list[dict[str, Any]]:
    """Parse `ip -4 route show table all` the way LuCI Active Routes does."""
    rows: list[dict[str, Any]] = []
    for line in (raw or "").splitlines():
        matched = _IP_ROUTE_LINE.match(line.strip())
        if not matched:
            continue
        dest = matched.group("dest")
        if dest == "default":
            dest = "::/0" if ipv6 else "0.0.0.0/0"
        if dest in {"fe80::/64", "ff00::/8"}:
            continue
        flags = _ip_flag_map(matched.group("rest") or "")
        device = flags.get("dev") or ""
        metric = flags.get("metric")
        rows.append({
            "dest": dest,
            "via": flags.get("via") or "",
            "src": flags.get("src") or flags.get("from") or "",
            "dev": device,
            "iface": _lookup_route_iface(networks or [], device, dest, ipv6=ipv6),
            "metric": int(metric) if metric is not None and str(metric).isdigit() else "",
            "table": flags.get("table") or "main",
            "proto": flags.get("proto") or "",
            "type": matched.group("kind") or "unicast",
            "raw": line.strip(),
        })
    return rows


_RULE_LINE = re.compile(r"^(\d+):\s+(.+)$")
_RULE_VALUE = {
    "from", "to", "fwmark", "iif", "oif", "lookup", "table", "tos", "dsfield",
    "ipproto", "sport", "dport", "pref", "priority", "goto", "realms",
    "uidrange", "suppress_prefixlength", "suppress_ifgroup", "protocol",
}
_RULE_ACTION = {"prohibit", "unreachable", "blackhole", "throw"}


def parse_ip_rules(raw: str) -> list[dict[str, Any]]:
    """Parse `ip -4 rule show` into LuCI Active IPv4 Rules columns."""
    rows: list[dict[str, Any]] = []
    for line in (raw or "").splitlines():
        matched = _RULE_LINE.match(line.strip())
        if not matched:
            continue
        flags: dict[str, str] = {}
        action = ""
        parts = matched.group(2).split()
        i = 0
        while i < len(parts):
            key = parts[i]
            if key == "nat" and i + 1 < len(parts):
                action = "nat"
                flags["nat"] = parts[i + 1]
                i += 2
                continue
            if key in _RULE_VALUE and i + 1 < len(parts):
                flags[key] = parts[i + 1]
                i += 2
                continue
            if key in _RULE_ACTION:
                action = key
            i += 1
        fwmark = flags.get("fwmark") or ""
        rows.append({
            "rule": f"# fwmark:{fwmark}" if fwmark else "#",
            "priority": int(matched.group(1)),
            "iif": flags.get("iif") or "",
            "src": flags.get("from") or "all",
            "sport": flags.get("sport") or "",
            "action": action,
            "ipproto": flags.get("ipproto") or "",
            "oif": flags.get("oif") or "",
            "dest": flags.get("to") or "any",
            "dport": flags.get("dport") or "",
            "table": flags.get("lookup") or flags.get("table") or "",
            "fwmark": fwmark,
            "raw": line.strip(),
        })
    return rows


def _proto_label(proto: str | None) -> str:
    raw = str(proto or "").strip()
    if not raw:
        return "—"
    return _PROTO_LABEL.get(raw.lower(), raw)


def _carrier_label(dev: dict[str, Any] | None) -> str:
    info = dev or {}
    if "carrier" in info:
        return "Present" if info.get("carrier") else "Absent"
    if "present" in info:
        return "Present" if info.get("present") else "Absent"
    if info.get("up"):
        return "Present"
    return "—"


def _fmt_iface_uptime(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    if total <= 0:
        return "—"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    parts.extend((f"{hours}h", f"{mins}m", f"{secs}s"))
    return " ".join(parts)


def _traffic_detail(num_bytes, packets) -> str:
    try:
        pkts = int(packets or 0)
    except (TypeError, ValueError):
        pkts = 0
    return f"{_fmt_bytes(num_bytes or 0)} ({pkts} Pkts.)"


def _parse_leases_text(raw: str) -> list[dict[str, Any]]:
    leases = []
    for line in raw.strip().splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        mac = parts[1]
        info = lookup_enhanced(mac)
        leases.append({
            "ip": parts[2],
            "mac": mac,
            "hostname": parts[3] if parts[3] != "*" else "",
            "expires": parts[0],
            "vendor": info["vendor"],
            "device_type": info["type"],
        })
    return leases


class NetworkService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def interfaces(self) -> CommandResult:
        data = self.device.ubus.call("network.interface", "dump")
        devices: dict[str, Any] = {}
        try:
            devices = self.device.ubus.call("network.device", "status") or {}
        except DeviceCommandError:
            devices = {}
        interfaces = []
        for iface in data.get("interface") or []:
            ipv4_raw = iface.get("ipv4-address") or []
            ipv4 = _parse_addrs(ipv4_raw)
            ipv6 = _parse_addrs(iface.get("ipv6-address"))
            for prefix in iface.get("ipv6-prefix") or []:
                ip = prefix.get("address")
                if not ip:
                    continue
                mask = prefix.get("mask")
                text = f"{ip}/{mask}" if mask is not None else str(ip)
                if text not in ipv6:
                    ipv6.append(text)
            dev_name = iface.get("l3_device") or iface.get("device") or ""
            dev = devices.get(dev_name) or {}
            mac, mtu = _device_mac_mtu(dev)
            st = dev.get("statistics") or {}
            proto = iface.get("proto", "")
            uptime = iface.get("uptime", 0)
            interfaces.append({
                "name": iface.get("interface", ""),
                "up": iface.get("up", False),
                "device": iface.get("device", ""),
                "proto": proto,
                "proto_human": _proto_label(proto),
                "l3_device": iface.get("l3_device", ""),
                "ipaddr": ipv4_raw[0]["address"] if ipv4_raw else "",
                "netmask": str(ipv4_raw[0]["mask"]) if ipv4_raw else "",
                "ipv4": ipv4,
                "ipv6": ipv6,
                "gateway": _pick_gateway(iface.get("route")),
                "dns": [d for d in (iface.get("dns-server") or []) if d],
                "mac": mac,
                "mtu": mtu,
                "carrier": bool(dev.get("carrier")) if "carrier" in dev else None,
                "carrier_human": _carrier_label(dev),
                "metric": iface.get("metric", ""),
                "uptime": uptime,
                "uptime_human": _fmt_iface_uptime(uptime),
                "rx_bytes": st.get("rx_bytes", 0),
                "tx_bytes": st.get("tx_bytes", 0),
                "rx_packets": st.get("rx_packets", 0),
                "tx_packets": st.get("tx_packets", 0),
                "rx_detail": _traffic_detail(st.get("rx_bytes"), st.get("rx_packets")),
                "tx_detail": _traffic_detail(st.get("tx_bytes"), st.get("tx_packets")),
            })
        return CommandResult.ok_data({"interfaces": interfaces}, transport=self.device.transport, kind="interfaces")

    def _interface_dump(self) -> list[dict[str, Any]]:
        try:
            return (self.device.ubus.call("network.interface", "dump") or {}).get("interface") or []
        except DeviceCommandError:
            return []

    def _ip_exec(self, params: list[str]) -> str | None:
        if Capability.SHELL in self.device.capabilities:
            try:
                return self.device.shell.exec("ip " + " ".join(params))
            except DeviceCommandError:
                return None
        try:
            data = self.device.ubus.call("file", "exec", {
                "command": "/sbin/ip",
                "params": params,
            })
        except DeviceCommandError:
            return None
        if not isinstance(data, dict) or data.get("code") not in (0, None):
            return None
        return data.get("stdout") or ""

    def _ip_route_text(self) -> str | None:
        return self._ip_exec(["-4", "route", "show", "table", "all"])

    def routes(self) -> CommandResult:
        networks = self._interface_dump()
        raw = self._ip_route_text()
        if raw is not None:
            return CommandResult.ok_data(
                {"routes": parse_ip_routes(raw, networks)},
                transport=self.device.transport,
                kind="routes",
            )

        lines: list[dict[str, Any]] = []
        seen: set[tuple] = set()
        for iface in networks:
            name = iface.get("interface") or ""
            dev = iface.get("l3_device") or iface.get("device") or ""
            for addr in iface.get("ipv4-address") or []:
                ip = addr.get("address")
                mask = addr.get("mask")
                if not ip:
                    continue
                try:
                    dest = str(ipaddress.ip_network(f"{ip}/{mask}", strict=False))
                except ValueError:
                    dest = f"{ip}/{mask}" if mask is not None else ip
                key = (dest, "", dev)
                if key in seen:
                    continue
                seen.add(key)
                lines.append({
                    "dest": dest, "via": "", "src": ip, "dev": dev, "iface": name,
                    "metric": "", "table": "main", "proto": "kernel", "type": "unicast",
                })
            for route in iface.get("route") or []:
                target = str(route.get("target") or "")
                mask = route.get("mask")
                via = route.get("nexthop") or ""
                if target in ("0.0.0.0", "::") or mask == 0:
                    dest = "0.0.0.0/0"
                else:
                    dest = f"{target}/{mask}" if mask is not None else target
                key = (dest, via, dev)
                if key in seen:
                    continue
                seen.add(key)
                lines.append({
                    "dest": dest, "via": via, "src": "", "dev": dev, "iface": name,
                    "metric": route.get("metric") or "", "table": "main",
                    "proto": "static", "type": "unicast",
                })
        return CommandResult.ok_data(
            {"routes": lines},
            transport=self.device.transport,
            kind="routes",
            warnings=[t("warn.http_routes")],
            degraded=True,
        )

    def rules(self) -> CommandResult:
        raw = self._ip_exec(["-4", "rule", "show"])
        if raw is None:
            return CommandResult.fail(t("err.rules"), transport=self.device.transport)
        return CommandResult.ok_data(
            {"rules": parse_ip_rules(raw)},
            transport=self.device.transport,
            kind="rules",
        )

    def dns(self) -> CommandResult:
        servers: list[str] = []
        if Capability.FILE_READ in self.device.capabilities:
            for path in ("/tmp/resolv.conf.d/resolv.conf.auto", "/etc/resolv.conf"):
                try:
                    if self.device.fs.exists(path):
                        raw = self.device.fs.read(path)
                        for line in raw.splitlines():
                            if line.startswith("nameserver"):
                                parts = line.split()
                                if len(parts) > 1 and parts[1] not in servers:
                                    servers.append(parts[1])
                        if servers:
                            break
                except (CapabilityError, DeviceCommandError, OSError):
                    continue
        if not servers:
            dump = self.device.ubus.call("network.interface", "dump")
            for iface in dump.get("interface") or []:
                for dns in iface.get("dns-server") or []:
                    if dns not in servers:
                        servers.append(dns)
        return CommandResult.ok_data({"nameservers": servers}, transport=self.device.transport, kind="dns")

    def dhcp(self) -> CommandResult:
        values = self.device.uci.show("dhcp")
        lease_result = self.leases()
        leases = (lease_result.data or {}).get("leases") if lease_result.ok else []
        return CommandResult.ok_data(
            {"uci_config": values, "leases": leases},
            transport=self.device.transport,
            kind="dhcp",
        )

    def leases(self) -> CommandResult:
        if Capability.FILE_READ in self.device.capabilities:
            try:
                if self.device.fs.exists("/tmp/dhcp.leases"):
                    raw = self.device.fs.read("/tmp/dhcp.leases")
                    parsed = _parse_leases_text(raw)
                    if parsed or raw.strip() == "":
                        return CommandResult.ok_data({"leases": parsed}, transport=self.device.transport, kind="leases")
            except (CapabilityError, DeviceCommandError):
                pass
        try:
            data = self.device.ubus.call("luci-rpc", "getDHCPLeases")
            leases = []
            for item in data.get("dhcp_leases") or []:
                mac = item.get("macaddr") or item.get("mac") or ""
                info = lookup_enhanced(mac) if mac else {"vendor": None, "type": "unknown"}
                leases.append({
                    "ip": item.get("ipaddr") or item.get("ip") or "",
                    "mac": mac,
                    "hostname": item.get("hostname") or "",
                    "expires": str(item.get("expires", "")),
                    "vendor": info["vendor"],
                    "device_type": info["type"],
                })
            return CommandResult.ok_data({"leases": leases}, transport=self.device.transport, kind="leases")
        except DeviceCommandError:
            pass
        try:
            hints = self.device.ubus.call("luci-rpc", "getHostHints")
            leases = []
            for mac, info in (hints or {}).items():
                ips = info.get("ipaddrs") or []
                if not ips:
                    continue
                extra = lookup_enhanced(mac)
                leases.append({
                    "ip": ips[0],
                    "mac": mac,
                    "hostname": info.get("name") or "",
                    "expires": "",
                    "vendor": extra["vendor"],
                    "device_type": extra["type"],
                })
            return CommandResult.ok_data(
                {"leases": leases},
                transport=self.device.transport,
                kind="leases",
                degraded=True,
                warnings=[t("warn.leases_hints")],
            )
        except DeviceCommandError as e:
            return CommandResult.fail(t("err.leases", error=e), transport=self.device.transport)

    def neighbors(
        self,
        *,
        live: bool = False,
        usage: bool = True,
        ip: str | None = None,
        mac: str | None = None,
    ) -> CommandResult:
        """IPv4 neighbors / ARP. Merge Bandix LAN/WAN and conn counts by MAC."""
        rows = self._neighbors_from_arp_file()
        source = "arp" if rows else ""
        if not rows:
            try:
                rows = self._neighbors_from_hints()
                source = "host_hints"
            except DeviceCommandError as e:
                return CommandResult.fail(t("err.neighbors", error=e), transport=self.device.transport)
        from openwrt_cli.services.bandix import BandixService, merge_neighbor_rows
        overlay = BandixService(self.device).overlay()
        if overlay is not None:
            merged = merge_neighbor_rows(rows, overlay.get("devices") or [], overlay.get("connections") or {})
            for row in merged:
                _apply_bandix_human(row)
            warnings = []
            if source == "host_hints":
                warnings.append(t("warn.http_arp"))
            merged = _filter_neighbors(merged, ip=ip, mac=mac)
            return CommandResult.ok_data(
                {"neighbors": merged, "count": len(merged), "source": "bandix"},
                transport=self.device.transport,
                kind="neighbors",
                warnings=warnings,
            )
        if usage:
            extra_map, usage_note = self._host_usage({r.get("ip") for r in rows}, live=live)
        else:
            extra_map, usage_note = {}, None
        for row in rows:
            extra = extra_map.get(row.get("ip"), {})
            row.update(extra)
            if extra:
                row["bytes_human"] = _fmt_bytes(extra.get("bytes") or 0)
                row["rx_human"] = _fmt_bytes(extra.get("rx_bytes") or 0)
                row["tx_human"] = _fmt_bytes(extra.get("tx_bytes") or 0)
            if extra.get("rx_bps") is not None:
                row["rx_rate"] = _fmt_rate(extra["rx_bps"])
                row["tx_rate"] = _fmt_rate(extra["tx_bps"])
        warnings = []
        if source == "host_hints":
            warnings.append(t("warn.http_arp"))
        if usage_note:
            warnings.append(usage_note)
        rows = _filter_neighbors(rows, ip=ip, mac=mac)
        return CommandResult.ok_data(
            {"neighbors": rows, "count": len(rows), "source": source},
            transport=self.device.transport,
            kind="neighbors",
            degraded=source == "host_hints" or bool(usage_note),
            warnings=warnings,
        )

    def set_neighbor_hostname(self, mac: str, hostname: str) -> CommandResult:
        from openwrt_cli.services.bandix import BandixService

        try:
            data = BandixService(self.device).set_hostname(mac, hostname)
        except DeviceCommandError as e:
            return CommandResult.fail(str(e), transport=self.device.transport)
        return CommandResult.ok_data(
            data,
            transport=self.device.transport,
            kind="neigh_hostname",
            message=t("msg.neigh_renamed", name=data.get("hostname") or "—"),
        )

    def _neighbors_from_arp_file(self) -> list[dict[str, Any]]:
        raw = ""
        try:
            if Capability.SHELL in self.device.capabilities:
                raw = self.device.shell.exec("cat /proc/net/arp 2>/dev/null")
            elif Capability.FILE_READ in self.device.capabilities:
                raw = self.device.fs.read("/proc/net/arp")
        except (CapabilityError, DeviceCommandError):
            return []
        return _parse_proc_arp(raw)

    def _neighbors_from_hints(self) -> list[dict[str, Any]]:
        hints = self.device.ubus.call("luci-rpc", "getHostHints")
        prefixes = self._iface_prefixes()
        rows: list[dict[str, Any]] = []
        for mac, info in (hints or {}).items():
            extra = lookup_enhanced(mac)
            for ip in info.get("ipaddrs") or []:
                rows.append({
                    "ip": ip,
                    "mac": mac,
                    "device": _iface_for_ip(ip, prefixes),
                    "hostname": info.get("name") or "",
                    "vendor": extra["vendor"],
                    "device_type": extra["type"],
                })
        rows.sort(key=lambda r: _ip_sort_key(r.get("ip", "")))
        return rows

    def _iface_prefixes(self) -> list[tuple[str, ipaddress.IPv4Network]]:
        prefixes: list[tuple[str, ipaddress.IPv4Network]] = []
        try:
            dump = self.device.ubus.call("network.interface", "dump")
        except DeviceCommandError:
            return prefixes
        for iface in dump.get("interface") or []:
            name = iface.get("interface") or ""
            for addr in iface.get("ipv4-address") or []:
                ip = addr.get("address")
                mask = addr.get("mask")
                if not ip:
                    continue
                try:
                    net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
                    if isinstance(net, ipaddress.IPv4Network):
                        prefixes.append((name, net))
                except ValueError:
                    continue
        return prefixes

    def reload(self, interface: str | None = None) -> CommandResult:
        self.device.require(Capability.INITD)
        if Capability.SHELL in self.device.capabilities:
            cmd = f"/etc/init.d/network reload {interface}" if interface else "/etc/init.d/network reload"
            out = self.device.shell.exec(cmd)
        else:
            self.device.ubus.call("rc", "init", {"name": "network", "action": "reload"})
            out = ""
        return CommandResult.ok_data(
            {"status": "ok", "output": out, "interface": interface},
            transport=self.device.transport,
            message=t("msg.net_reloaded"),
        )

    def stats(self, *, with_rates: bool = True) -> CommandResult:
        interfaces: list[dict[str, Any]] = []
        if Capability.SHELL in self.device.capabilities:
            raw = self.device.shell.exec("cat /proc/net/dev")
            for line in raw.strip().splitlines():
                if ":" not in line or line.strip().startswith("|"):
                    continue
                name, fields_s = line.split(":", 1)
                fields = fields_s.split()
                if len(fields) < 12:
                    continue
                try:
                    interfaces.append({
                        "name": name.strip(),
                        "rx_bytes": int(fields[0]),
                        "rx_packets": int(fields[1]),
                        "rx_errors": int(fields[2]),
                        "rx_dropped": int(fields[3]),
                        "tx_bytes": int(fields[8]),
                        "tx_packets": int(fields[9]),
                        "tx_errors": int(fields[10]),
                        "tx_dropped": int(fields[11]),
                    })
                except ValueError:
                    continue
        else:
            devices = self.device.ubus.call("network.device", "status")
            for name, dev in devices.items():
                st = dev.get("statistics") or {}
                interfaces.append({
                    "name": name,
                    "rx_bytes": st.get("rx_bytes", 0),
                    "tx_bytes": st.get("tx_bytes", 0),
                    "rx_packets": st.get("rx_packets", 0),
                    "tx_packets": st.get("tx_packets", 0),
                    "rx_errors": st.get("rx_errors", 0),
                    "tx_errors": st.get("tx_errors", 0),
                    "rx_dropped": st.get("rx_dropped", 0),
                    "tx_dropped": st.get("tx_dropped", 0),
                })

        l3_index: dict[str, dict[str, Any]] = {}
        try:
            l3_index = _ubus_l3_index(self.device.ubus.call("network.interface", "dump"))
        except DeviceCommandError:
            l3_index = {}

        devices: dict[str, Any] = {}
        try:
            devices = self.device.ubus.call("network.device", "status") or {}
        except DeviceCommandError:
            devices = {}
        names = [
            i["name"] for i in interfaces
            if (i.get("name") or "").lower() not in {"lo", "dummy0"}
            and not (i.get("name") or "").lower().startswith("ifb")
        ]
        rates = self._iface_rates_from_bwc(names) if with_rates else {}
        enriched = []
        for iface in interfaces:
            name = iface["name"]
            info = l3_index.get(name) or {}
            dev = devices.get(name) or {}
            link = _parse_link_speed(dev.get("speed"))
            rate = rates.get(name) or {}
            role = _primary_role(info.get("roles") or [])
            mac, mtu = _device_mac_mtu(dev)
            ipv4 = list(info.get("ipv4") or [])
            proto = info.get("proto") or ""
            uptime = info.get("uptime") or 0
            row = {
                **iface,
                "label": role or name,
                "role": role,
                "roles": list(info.get("roles") or []),
                "proto": proto,
                "proto_human": _proto_label(proto),
                "mac": mac,
                "mtu": mtu,
                "ipv4": ipv4,
                "ipv6": list(info.get("ipv6") or []),
                "ipaddr": ipv4[0].split("/")[0] if ipv4 else "",
                "gateway": info.get("gateway") or "",
                "dns": list(info.get("dns") or []),
                "carrier": bool(dev.get("carrier")) if "carrier" in dev else None,
                "carrier_human": _carrier_label(dev),
                "uptime": uptime,
                "uptime_human": _fmt_iface_uptime(uptime),
                "up": info.get("up") if info.get("up") is not None else bool(dev.get("up") or dev.get("carrier")),
                "rx_human": _fmt_bytes(iface["rx_bytes"]),
                "tx_human": _fmt_bytes(iface["tx_bytes"]),
                "rx_detail": _traffic_detail(iface.get("rx_bytes"), iface.get("rx_packets")),
                "tx_detail": _traffic_detail(iface.get("tx_bytes"), iface.get("tx_packets")),
                "speed": link["mbps"],
                "duplex": link["duplex"],
                "speed_human": link["human"],
                "dev_type": dev.get("type") or "",
                "physical": is_physical_device(name, dev),
            }
            if rate:
                row["rx_bps"] = rate.get("rx_bps")
                row["tx_bps"] = rate.get("tx_bps")
                row["rx_rate"] = _fmt_rate(rate.get("rx_bps") or 0)
                row["tx_rate"] = _fmt_rate(rate.get("tx_bps") or 0)
            enriched.append(row)
        return CommandResult.ok_data({"interfaces": enriched}, transport=self.device.transport, kind="interfaces")

    def metrics(
        self,
        *,
        ip: str | None = None,
        mac: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        raw: bool = False,
    ) -> CommandResult:
        """Bandix getMetrics. Fail closed if the plugin is missing."""
        from openwrt_cli.services.bandix import (
            BandixService,
            downsample_metric_points,
            norm_mac,
        )

        target = "all"
        resolved_mac = ""
        if ip:
            resolved_mac = self._mac_for_ip(ip)
            if not resolved_mac:
                return CommandResult.fail(
                    t("err.no_mac_for_ip", ip=ip),
                    transport=self.device.transport,
                )
            target = ip
        elif mac:
            resolved_mac = norm_mac(mac)
            if not resolved_mac:
                return CommandResult.fail(t("err.bad_mac"), transport=self.device.transport)
            target = resolved_mac

        parsed = BandixService(self.device).metrics(resolved_mac or None)
        if parsed is None:
            return CommandResult.fail(
                t("err.no_bandix"),
                transport=self.device.transport,
            )

        warnings: list[str] = []
        points = list(parsed.get("points") or [])
        retention = int(parsed.get("retention_seconds") or 3600)
        now = time.time()
        window_start = now - retention if retention > 0 else 0.0
        since_ts = since.timestamp() if since else window_start
        until_ts = until.timestamp() if until else now
        if since_ts < window_start:
            warnings.append(t("warn.metrics_window", seconds=retention))
            since_ts = window_start
        if until_ts < since_ts:
            until_ts = since_ts

        clipped = [
            {
                "ts": int(_point_ts_seconds(p.get("ts"))),
                "rx_bps": float(p.get("rx_bps") or 0),
                "tx_bps": float(p.get("tx_bps") or 0),
            }
            for p in points
            if since_ts <= _point_ts_seconds(p.get("ts")) <= until_ts
        ]
        display = clipped if raw else downsample_metric_points(clipped, 60)

        rx_vals = [float(p.get("rx_bps") or 0) for p in clipped]
        tx_vals = [float(p.get("tx_bps") or 0) for p in clipped]
        latest_rx = rx_vals[-1] if rx_vals else 0.0
        latest_tx = tx_vals[-1] if tx_vals else 0.0
        avg_rx = (sum(rx_vals) / len(rx_vals)) if rx_vals else 0.0
        avg_tx = (sum(tx_vals) / len(tx_vals)) if tx_vals else 0.0
        peak_rx = max(rx_vals) if rx_vals else 0.0
        peak_tx = max(tx_vals) if tx_vals else 0.0

        return CommandResult.ok_data(
            {
                "target": target,
                "mac": resolved_mac or "all",
                "since": int(since_ts),
                "until": int(until_ts),
                "retention_seconds": retention,
                "points": display,
                "latest": {"rx_bps": latest_rx, "tx_bps": latest_tx, "rx_rate": _fmt_rate(latest_rx), "tx_rate": _fmt_rate(latest_tx)},
                "average": {"rx_bps": avg_rx, "tx_bps": avg_tx, "rx_rate": _fmt_rate(avg_rx), "tx_rate": _fmt_rate(avg_tx)},
                "peak": {"rx_bps": peak_rx, "tx_bps": peak_tx, "rx_rate": _fmt_rate(peak_rx), "tx_rate": _fmt_rate(peak_tx)},
            },
            transport=self.device.transport,
            kind="metrics",
            warnings=warnings,
        )

    def _mac_for_ip(self, ip: str) -> str:
        from openwrt_cli.services.bandix import norm_mac

        result = self.neighbors(live=False, usage=False, ip=ip)
        for row in (result.data or {}).get("neighbors") or []:
            mac = norm_mac(row.get("mac"))
            if mac:
                return mac
        return ""

    def traffic(self) -> CommandResult:
        """Interface rates plus current neighbor traffic."""
        stats = self.stats()
        neigh = self.neighbors(live=True)
        warnings = list(stats.warnings or []) + list(neigh.warnings or [])
        if (neigh.data or {}).get("source") != "bandix":
            warnings.append(t("warn.neigh_conntrack"))
        return CommandResult.ok_data(
            {
                "interfaces": (stats.data or {}).get("interfaces") or [],
                "neighbors": (neigh.data or {}).get("neighbors") or [],
            },
            transport=self.device.transport,
            kind="traffic",
            warnings=warnings,
            degraded=stats.degraded or neigh.degraded,
        )

    def _iface_rates_from_bwc(self, names: list[str]) -> dict[str, dict[str, float]]:
        from openwrt_cli.services.monitor import MonitorService
        mon = MonitorService(self.device)
        out: dict[str, dict[str, float]] = {}
        for name in names:
            rows = mon.realtime(name)
            if not rows or len(rows) < 2:
                continue
            prev, cur = rows[-2], rows[-1]
            try:
                dt = max(float(cur[0]) - float(prev[0]), 0.2)
                out[name] = {
                    "rx_bps": max(0.0, (float(cur[1]) - float(prev[1])) / dt),
                    "tx_bps": max(0.0, (float(cur[3]) - float(prev[3])) / dt),
                }
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _host_usage(self, ips: set, *, live: bool = False) -> tuple[dict[str, dict[str, Any]], str | None]:
        first, err = self._conntrack_by_ip(ips)
        if err and not first:
            return {}, err
        if not live:
            return first, err
        time.sleep(1.0)
        second, err2 = self._conntrack_by_ip(ips)
        note = err or err2
        for ip, cur in second.items():
            prev = first.get(ip) or {}
            cur["rx_bps"] = max(0.0, float(cur.get("rx_bytes") or 0) - float(prev.get("rx_bytes") or 0))
            cur["tx_bps"] = max(0.0, float(cur.get("tx_bytes") or 0) - float(prev.get("tx_bytes") or 0))
        for ip, prev in first.items():
            if ip not in second:
                second[ip] = {**prev, "rx_bps": 0.0, "tx_bps": 0.0}
        return second, note

    def _conntrack_by_ip(self, ips: set) -> tuple[dict[str, dict[str, Any]], str | None]:
        wanted = {ip for ip in ips if ip}
        if not wanted:
            return {}, None
        try:
            data = self.device.ubus.call("luci", "getConntrackList", timeout=5)
        except (DeviceCommandError, DeviceConnectionError) as e:
            return {}, t("err.conntrack", error=e)
        usage: dict[str, dict[str, Any]] = {}
        for conn in data.get("result") or []:
            src = str(conn.get("src") or "")
            dst = str(conn.get("dst") or "")
            try:
                nbytes = int(conn.get("bytes") or 0)
            except (TypeError, ValueError):
                nbytes = 0
            for ip, direction in ((src, "tx"), (dst, "rx")):
                if ip not in wanted:
                    continue
                row = usage.setdefault(ip, {"conns": 0, "bytes": 0, "rx_bytes": 0, "tx_bytes": 0})
                row["conns"] += 1
                row["bytes"] += nbytes
                row[f"{direction}_bytes"] += nbytes
        return usage, None

    def wifi_list(self) -> CommandResult:
        values = self.device.uci.show("wireless")
        wifi = []
        for name, sec in sections_of_type(values, "wifi-iface"):
            wifi.append({
                "section": name,
                "device": sec.get("device", ""),
                "ssid": sec.get("ssid", ""),
                "network": sec.get("network", ""),
                "encryption": sec.get("encryption", ""),
                "disabled": sec.get("disabled", "0"),
            })
        return CommandResult.ok_data({"wifi": wifi}, transport=self.device.transport, kind="wifi")

    def wifi_set(self, section: str, ssid: str | None = None, key: str | None = None) -> CommandResult:
        if ssid is not None:
            self.device.uci.set(f"wireless.{section}.ssid", ssid)
        if key is not None:
            self.device.uci.set(f"wireless.{section}.key", key)
        self.device.uci.commit("wireless")
        if Capability.INITD in self.device.capabilities:
            try:
                self.reload()
            except (CapabilityError, DeviceCommandError):
                pass
        return CommandResult.ok_data(
            {"section": section, "ssid": ssid},
            transport=self.device.transport,
            message=t("msg.wifi_updated"),
        )

    def lan_show(self) -> CommandResult:
        ip = self.device.uci.get("network.lan.ipaddr")
        mask = self.device.uci.get("network.lan.netmask")
        return CommandResult.ok_data(
            {"ipaddr": ip, "netmask": mask},
            transport=self.device.transport,
            kind="lan",
        )

    def lan_set(self, ipaddr: str) -> CommandResult:
        old = self.device.uci.get("network.lan.ipaddr")
        self.device.uci.set("network.lan.ipaddr", ipaddr)
        self.device.uci.commit("network")
        if Capability.INITD in self.device.capabilities:
            try:
                self.reload()
            except (CapabilityError, DeviceCommandError):
                pass
        return CommandResult.ok_data(
            {"old_ip": old, "new_ip": ipaddr},
            transport=self.device.transport,
            message=t("msg.lan_changed", ip=ipaddr),
        )


def _parse_proc_arp(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        ip, _hw, flags, mac, _mask, dev = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        if mac in ("00:00:00:00:00:00", "00:00:00:00:00:00".lower()) or flags == "0x0":
            continue
        extra = lookup_enhanced(mac)
        rows.append({
            "ip": ip,
            "mac": mac,
            "device": dev,
            "hostname": "",
            "vendor": extra["vendor"],
            "device_type": extra["type"],
        })
    rows.sort(key=lambda r: _ip_sort_key(r.get("ip", "")))
    return rows


def _iface_for_ip(ip: str, prefixes: list[tuple[str, ipaddress.IPv4Network]]) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    for name, net in prefixes:
        if addr in net:
            return name
    return ""


def _ip_sort_key(ip: str) -> tuple:
    try:
        return (0, ipaddress.ip_address(ip).packed)
    except ValueError:
        return (1, ip.encode())
