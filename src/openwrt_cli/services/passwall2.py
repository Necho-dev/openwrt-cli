"""luci-app-passwall2: UCI lists, LuCI dispatcher / local ping, and node writes."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from openwrt_cli.core.channels.uci import option_from_values
from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError, DeviceConnectionError
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult

_META = {".type", ".name", ".index", ".anonymous", ".data"}
_LIST_TYPES = {"nodes", "subscribe_list", "acl_rule"}
# LuCI app_update order: PassWall2, then com.lua (plus Hysteria still on 26.7.x pages).
_KNOWN_COMS = (
    ("passwall2", ()),
    ("geoview", ("geoview_file",)),
    ("sing-box", ("sing_box_file", "sing-box_file", "singbox_file")),
    ("xray", ("xray_file", "xray")),
    ("hysteria", ("hysteria_file", "hysteria2_file")),
)
_COM_DEFAULT_PATH = {
    "geoview": "/usr/bin/geoview",
    "sing-box": "/usr/bin/sing-box",
    "xray": "/usr/bin/xray",
    "hysteria": "/usr/bin/hysteria",
}
_COM_TITLE = {
    "passwall2": "PassWall2",
    "geoview": "Geoview",
    "sing-box": "Sing-Box",
    "xray": "Xray",
    "hysteria": "Hysteria",
}
_COM_ALIAS = {
    "singbox": "sing-box",
    "sing_box": "sing-box",
    "hysteria2": "hysteria",
}
_PKG_CONTROL = (
    "/usr/lib/opkg/info/luci-app-passwall2.control",
    "/usr/lib/apk/packages/luci-app-passwall2",
)
# LuCI DummyValue on 26.7.x; SSH also tries the older TMP_ACL_PATH file.
ACL_LOG_DIR = "/tmp/log"
_ACL_LOG_LEGACY_DIR = "/tmp/etc/passwall2/acl"
_LAT_NA = "—"
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_TIME_MS = re.compile(r"(\d+(?:\.\d+)?)\s*m?s", re.I)
_PW2_LOG_LINE = re.compile(
    r"^(?P<tz>[+-]\d{4}\s+)?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|TRACE)\b(?P<rest>.*)$",
    re.I,
)
_PW2_LOG_TS = re.compile(r"^(?P<tz>[+-]\d{4}\s+)?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\b")


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x not in (None, "")]
    return [str(value)]


def _as_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return " ".join(str(x) for x in value if x not in (None, ""))
    return str(value)


def _truthy(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def _options(sec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in sec.items() if k not in _META}


def acl_log_path(acl_id: str) -> str:
    return f"{ACL_LOG_DIR}/passwall2_acl_{acl_id}.log"


def _acl_log_candidates(acl_id: str) -> tuple[str, ...]:
    return (acl_log_path(acl_id), f"{_ACL_LOG_LEGACY_DIR}/{acl_id}.log")


def _section(values: dict[str, dict[str, Any]], typ: str, idx: int = 0) -> dict[str, Any]:
    matches = [sec for sec in values.values() if isinstance(sec, dict) and sec.get(".type") == typ]
    if idx >= len(matches):
        return {}
    return matches[idx]


def _section_name(values: dict[str, dict[str, Any]], typ: str, idx: int = 0) -> str:
    matches = [name for name, sec in values.items() if isinstance(sec, dict) and sec.get(".type") == typ]
    if idx >= len(matches):
        return ""
    return matches[idx]


_ACRONYMS = {
    "tcp": "TCP",
    "udp": "UDP",
    "dns": "DNS",
    "ip": "IP",
    "ipv4": "IPv4",
    "ipv6": "IPv6",
    "icmp": "ICMP",
    "nft": "Nft",
    "url": "URL",
    "acl": "ACL",
    "http": "HTTP",
    "https": "HTTPS",
    "ttl": "TTL",
    "mtu": "MTU",
    "mss": "MSS",
    "lan": "LAN",
    "wan": "WAN",
    "tproxy": "TProxy",
    "tcping": "TCPing",
    "id": "ID",
    "ttl": "TTL",
    "geoip": "GeoIP",
    "geosite": "Geosite",
}
_BOOL_OPTS = frozenset({
    "start_daemon", "accept_icmp", "prefer_nft", "ipv6_tproxy", "show_node_info",
    "enabled", "log", "acl_enable", "socks_enabled", "localhost_proxy",
    "client_proxy", "dns_redirect", "remote_fakedns", "filter_keyword_discarded",
    "geoip_update", "geosite_update",
    "balancing_enable", "sniffing_override_dest", "record_fragment", "fragment",
})
_LINE_LIST_OPTS = frozenset({"domain_list", "ip_list", "sources"})
_SETTINGS_BLOCKS = (
    "global_delay", "global_forwarding", "global_other",
    "global_haproxy", "global_xray", "global_singbox",
)
_RULE_GEO_KEYS = (
    "geoip_url", "geosite_url", "v2ray_location_asset", "update_week_mode",
)
_WEEK_MODE = {
    "8": "pw2.week.loop",
    "7": "pw2.week.daily",
    "0": "pw2.week.sun",
    "1": "pw2.week.mon",
    "2": "pw2.week.tue",
    "3": "pw2.week.wed",
    "4": "pw2.week.thu",
    "5": "pw2.week.fri",
    "6": "pw2.week.sat",
}


def snake_to_pascal(key: str) -> str:
    """tcp_no_redir_ports -> TCPNoRedirPorts."""
    parts = [p for p in str(key or "").split("_") if p]
    if not parts:
        return str(key or "")
    out: list[str] = []
    for part in parts:
        low = part.lower()
        if low in _ACRONYMS:
            out.append(_ACRONYMS[low])
        else:
            out.append(part[:1].upper() + part[1:].lower())
    return "".join(out)


def uci_label(key: str, *, section: bool = False) -> str:
    if not key:
        return ""
    prefix = "pw2.sec." if section else "pw2.opt."
    i18n_key = f"{prefix}{key}"
    text = t(i18n_key)
    if text != i18n_key:
        return text
    return snake_to_pascal(key)


def com_title(name: str) -> str:
    key = f"pw2.com.{name}"
    text = t(key)
    if text != key:
        return text
    return _COM_TITLE.get(name, name)


def summarize_list(value: Any, *, empty: str = "—") -> str:
    """Table cell: first item, remaining as +N."""
    if isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
    else:
        text = str(value or "").strip()
        items = [text] if text else []
    if not items:
        return empty
    if len(items) == 1:
        return items[0]
    return f"{items[0]} +{len(items) - 1}"


def format_line_list(value: Any) -> str:
    if isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
    else:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        if "\n" in text:
            items = [ln.strip() for ln in text.split("\n") if ln.strip()]
        else:
            items = [part for part in text.split() if part]
    return "\n".join(items) if items else "—"


def count_line_list(value: Any) -> int:
    text = format_line_list(value)
    if text == "—":
        return 0
    return sum(1 for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))


_SECRET_OPTS = frozenset({"uuid", "password", "obfsPassword", "reality_publicKey"})


def format_uci_value(key: str, value: Any) -> str:
    if key in _LINE_LIST_OPTS:
        return format_line_list(value)
    if isinstance(value, list):
        value = "\n".join(str(x) for x in value if x not in (None, ""))
    if value in (None, ""):
        if key == "update_week_mode":
            return t("pw2.week.disable")
        return "—"
    if key in _SECRET_OPTS:
        return "******"
    if key in _BOOL_OPTS:
        return t("label.yes") if _truthy(value) else t("label.no")
    if key == "update_week_mode":
        week_key = _WEEK_MODE.get(str(value).strip())
        if week_key:
            return t(week_key)
    if key == "auto_detection_time":
        mode = str(value).strip().lower()
        if mode in {"tcping", "tcp"}:
            return t("col.tcping")
        if mode in {"icmp", "ping", "icmping"}:
            return t("col.ping")
        if mode in {"0", "off", "close", "closed", "disable", "disabled"}:
            return t("label.no")
    return str(value)


def detection_mode(values: dict[str, dict[str, Any]]) -> str:
    raw = str(_section(values, "global_other").get("auto_detection_time") or "").strip().lower()
    if raw in {"", "0", "off", "close", "closed", "disable", "disabled", "none", "nil"}:
        return "off"
    if raw in {"tcping", "tcp"}:
        return "tcping"
    if raw in {"icmp", "ping", "icmping"}:
        return "icmp"
    return "off"


def can_measure(node: dict[str, Any]) -> bool:
    proto = str(node.get("protocol") or "")
    if proto.startswith("_"):
        return False
    if not node.get("address") or node.get("port") in (None, ""):
        return False
    return True


def parse_nodes(values: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, sec in values.items():
        if not isinstance(sec, dict) or sec.get(".type") != "nodes":
            continue
        opts = _options(sec)
        out.append({
            "id": name,
            "remarks": _as_text(opts.get("remarks")) or name,
            "group": _as_text(opts.get("group")),
            "type": _as_text(opts.get("type")),
            "protocol": _as_text(opts.get("protocol")),
            "address": _as_text(opts.get("address")),
            "port": _as_text(opts.get("port")),
            "add_from": _as_text(opts.get("add_from")),
            "ping": _LAT_NA,
            "tcping": _LAT_NA,
            "options": opts,
        })
    out.sort(key=lambda n: (n.get("group") or "", n.get("remarks") or "", n.get("id") or ""))
    return out


def parse_subscribe(values: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = parse_nodes(values)
    out: list[dict[str, Any]] = []
    for name, sec in values.items():
        if not isinstance(sec, dict) or sec.get(".type") != "subscribe_list":
            continue
        opts = _options(sec)
        remarks = _as_text(opts.get("remark") or opts.get("remarks")) or name
        needle = remarks.lower()
        count = sum(1 for n in nodes if (n.get("group") or "").lower() == needle)
        out.append({
            "id": name,
            "remarks": remarks,
            "url": _as_text(opts.get("url") or opts.get("subscribe_url")),
            "node_count": count,
            "options": opts,
        })
    return out


def acl_inherit(value: Any, fallback: str, *, disable_ok: bool = False) -> str:
    """Empty ACL option means LuCI 'Use global config (fallback)'."""
    text = _as_text(value)
    if disable_ok and text == "disable":
        return t("pw2.no_patterns")
    if text == "":
        return t("pw2.use_global", value=fallback or "—")
    return text


def parse_acl(values: dict[str, dict[str, Any]], nodes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    remarks = {n["id"]: n.get("remarks") or n["id"] for n in (nodes or parse_nodes(values))}
    fwd = _options(_section(values, "global_forwarding"))
    global_id, global_remarks = current_node(values)
    out: list[dict[str, Any]] = []
    for name, sec in values.items():
        if not isinstance(sec, dict) or sec.get(".type") != "acl_rule":
            continue
        opts = _options(sec)
        node_id = _as_text(opts.get("node"))
        if node_id:
            node_remarks = remarks.get(node_id, node_id)
        else:
            node_remarks = t("pw2.use_global", value=global_remarks or global_id or "—")
        log_on = _truthy(opts.get("log"))
        loglevel = _as_text(opts.get("loglevel"))
        if not loglevel and log_on:
            loglevel = "warning"
        out.append({
            "id": name,
            "enabled": _truthy(opts.get("enabled")),
            "remarks": _as_text(opts.get("remarks")) or name,
            "sources": _as_list(opts.get("sources")),
            "node": node_id,
            "node_remarks": node_remarks,
            "tcp_no_redir_ports": acl_inherit(opts.get("tcp_no_redir_ports"), _as_text(fwd.get("tcp_no_redir_ports")), disable_ok=True),
            "udp_no_redir_ports": acl_inherit(opts.get("udp_no_redir_ports"), _as_text(fwd.get("udp_no_redir_ports")), disable_ok=True),
            "tcp_redir_ports": acl_inherit(opts.get("tcp_redir_ports"), _as_text(fwd.get("tcp_redir_ports"))),
            "udp_redir_ports": acl_inherit(opts.get("udp_redir_ports"), _as_text(fwd.get("udp_redir_ports"))),
            "interface": _as_text(opts.get("interface")),
            "dns_mode": _as_text(opts.get("dns_mode") or opts.get("remote_dns_protocol")),
            "remote_dns_protocol": _as_text(opts.get("remote_dns_protocol") or opts.get("dns_mode")),
            "remote_dns": _as_text(opts.get("remote_dns") or opts.get("dns")),
            "direct_dns_query_strategy": _as_text(opts.get("direct_dns_query_strategy")),
            "remote_dns_detour": _as_text(opts.get("remote_dns_detour")),
            "remote_fakedns": _as_text(opts.get("remote_fakedns")),
            "remote_dns_query_strategy": _as_text(opts.get("remote_dns_query_strategy")),
            "dns_redirect": _as_text(opts.get("dns_redirect")),
            "log": _as_text(opts.get("log")),
            "loglevel": loglevel,
            "log_file": acl_log_path(name),
            "options": opts,
        })
    return out


def parse_settings(values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    known = {block: _options(_section(values, block)) for block in _SETTINGS_BLOCKS}
    extra: dict[str, Any] = {}
    skip_types = _LIST_TYPES | set(_SETTINGS_BLOCKS) | {
        "global", "global_app", "global_subscribe", "global_acl",
        "global_rules", "shunt_rules",
    }
    for name, sec in values.items():
        if not isinstance(sec, dict):
            continue
        typ = sec.get(".type")
        if typ in skip_types:
            continue
        extra[name] = {"type": typ, **_options(sec)}
    return {**known, "extra": extra}


def parse_shunt_rules(values: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, sec in values.items():
        if not isinstance(sec, dict) or sec.get(".type") != "shunt_rules":
            continue
        opts = _options(sec)
        domains = opts.get("domain_list")
        ips = opts.get("ip_list")
        out.append({
            "id": name,
            "remarks": _as_text(opts.get("remarks")) or name,
            "network": _as_text(opts.get("network")),
            "domain_list": domains,
            "ip_list": ips,
            "domain_count": count_line_list(domains),
            "ip_count": count_line_list(ips),
            "options": opts,
        })
    return out


def parse_geo_rules(fields: dict[str, Any]) -> list[tuple[str, Any]]:
    fields = fields or {}
    mode = str(fields.get("update_week_mode") or "").strip()
    ordered: list[tuple[str, Any]] = [(key, fields.get(key)) for key in _RULE_GEO_KEYS]
    if mode in {"0", "1", "2", "3", "4", "5", "6", "7"}:
        ordered.append(("update_time_mode", fields.get("update_time_mode")))
    if mode == "8":
        ordered.append(("update_interval_mode", fields.get("update_interval_mode")))
    flags = []
    if _truthy(fields.get("geoip_update")):
        flags.append("geoip")
    if _truthy(fields.get("geosite_update")):
        flags.append("geosite")
    ordered.append(("update_options", "  ".join(flags) if flags else t("label.no")))
    return ordered


def parse_rules(values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "global_rules": _options(_section(values, "global_rules")),
        "shunt_rules": parse_shunt_rules(values),
    }


def acl_enable(values: dict[str, dict[str, Any]]) -> bool | None:
    found = None
    for sec in values.values():
        if not isinstance(sec, dict) or sec.get(".type") == "acl_rule":
            continue
        if "acl_enable" in sec:
            found = _truthy(sec.get("acl_enable"))
    return found


def node_references(values: dict[str, dict[str, Any]], node_id: str) -> list[str]:
    refs: list[str] = []
    current, _ = current_node(values)
    if current == node_id:
        refs.append("global.node")
    for name, sec in values.items():
        if not isinstance(sec, dict):
            continue
        typ = sec.get(".type")
        opts = _options(sec)
        if typ == "acl_rule" and _as_text(opts.get("node")) == node_id:
            refs.append(f"acl:{name}")
        if typ != "nodes" or name == node_id:
            continue
        proto = _as_text(opts.get("protocol"))
        if not proto.startswith("_"):
            continue
        for key, value in opts.items():
            if key in {"remarks", "group", "type", "protocol", "add_from"}:
                continue
            items = value if isinstance(value, list) else [value]
            if any(_as_text(item) == node_id for item in items):
                refs.append(f"{name}.{key}")
    return refs


def _is_write_denied(exc: Exception) -> bool:
    text = str(exc).lower()
    return "access denied" in text or "permission denied" in text


def current_node(values: dict[str, dict[str, Any]]) -> tuple[str, str]:
    node_id = _as_text(_section(values, "global").get("node"))
    if not node_id:
        try:
            node_id = option_from_values(values, "passwall2.@global[0].node")
        except KeyError:
            node_id = ""
    remarks = ""
    if node_id:
        sec = values.get(node_id) or {}
        remarks = _as_text(sec.get("remarks")) or node_id
    return node_id, remarks


def parse_log_text(text: str, tail: int | None = None) -> list[str]:
    lines = [ln.rstrip("\r") for ln in (text or "").split("\n")]
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if tail is not None and tail > 0:
        lines = lines[-tail:]
    return lines


def parse_acl_log_text(text: str) -> tuple[bool, list[str], str | None]:
    raw = text or ""
    if "未启用日志" in raw or "alert(" in raw:
        return False, [], t("pw2.acl_log_off")
    body = _BR.sub("\n", raw)
    body = _TAG.sub("", body)
    lines = parse_log_text(body)
    return True, lines, None


def parse_pw2_log_entry(line: str) -> dict[str, Any]:
    raw = (line or "").rstrip("\r")
    entry: dict[str, Any] = {"msg": raw}
    match = _PW2_LOG_LINE.match(raw) or _PW2_LOG_TS.match(raw)
    if not match:
        return entry
    level = (match.groupdict().get("level") or "").upper()
    if level:
        entry["level"] = "WARN" if level == "WARNING" else level
    stamp = match.group("ts")
    entry["ts"] = stamp
    dt = _pw2_stamp_datetime(stamp, match.group("tz"))
    if dt is not None:
        entry["time"] = int(dt.timestamp() * 1000)
    return entry


def filter_pw2_log_entries(
    entries: list[dict[str, Any]],
    *,
    tail: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in entries:
        dt = _pw2_entry_datetime(entry)
        if dt is not None:
            if since is not None and dt < since:
                continue
            if until is not None and dt > until:
                continue
        out.append(entry)
    if tail is not None and tail > 0:
        out = out[-tail:]
    return out


def log_messages(items: list[Any] | None) -> list[str]:
    out: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            out.append(str(item.get("msg") or ""))
        else:
            out.append(str(item))
    return out


def _pw2_log_window(since: datetime | None, until: datetime | None) -> dict[str, Any]:
    return {
        "since": since.isoformat(sep=" ", timespec="seconds") if since else None,
        "until": until.isoformat(sep=" ", timespec="seconds") if until else None,
    }


def _pw2_stamp_datetime(stamp: str, tz_prefix: str | None) -> datetime | None:
    try:
        naive = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    raw = (tz_prefix or "").strip()
    match = re.fullmatch(r"([+-])(\d{2})(\d{2})", raw)
    if not match:
        return naive
    sign = 1 if match.group(1) == "+" else -1
    offset = timezone(timedelta(hours=sign * int(match.group(2)), minutes=sign * int(match.group(3))))
    return naive.replace(tzinfo=offset).astimezone().replace(tzinfo=None)


def _pw2_entry_datetime(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("time")
    if raw in (None, "", 0, "0"):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value >= 10**11:
        value = value / 1000
    try:
        return datetime.fromtimestamp(value)
    except (OSError, OverflowError, ValueError):
        return None


def _fmt_latency(value: Any) -> str:
    if value in (None, "", 0, "0", "timeout", "nil", "null", "false"):
        return _LAT_NA
    if isinstance(value, (int, float)):
        if value <= 0:
            return _LAT_NA
        return f"{value:g}ms"
    text = str(value).strip()
    if not text or text in {"-", "—", "timeout"}:
        return _LAT_NA
    if text.lower() in {"ok", "true"}:
        return text
    if text.replace(".", "", 1).isdigit():
        return f"{text}ms"
    match = _TIME_MS.search(text.replace(",", ""))
    if match:
        return f"{match.group(1)}ms"
    return text


def parse_ping_payload(raw: Any) -> str:
    if raw in (None, ""):
        return _LAT_NA
    if isinstance(raw, dict):
        for key in ("ping", "tcping", "data", "result", "time"):
            if raw.get(key) not in (None, ""):
                return _fmt_latency(raw.get(key))
        return _LAT_NA
    return _fmt_latency(raw)


def parse_version_payload(raw: Any) -> str:
    if raw in (None, ""):
        return ""
    if isinstance(raw, dict):
        data = raw.get("data")
        if data not in (None, ""):
            return str(data).strip()
        if raw.get("code") in (0, "0") and raw.get("msg"):
            return str(raw.get("msg")).strip()
        return str(raw.get("version") or raw.get("result") or "").strip()
    text = _TAG.sub("", str(raw)).strip()
    return text.splitlines()[0].strip() if text else ""


def parse_pkg_version(raw: str) -> str:
    """opkg/apk list-installed or control Version: line → 26.7.16."""
    text = (raw or "").strip()
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("version:"):
            return stripped.split(":", 1)[1].strip()
    if " - " in text:
        return text.split(" - ", 1)[1].strip().split()[0]
    return text.splitlines()[0].strip()


def _shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


class PassWall2Service:
    def __init__(self, device: DeviceClient):
        self.device = device

    def available(self) -> bool:
        try:
            return bool(self.device.uci.show("passwall2"))
        except Exception:
            return False

    def _values(self) -> dict[str, dict[str, Any]]:
        try:
            values = self.device.uci.show("passwall2")
        except DeviceCommandError as e:
            raise DeviceCommandError(t("err.pw2_missing")) from e
        return values or {}

    def _missing(self) -> CommandResult:
        return CommandResult.fail(
            t("err.pw2_missing"),
            transport=self.device.transport,
            data={"error": "pw2_missing", "installed": False},
        )

    def _ok(self, data: dict[str, Any], kind: str, message: str | None = None) -> CommandResult:
        return CommandResult.ok_data(data, transport=self.device.transport, kind=kind, message=message)

    def _require(self) -> dict[str, dict[str, Any]] | CommandResult:
        values = self._values()
        if not values:
            return self._missing()
        return values

    def _running(self) -> bool | None:
        if self.device.transport == "http" and hasattr(self.device, "list_services"):
            try:
                listing = self.device.list_services()
                info = (listing or {}).get("passwall2") or {}
                return bool(info.get("running"))
            except (DeviceCommandError, DeviceConnectionError, AttributeError):
                return None
        if Capability.SHELL in self.device.capabilities:
            raw = self.device.shell.exec(
                "/etc/init.d/passwall2 running 2>/dev/null && echo running || echo stopped"
            )
            return "running" in raw
        try:
            listing = self.device.ubus.call("rc", "list")
            return bool((listing.get("passwall2") or {}).get("running"))
        except (DeviceCommandError, DeviceConnectionError, AttributeError):
            return None

    def _luci(self, action: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> Any:
        call = getattr(self.device, "luci_call", None)
        if not callable(call):
            raise CapabilityError(t("err.pw2_no_luci"), missing=("luci",), hint=t("err.use_ssh"))
        return call(action, params, timeout=timeout)

    def status(self) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        node_id, remarks = current_node(values)
        return self._ok({
            "installed": True,
            "running": self._running(),
            "node": node_id,
            "node_remarks": remarks,
            "acl_enable": acl_enable(values),
            "detection": detection_mode(values),
        }, "pw2_status")

    def nodes(self, *, measure: bool = True) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        rows = parse_nodes(values)
        mode = detection_mode(values)
        if measure:
            self.measure_nodes(rows)
        node_id, remarks = current_node(values)
        return self._ok({
            "nodes": rows,
            "detection": mode,
            "current": node_id,
            "current_remarks": remarks,
        }, "pw2_nodes")

    def measure_nodes(self, rows: list[dict[str, Any]], modes: tuple[str, ...] | None = None) -> None:
        chosen = modes or ("icmp", "tcping")
        for row in rows:
            for mode in chosen:
                key = "tcping" if mode == "tcping" else "ping"
                if not can_measure(row):
                    row[key] = _LAT_NA
                    continue
                row[key] = self.ping_address(row["address"], row["port"], mode)

    def node_show(self, node_id: str) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        sec = values.get(node_id)
        if not isinstance(sec, dict) or sec.get(".type") != "nodes":
            return CommandResult.fail(
                t("err.pw2_no_node", id=node_id),
                transport=self.device.transport,
                data={"error": "pw2_no_node", "id": node_id},
            )
        row = next((n for n in parse_nodes(values) if n["id"] == node_id), None)
        return self._ok({"node": row or {"id": node_id, **_options(sec)}}, "pw2_node")

    def node_ping(self, node_id: str, *, mode: str | None = None) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        node = next((n for n in parse_nodes(values) if n["id"] == node_id), None)
        if not node:
            return CommandResult.fail(
                t("err.pw2_no_node", id=node_id),
                transport=self.device.transport,
                data={"error": "pw2_no_node", "id": node_id},
            )
        chosen = mode or detection_mode(values)
        if chosen == "off":
            chosen = "icmp"
        if chosen == "url":
            latency = self.urltest_node(node_id)
            field = "urltest"
        else:
            if not can_measure(node):
                latency = _LAT_NA
            else:
                latency = self.ping_address(node["address"], node["port"], chosen)
            field = "tcping" if chosen == "tcping" else "ping"
        return self._ok({
            "id": node_id,
            "remarks": node.get("remarks"),
            "mode": chosen,
            field: latency,
            "latency": latency,
        }, "pw2_ping")

    def _write_error(self, exc: Exception) -> CommandResult:
        if _is_write_denied(exc):
            return CommandResult.fail(
                t("err.pw2_write_denied"),
                transport=self.device.transport,
                data={"error": "pw2_write_denied"},
            )
        return CommandResult.fail(
            t("msg.exec_fail", error=exc),
            transport=self.device.transport,
            data={"error": "pw2_write_failed"},
        )

    def _apply(self) -> tuple[bool, str | None]:
        from openwrt_cli.services.service import ServiceService

        try:
            result = ServiceService(self.device).action("passwall2", "restart")
        except (DeviceCommandError, DeviceConnectionError, CapabilityError) as e:
            return False, str(e)
        if not result.ok:
            return False, result.message
        return True, None

    def _node_payload(self, node_id: str, values: dict[str, dict[str, Any]], *, applied: bool) -> dict[str, Any]:
        row = next((n for n in parse_nodes(values) if n["id"] == node_id), None)
        return {
            "id": node_id,
            "remarks": (row or {}).get("remarks") or node_id,
            "type": (row or {}).get("type") or "",
            "protocol": (row or {}).get("protocol") or "",
            "applied": applied,
        }

    def node_add(
        self,
        fields: dict[str, Any] | None = None,
        *,
        apply: bool = False,
        from_url: str | None = None,
        raw: bool = False,
    ) -> CommandResult:
        from openwrt_cli.services.pw2_node_schema import validate_node
        from openwrt_cli.services.pw2_share_url import ShareURLError, parse_share_url

        values = self._require()
        if isinstance(values, CommandResult):
            return values
        draft = dict(fields or {})
        if from_url:
            try:
                draft = {**parse_share_url(from_url), **draft}
            except ShareURLError as e:
                return CommandResult.fail(
                    str(e),
                    transport=self.device.transport,
                    data={"error": "pw2_share_url"},
                )
        ok, errors, normalized = validate_node(draft, raw=raw)
        if not ok:
            return CommandResult.fail(
                t("err.pw2_node_invalid", error="; ".join(errors)),
                transport=self.device.transport,
                data={"error": "pw2_node_invalid", "errors": errors},
            )
        try:
            node_id = self.device.uci.add("passwall2", "nodes", values=normalized)
            self.device.uci.commit("passwall2")
        except DeviceCommandError as e:
            return self._write_error(e)
        applied = False
        warnings: list[str] = []
        if apply:
            applied, warn = self._apply()
            if warn:
                warnings.append(warn)
        fresh = self._values()
        return self._ok(
            self._node_payload(node_id, fresh, applied=applied),
            "pw2_node",
            message=t("msg.pw2_node_saved") if applied else t("msg.pw2_node_committed"),
        ) if not warnings else CommandResult.ok_data(
            self._node_payload(node_id, fresh, applied=applied),
            transport=self.device.transport,
            kind="pw2_node",
            message=t("msg.pw2_node_committed"),
            warnings=warnings,
        )

    def node_set(
        self,
        node_id: str,
        patch: dict[str, Any] | None = None,
        *,
        apply: bool = False,
        unset: tuple[str, ...] | list[str] = (),
        raw: bool = False,
    ) -> CommandResult:
        from openwrt_cli.services.pw2_node_schema import FIELDS, validate_node

        values = self._require()
        if isinstance(values, CommandResult):
            return values
        sec = values.get(node_id)
        if not isinstance(sec, dict) or sec.get(".type") != "nodes":
            return CommandResult.fail(
                t("err.pw2_no_node", id=node_id),
                transport=self.device.transport,
                data={"error": "pw2_no_node", "id": node_id},
            )
        existing = _options(sec)
        patch = dict(patch or {})
        clear = {key for key in unset if key}
        clearable = {item.key for item in FIELDS if item.clear_empty}
        for key, value in list(patch.items()):
            if value == "" and key in clearable:
                clear.add(key)
                patch.pop(key)
        ok, errors, normalized = validate_node(patch, existing=existing, raw=raw, partial=True)
        if not ok:
            return CommandResult.fail(
                t("err.pw2_node_invalid", error="; ".join(errors)),
                transport=self.device.transport,
                data={"error": "pw2_node_invalid", "errors": errors},
            )
        try:
            if normalized:
                self.device.uci.set_values("passwall2", node_id, normalized)
            if clear:
                self.device.uci.delete("passwall2", node_id, options=sorted(clear))
            self.device.uci.commit("passwall2")
        except DeviceCommandError as e:
            return self._write_error(e)
        applied = False
        warnings: list[str] = []
        if apply:
            applied, warn = self._apply()
            if warn:
                warnings.append(warn)
        fresh = self._values()
        payload = self._node_payload(node_id, fresh, applied=applied)
        return CommandResult.ok_data(
            payload,
            transport=self.device.transport,
            kind="pw2_node",
            message=t("msg.pw2_node_saved") if applied else t("msg.pw2_node_committed"),
            warnings=warnings,
        )

    def node_delete(self, node_id: str, *, apply: bool = False, force: bool = False) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        sec = values.get(node_id)
        if not isinstance(sec, dict) or sec.get(".type") != "nodes":
            return CommandResult.fail(
                t("err.pw2_no_node", id=node_id),
                transport=self.device.transport,
                data={"error": "pw2_no_node", "id": node_id},
            )
        refs = node_references(values, node_id)
        if refs and not force:
            return CommandResult.fail(
                t("err.pw2_node_in_use", id=node_id, refs=", ".join(refs)),
                transport=self.device.transport,
                data={"error": "pw2_node_in_use", "id": node_id, "refs": refs},
            )
        remarks = _as_text(sec.get("remarks")) or node_id
        try:
            if refs and force:
                self._clear_node_refs(values, node_id, refs)
            self.device.uci.delete("passwall2", node_id)
            self.device.uci.commit("passwall2")
        except DeviceCommandError as e:
            return self._write_error(e)
        applied = False
        warnings: list[str] = []
        if apply:
            applied, warn = self._apply()
            if warn:
                warnings.append(warn)
        return CommandResult.ok_data(
            {"id": node_id, "remarks": remarks, "deleted": True, "applied": applied, "refs": refs},
            transport=self.device.transport,
            kind="pw2_node",
            message=t("msg.pw2_node_deleted"),
            warnings=warnings,
        )

    def _clear_node_refs(self, values: dict[str, dict[str, Any]], node_id: str, refs: list[str]) -> None:
        if "global.node" in refs:
            global_name = _section_name(values, "global")
            if global_name:
                self.device.uci.delete("passwall2", global_name, option="node")
        for ref in refs:
            if ref.startswith("acl:"):
                self.device.uci.delete("passwall2", ref[4:], option="node")
            elif "." in ref and not ref.startswith("acl:"):
                section, option = ref.split(".", 1)
                self.device.uci.delete("passwall2", section, option=option)

    def ping_address(self, address: str, port: Any, mode: str) -> str:
        if mode == "tcping":
            return self._tcping(address, port)
        return self._icmp(address)

    def urltest_node(self, node_id: str) -> str:
        if self.device.transport == "http":
            return parse_ping_payload(self._luci("urltest_node", {"id": node_id}, timeout=25))
        self.device.require(Capability.SHELL)
        script = "/usr/share/passwall2/test.sh"
        if not self.device.fs.exists(script):
            raise CapabilityError(t("err.pw2_no_urltest"), missing=("urltest",), hint=t("err.use_ssh"))
        raw = self.device.shell.exec(
            f"{_shell_quote(script)} url_test_node {_shell_quote(node_id)} 2>/dev/null",
            timeout=25,
        )
        return parse_ping_payload(raw)

    def _tcping(self, address: str, port: Any) -> str:
        if self.device.transport == "http":
            return parse_ping_payload(self._luci(
                "ping_node",
                {"address": address, "port": str(port), "type": "tcping"},
                timeout=12,
            ))
        self.device.require(Capability.SHELL)
        raw = self.device.shell.exec(
            f"tcping -q -c 1 -i 1 -t 2 -p {int(port)} {_shell_quote(address)} 2>/dev/null || true",
            timeout=8,
        )
        return parse_ping_payload(raw)

    def _icmp(self, address: str) -> str:
        if self.device.transport == "http":
            return parse_ping_payload(self._luci(
                "ping_node",
                {"address": address, "type": "icmp"},
                timeout=12,
            ))
        self.device.require(Capability.SHELL)
        raw = self.device.shell.exec(
            f"ping -c 1 -W 2 {_shell_quote(address)} 2>/dev/null "
            f"|| ping -c 1 -w 2 {_shell_quote(address)} 2>/dev/null || true",
            timeout=8,
        )
        return parse_ping_payload(raw)

    def subscribe(self) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        return self._ok({
            "subscribe": parse_subscribe(values),
            "filter": _options(_section(values, "global_subscribe")),
        }, "pw2_subscribe")

    def settings(self) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        data = parse_settings(values)
        data["detection"] = detection_mode(values)
        app = self._app_section(values)
        data["components"] = [self._com_row(app, name, keys) for name, keys in _KNOWN_COMS]
        return self._ok(data, "pw2_settings")

    def rules(self) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        return self._ok(parse_rules(values), "pw2_rules")

    def _app_section(self, values: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return _options(_section(values, "global_app"))

    def _com_path(self, app: dict[str, Any], name: str, keys: tuple[str, ...]) -> str:
        if name == "passwall2":
            return ""
        for key in keys:
            if app.get(key):
                return _as_text(app.get(key))
        return _COM_DEFAULT_PATH.get(name, "")

    def _normalize_com(self, name: str) -> str:
        key = (name or "").strip().lower()
        return _COM_ALIAS.get(key, key)

    def _com_row(self, app: dict[str, Any], name: str, keys: tuple[str, ...], *, remote: bool = False) -> dict[str, Any]:
        path = self._com_path(app, name, keys)
        row = {
            "name": name,
            "title": com_title(name),
            "path": path,
            "version": self._version(name, path),
        }
        if remote:
            row["remote"] = self._check_com(name, path)
        return row

    def components(self) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        app = self._app_section(values)
        rows = [self._com_row(app, name, keys) for name, keys in _KNOWN_COMS]
        return self._ok({"components": rows, "app": app}, "pw2_components")

    def components_check(self, name: str | None = None) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        app = self._app_section(values)
        lookup = {c[0]: c[1] for c in _KNOWN_COMS}
        wanted = [self._normalize_com(name)] if name else [c[0] for c in _KNOWN_COMS]
        rows = []
        for com in wanted:
            keys = lookup.get(com, (f"{com.replace('-', '_')}_file",) if com != "passwall2" else ())
            rows.append(self._com_row(app, com, keys, remote=True))
        return self._ok({"components": rows}, "pw2_components")

    def _version(self, name: str, path: str) -> str:
        if name == "passwall2":
            return self._passwall2_version()
        action = f"version_{name}"
        if self.device.transport == "http":
            try:
                return parse_version_payload(self._luci(action, timeout=15))
            except (DeviceCommandError, CapabilityError):
                return ""
        if not path:
            return ""
        self.device.require(Capability.SHELL)
        raw = self.device.shell.exec(f"{_shell_quote(path)} --version 2>/dev/null | head -n 3 || true", timeout=8)
        return parse_version_payload(raw)

    def _passwall2_version(self) -> str:
        fs = getattr(self.device, "fs", None)
        if fs is not None:
            for path in _PKG_CONTROL:
                try:
                    ver = parse_pkg_version(fs.read(path))
                except (CapabilityError, DeviceCommandError, OSError, AttributeError):
                    continue
                if ver:
                    return ver
        if self.device.transport == "http" or Capability.SHELL not in self.device.capabilities:
            return ""
        raw = self.device.shell.exec(
            "opkg list-installed luci-app-passwall2 2>/dev/null "
            "|| apk list -I luci-app-passwall2 2>/dev/null || true",
            timeout=8,
        )
        return parse_pkg_version(raw)

    def _check_com(self, name: str, path: str) -> str:
        action = "check_passwall2" if name == "passwall2" else f"check_{name}"
        if self.device.transport == "http":
            return parse_version_payload(self._luci(action, timeout=25))
        raise CapabilityError(t("err.pw2_no_check"), missing=("check",), hint=t("err.use_ssh"))

    def acl(self) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        return self._ok({
            "acl": parse_acl(values),
            "acl_enable": acl_enable(values),
        }, "pw2_acl")

    def acl_show(self, acl_id: str) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        row = next((a for a in parse_acl(values) if a["id"] == acl_id), None)
        if not row:
            return CommandResult.fail(
                t("err.pw2_no_acl", id=acl_id),
                transport=self.device.transport,
                data={"error": "pw2_no_acl", "id": acl_id},
            )
        return self._ok({"acl": row}, "pw2_acl_rule")

    def _acl_section(self, values: dict[str, dict[str, Any]], acl_id: str) -> dict[str, Any] | CommandResult:
        sec = values.get(acl_id)
        if not isinstance(sec, dict) or sec.get(".type") != "acl_rule":
            return CommandResult.fail(
                t("err.pw2_no_acl", id=acl_id),
                transport=self.device.transport,
                data={"error": "pw2_no_acl", "id": acl_id},
            )
        return sec

    def _acl_payload(self, acl_id: str, values: dict[str, dict[str, Any]], *, applied: bool) -> dict[str, Any]:
        row = next((a for a in parse_acl(values) if a["id"] == acl_id), None)
        return {
            "id": acl_id,
            "remarks": (row or {}).get("remarks") or acl_id,
            "applied": applied,
        }

    def _finish_write(
        self,
        payload: dict[str, Any],
        *,
        kind: str,
        apply: bool,
        saved: str,
        committed: str,
    ) -> CommandResult:
        applied = False
        warnings: list[str] = []
        if apply:
            applied, warn = self._apply()
            if warn:
                warnings.append(warn)
            payload = {**payload, "applied": applied}
        return CommandResult.ok_data(
            payload,
            transport=self.device.transport,
            kind=kind,
            message=saved if applied else committed,
            warnings=warnings,
        )

    def acl_add(
        self,
        fields: dict[str, Any] | None = None,
        *,
        apply: bool = False,
        raw: bool = False,
    ) -> CommandResult:
        from openwrt_cli.services.pw2_acl_schema import validate_acl

        values = self._require()
        if isinstance(values, CommandResult):
            return values
        draft = dict(fields or {})
        if "enabled" not in draft:
            draft["enabled"] = "1"
        ok, errors, normalized = validate_acl(draft, raw=raw)
        if not ok:
            return CommandResult.fail(
                t("err.pw2_acl_invalid", error="; ".join(errors)),
                transport=self.device.transport,
                data={"error": "pw2_acl_invalid", "errors": errors},
            )
        try:
            acl_id = self.device.uci.add("passwall2", "acl_rule", values=normalized)
            self.device.uci.commit("passwall2")
        except DeviceCommandError as e:
            return self._write_error(e)
        fresh = self._values()
        return self._finish_write(
            self._acl_payload(acl_id, fresh, applied=False),
            kind="pw2_acl_rule",
            apply=apply,
            saved=t("msg.pw2_acl_saved"),
            committed=t("msg.pw2_acl_committed"),
        )

    def acl_set(
        self,
        acl_id: str,
        patch: dict[str, Any] | None = None,
        *,
        apply: bool = False,
        unset: tuple[str, ...] | list[str] = (),
        raw: bool = False,
    ) -> CommandResult:
        from openwrt_cli.services.pw2_acl_schema import FIELDS, validate_acl

        values = self._require()
        if isinstance(values, CommandResult):
            return values
        sec = self._acl_section(values, acl_id)
        if isinstance(sec, CommandResult):
            return sec
        existing = _options(sec)
        patch = dict(patch or {})
        if "remote_dns_protocol" in patch and "remote_dns_protocol" not in existing and "dns_mode" in existing:
            patch["dns_mode"] = patch.pop("remote_dns_protocol")
        clear = {key for key in unset if key}
        clearable = {item.key for item in FIELDS if item.clear_empty}
        for key, value in list(patch.items()):
            if value == "" and key in clearable:
                clear.add(key)
                patch.pop(key)
        ok, errors, normalized = validate_acl(patch, existing=existing, raw=raw, partial=True)
        if not ok:
            return CommandResult.fail(
                t("err.pw2_acl_invalid", error="; ".join(errors)),
                transport=self.device.transport,
                data={"error": "pw2_acl_invalid", "errors": errors},
            )
        for key in patch:
            if key not in normalized and key in clearable:
                clear.add(key)
        try:
            if normalized:
                self.device.uci.set_values("passwall2", acl_id, normalized)
            if clear:
                self.device.uci.delete("passwall2", acl_id, options=sorted(clear))
            self.device.uci.commit("passwall2")
        except DeviceCommandError as e:
            return self._write_error(e)
        fresh = self._values()
        return self._finish_write(
            self._acl_payload(acl_id, fresh, applied=False),
            kind="pw2_acl_rule",
            apply=apply,
            saved=t("msg.pw2_acl_saved"),
            committed=t("msg.pw2_acl_committed"),
        )

    def acl_delete(self, acl_id: str, *, apply: bool = False) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        sec = self._acl_section(values, acl_id)
        if isinstance(sec, CommandResult):
            return sec
        remarks = _as_text(sec.get("remarks")) or acl_id
        try:
            self.device.uci.delete("passwall2", acl_id)
            self.device.uci.commit("passwall2")
        except DeviceCommandError as e:
            return self._write_error(e)
        return self._finish_write(
            {"id": acl_id, "remarks": remarks, "deleted": True, "applied": False},
            kind="pw2_acl_rule",
            apply=apply,
            saved=t("msg.pw2_acl_deleted"),
            committed=t("msg.pw2_acl_deleted"),
        )

    def acl_source_add(self, acl_id: str, items: Any, *, apply: bool = False) -> CommandResult:
        from openwrt_cli.services.pw2_acl_schema import parse_sources

        values = self._require()
        if isinstance(values, CommandResult):
            return values
        sec = self._acl_section(values, acl_id)
        if isinstance(sec, CommandResult):
            return sec
        current = _as_list(sec.get("sources"))
        incoming = parse_sources(items)
        seen = set(current)
        added: list[str] = []
        new_list = list(current)
        for item in incoming:
            if item in seen:
                continue
            seen.add(item)
            new_list.append(item)
            added.append(item)
        try:
            self.device.uci.set_values("passwall2", acl_id, {"sources": new_list})
            self.device.uci.commit("passwall2")
        except DeviceCommandError as e:
            return self._write_error(e)
        return self._finish_write(
            {"id": acl_id, "sources": new_list, "added": added, "applied": False},
            kind="pw2_acl_source",
            apply=apply,
            saved=t("msg.pw2_acl_source_updated"),
            committed=t("msg.pw2_acl_source_updated"),
        )

    def acl_source_remove(self, acl_id: str, items: Any, *, apply: bool = False) -> CommandResult:
        from openwrt_cli.services.pw2_acl_schema import parse_sources

        values = self._require()
        if isinstance(values, CommandResult):
            return values
        sec = self._acl_section(values, acl_id)
        if isinstance(sec, CommandResult):
            return sec
        current = _as_list(sec.get("sources"))
        incoming = parse_sources(items)
        missing = [item for item in incoming if item not in current]
        if missing:
            return CommandResult.fail(
                t("err.pw2_acl_source_missing", items=", ".join(missing)),
                transport=self.device.transport,
                data={"error": "pw2_acl_source_missing", "id": acl_id, "missing": missing},
            )
        drop = set(incoming)
        new_list = [item for item in current if item not in drop]
        removed = [item for item in incoming if item in current]
        try:
            if new_list:
                self.device.uci.set_values("passwall2", acl_id, {"sources": new_list})
            else:
                self.device.uci.delete("passwall2", acl_id, options=["sources"])
            self.device.uci.commit("passwall2")
        except DeviceCommandError as e:
            return self._write_error(e)
        return self._finish_write(
            {"id": acl_id, "sources": new_list, "removed": removed, "applied": False},
            kind="pw2_acl_source",
            apply=apply,
            saved=t("msg.pw2_acl_source_updated"),
            committed=t("msg.pw2_acl_source_updated"),
        )

    def acl_log(
        self,
        acl_id: str,
        tail: int | None = None,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        verify: bool = True,
    ) -> CommandResult:
        if verify:
            values = self._require()
            if isinstance(values, CommandResult):
                return values
            if acl_id not in values or (values.get(acl_id) or {}).get(".type") != "acl_rule":
                return CommandResult.fail(
                    t("err.pw2_no_acl", id=acl_id),
                    transport=self.device.transport,
                    data={"error": "pw2_no_acl", "id": acl_id},
                )
        text = self._read_acl_log(acl_id)
        ok, lines, message = parse_acl_log_text(text)
        entries = filter_pw2_log_entries(
            [parse_pw2_log_entry(line) for line in lines],
            tail=tail,
            since=since,
            until=until,
        )
        return CommandResult(
            ok=ok,
            data={
                "id": acl_id,
                "enabled": ok,
                "log_file": acl_log_path(acl_id),
                "entries": entries,
                "count": len(entries),
                "tail": tail,
                **_pw2_log_window(since, until),
            },
            message=message,
            transport=self.device.transport,
            kind="pw2_acl_log",
        )

    def _read_acl_log(self, acl_id: str) -> str:
        if self.device.transport == "http":
            try:
                raw = self._luci("get_acl_log", {"id": acl_id}, timeout=20)
            except DeviceCommandError as e:
                if getattr(e, "status", None) is not None and e.status >= 500:
                    raise
                raw = self._luci("get_redir_log", {"id": acl_id}, timeout=20)
            return raw if isinstance(raw, str) else str(raw or "")
        for path in _acl_log_candidates(acl_id):
            if self.device.fs.exists(path):
                return self.device.fs.read(path)
        raise CapabilityError(t("err.pw2_no_acl_log", id=acl_id), missing=("file_read",))

    def logs(
        self,
        tail: int = 80,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CommandResult:
        values = self._require()
        if isinstance(values, CommandResult):
            return values
        if tail < 1:
            tail = 80
        text = self._read_run_log()
        entries = filter_pw2_log_entries(
            [parse_pw2_log_entry(line) for line in parse_log_text(text)],
            tail=tail,
            since=since,
            until=until,
        )
        return self._ok({
            "entries": entries,
            "count": len(entries),
            "tail": tail,
            **_pw2_log_window(since, until),
        }, "pw2_logs")

    def _read_run_log(self) -> str:
        if self.device.transport == "http":
            raw = self._luci("get_log", timeout=30)
            return raw if isinstance(raw, str) else str(raw or "")
        path = "/tmp/log/passwall2.log"
        if not self.device.fs.exists(path):
            raise CapabilityError(t("err.pw2_no_log"), missing=("file_read",))
        return self.device.fs.read(path)
