from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult

_ROLE_LABEL = {
    "lan": "LAN",
    "wan": "WAN",
    "wan6": "WAN6",
    "loopback": "LOOPBACK",
    "lo": "LOOPBACK",
}
_LOGICAL_IFACE = frozenset(_ROLE_LABEL)


def _join_addrs(items) -> str:
    if isinstance(items, str):
        return items or "—"
    values = [str(v) for v in (items or []) if v]
    return ", ".join(values) if values else "—"


def _role_label(role: str | None, name: str | None = None) -> str:
    key = (role or name or "").strip().lower()
    if key in _ROLE_LABEL:
        return _ROLE_LABEL[key]
    if key.startswith("wan"):
        return key.upper()
    if key.startswith("lan"):
        return key.upper()
    if key in {"lo", "dummy0"}:
        return "LOOPBACK"
    return ""


def display_iface(name: str | None, role: str | None = None, device: str | None = None) -> str:
    """Pretty-print ROLE(device), e.g. WAN(eth0). JSON still uses raw names."""
    raw_name = (name or "").strip()
    raw_role = (role or "").strip()
    raw_dev = (device or "").strip()
    title = _role_label(raw_role, raw_name)
    phys = ""
    for candidate in (raw_dev, raw_name):
        if candidate and candidate.lower() not in _LOGICAL_IFACE:
            phys = candidate
            break
    if title == "LOOPBACK":
        return f"LOOPBACK({phys or 'lo'})"
    if title and phys:
        return f"{title}({phys})"
    if title:
        return title
    return raw_name or raw_dev or "—"


def display_route_iface(route: dict[str, Any]) -> str:
    iface = str(route.get("iface") or "").strip()
    dev = str(route.get("dev") or "").strip()
    if iface:
        return display_iface(dev, role=iface, device=dev)
    return f"({dev})" if dev else "—"


def clip_mid(text: str | None, width: int = 36) -> str:
    """Ellipsize a long command in the middle."""
    s = " ".join(str(text or "").split())
    if len(s) <= width:
        return s
    keep = max(width - 1, 3)
    left = keep // 2
    right = keep - left
    return f"{s[:left]}…{s[-right:]}"


def emit_failure(
    console: Console,
    fmt: str,
    message: str,
    *,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Structured error for Agent JSON; colored text otherwise."""
    payload: dict[str, Any] = {"ok": False, "message": message}
    if error:
        payload["error"] = error
    if extra:
        payload.update(extra)
    if fmt == "json":
        console.print_json(data=payload)
        return
    if fmt == "compact":
        console.print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    console.print(f"[error]✗ {message}[/error]")


def emit(console: Console, result: CommandResult, fmt: str) -> None:
    if fmt == "json":
        console.print_json(data=_to_payload(result))
        return
    if fmt == "compact":
        console.print(json.dumps(_to_payload(result), ensure_ascii=False, separators=(",", ":")))
        return
    if result.warnings:
        for w in result.warnings:
            console.print(f"[warning]⚠ {w}[/warning]")
    if result.message and not result.data:
        style = "success" if result.ok else "error"
        mark = "✓" if result.ok else "✗"
        console.print(f"[{style}]{mark} {result.message}[/{style}]")
        return
    _render_text(console, result)


def _to_payload(result: CommandResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": result.ok,
        "transport": result.transport,
        "degraded": result.degraded,
    }
    if result.message:
        payload["message"] = result.message
    if result.warnings:
        payload["warnings"] = result.warnings
    if isinstance(result.data, dict):
        payload.update(result.data)
    elif result.data is not None:
        payload["data"] = result.data
    if result.kind:
        payload["kind"] = result.kind
    return payload


def _render_text(console: Console, result: CommandResult) -> None:
    data = result.data
    kind = result.kind or (next(iter(data)) if isinstance(data, dict) and data else None)

    if kind == "config" and isinstance(data, dict):
        _render_config(console, data)
        return
    if kind == "status" and isinstance(data, dict):
        _render_status(console, data)
        return
    if kind == "metrics" and isinstance(data, dict):
        _render_metrics(console, data)
        return
    if kind == "leases" and isinstance(data, dict):
        _table(console, [t("col.ip"), t("col.mac"), t("col.device_type"), t("col.vendor"), t("col.hostname")], [
            [le.get("ip", ""), le.get("mac", ""), le.get("device_type") or "?", le.get("vendor") or t("unknown.vendor"), le.get("hostname") or "—"]
            for le in data.get("leases") or []
        ], empty=t("empty.leases"))
        return
    if kind == "neighbors" and isinstance(data, dict):
        if data.get("source") == "bandix":
            _table(console, [t("col.ip"), t("col.mac"), t("col.hostname_short"), t("col.interface"), t("col.vendor"), t("col.lan_down"), t("col.lan_up"), t("col.wan_down"), t("col.wan_up"), t("col.connections"), "TCP", "UDP"], [
                [
                    n.get("ip") or "—",
                    n.get("mac") or "—",
                    n.get("hostname") or "—",
                    display_iface(n.get("device")),
                    n.get("vendor") or "?",
                    f"{n.get('lan_rx_rate') or '—'} {n.get('lan_rx_human') or ''}".strip(),
                    f"{n.get('lan_tx_rate') or '—'} {n.get('lan_tx_human') or ''}".strip(),
                    f"{n.get('wan_rx_rate') or '—'} {n.get('wan_rx_human') or ''}".strip(),
                    f"{n.get('wan_tx_rate') or '—'} {n.get('wan_tx_human') or ''}".strip(),
                    n.get("conns", "—"),
                    n.get("tcp", "—"),
                    n.get("udp", "—"),
                ]
                for n in data.get("neighbors") or []
            ], empty=t("empty.neighbors"))
            return
        _table(console, [t("col.ip"), t("col.mac"), t("col.interface"), t("col.vendor"), t("col.connections"), t("col.received"), t("col.sent"), t("col.inbound"), t("col.outbound")], [
            [
                n.get("ip", ""),
                n.get("mac", ""),
                display_iface(n.get("device")),
                n.get("vendor") or t("unknown.vendor"),
                n.get("conns", "—"),
                n.get("rx_human") or "—",
                n.get("tx_human") or "—",
                n.get("rx_rate") or "—",
                n.get("tx_rate") or "—",
            ]
            for n in data.get("neighbors") or []
        ], empty=t("empty.neighbors"))
        return
    if kind in ("interfaces", "stats") and isinstance(data, dict):
        rows = data.get("interfaces") or []
        has_rates = any(i.get("rx_rate") or i.get("tx_rate") or i.get("rx_bps") is not None for i in rows)
        headers = [t("col.interface"), t("col.protocol"), t("col.carrier"), t("col.uptime"), t("col.mac"), t("col.ipv4"), t("col.ipv6"), t("col.rx"), t("col.tx"), t("col.device")]
        table_rows = []
        for i in rows:
            row = [
                display_iface(
                    i.get("name"),
                    role=i.get("role") or i.get("label"),
                    device=i.get("l3_device") or i.get("device"),
                ),
                i.get("proto_human") or i.get("proto") or "—",
                i.get("carrier_human") or "—",
                i.get("uptime_human") or "—",
                i.get("mac") or "—",
                _join_addrs(i.get("ipv4") or i.get("ipaddr")),
                _join_addrs(i.get("ipv6")),
                i.get("rx_detail") or i.get("rx_human") or "—",
                i.get("tx_detail") or i.get("tx_human") or "—",
            ]
            if has_rates:
                row.extend([i.get("rx_rate") or "—", i.get("tx_rate") or "—"])
            row.append(i.get("device") or i.get("l3_device") or i.get("name") or "")
            table_rows.append(row)
        if has_rates:
            headers = [t("col.interface"), t("col.protocol"), t("col.carrier"), t("col.uptime"), t("col.mac"), t("col.ipv4"), t("col.ipv6"), t("col.rx"), t("col.tx"), t("col.inbound"), t("col.outbound"), t("col.device")]
        _table(console, headers, table_rows)
        return
    if kind == "traffic" and isinstance(data, dict):
        _table(console, [t("col.interface"), t("col.status"), t("col.link"), t("col.received"), t("col.sent"), t("col.inbound"), t("col.outbound")], [
            [
                display_iface(
                    i.get("name"),
                    role=i.get("role") or i.get("label"),
                    device=i.get("l3_device") or i.get("device"),
                ),
                "up" if i.get("up") else ("down" if i.get("up") is False else "?"),
                i.get("speed_human") or "—",
                i.get("rx_human") or "—",
                i.get("tx_human") or "—",
                i.get("rx_rate") or "—",
                i.get("tx_rate") or "—",
            ]
            for i in data.get("interfaces") or []
        ])
        console.print()
        _table(console, [t("col.ip"), t("col.mac"), t("col.interface"), t("col.connections"), t("col.received"), t("col.sent"), t("col.inbound"), t("col.outbound")], [
            [
                n.get("ip", ""),
                n.get("mac", ""),
                display_iface(n.get("device")),
                n.get("conns", "—"),
                n.get("rx_human") or "—",
                n.get("tx_human") or "—",
                n.get("rx_rate") or "—",
                n.get("tx_rate") or "—",
            ]
            for n in data.get("neighbors") or []
        ], empty=t("empty.neighbors"))
        return
    if kind == "routes" and isinstance(data, dict):
        _table(console, [
            t("col.device"), t("col.destination"), t("col.gateway"), t("col.source"),
            t("col.metric"), t("col.table"), t("col.protocol"),
        ], [
            [
                display_route_iface(r),
                r.get("dest") or "—",
                r.get("via") or "—",
                r.get("src") or "—",
                r.get("metric") if r.get("metric") not in (None, "") else "—",
                r.get("table") or "—",
                r.get("proto") or "—",
            ]
            for r in data.get("routes") or []
        ], empty=t("empty.routes"))
        return
    if kind == "rules" and isinstance(data, dict):
        def _rule_cell(value) -> str:
            return "—" if value in (None, "") else str(value)

        _table(console, [
            t("col.rule"), t("col.priority"), t("col.ingress"), t("col.source"),
            t("col.src_port"), t("col.action"), t("col.ip_protocol"), t("col.egress"),
            t("col.destination"), t("col.dst_port"), t("col.table"),
        ], [
            [
                _rule_cell(r.get("rule")),
                _rule_cell(r.get("priority")),
                _rule_cell(r.get("iif")),
                _rule_cell(r.get("src")),
                _rule_cell(r.get("sport")),
                _rule_cell(r.get("action")),
                _rule_cell(r.get("ipproto")),
                _rule_cell(r.get("oif")),
                _rule_cell(r.get("dest")),
                _rule_cell(r.get("dport")),
                _rule_cell(r.get("table")),
            ]
            for r in data.get("rules") or []
        ], empty=t("empty.rules"))
        return
    if kind == "services" and isinstance(data, dict):
        _table(console, [t("col.service"), t("col.priority"), t("col.running"), t("col.startup")], [
            [
                s.get("name", ""),
                s.get("priority") if s.get("priority") is not None else "—",
                t("label.yes") if s.get("running") else t("label.no"),
                t("label.yes") if s.get("enabled") else t("label.no"),
            ]
            for s in data.get("services") or []
        ])
        return
    if kind == "users" and isinstance(data, dict):
        _table(console, [t("col.user"), "UID", "GID", t("col.home"), t("col.shell")], [
            [u.get("username", ""), u.get("uid", ""), u.get("gid", ""), u.get("home", ""), u.get("shell", "")]
            for u in data.get("users") or []
        ])
        return
    if kind == "checks" and isinstance(data, dict):
        _render_checks(console, data.get("checks") or [], result)
        return
    if kind == "processes" and isinstance(data, dict):
        _table(console, [t("col.process"), "PID", t("col.user"), "CPU", t("col.memory"), t("col.command")], [
            [
                p.get("name", ""),
                p.get("pid", ""),
                p.get("user", ""),
                p.get("cpu", ""),
                p.get("mem", ""),
                clip_mid(p.get("command") or p.get("comm", "")),
            ]
            for p in data.get("processes") or []
        ])
        return
    if kind == "logs" and isinstance(data, dict):
        entries = data.get("entries") or []
        if not entries:
            console.print(f"[muted]{t('empty.logs')}[/muted]")
            return
        from openwrt_cli.services.system import format_log_line
        for entry in entries:
            console.print(format_log_line(entry))
        return
    if kind == "wifi" and isinstance(data, dict):
        _table(console, [t("col.section"), t("col.device"), "SSID", t("col.network"), t("col.disabled")], [
            [w.get("section", ""), w.get("device", ""), w.get("ssid", ""), w.get("network", ""), str(w.get("disabled", ""))]
            for w in data.get("wifi") or []
        ], empty=t("empty.wifi"))
        return
    if kind == "pw2_status" and isinstance(data, dict):
        _table(console, [t("col.item"), t("col.value")], [
            [t("pw2.installed"), t("label.yes") if data.get("installed") else t("label.no")],
            [t("col.running"), t("label.yes") if data.get("running") else t("label.no")],
            [t("col.node"), data.get("node_remarks") or data.get("node") or "—"],
            [t("pw2.acl_enable"), t("label.yes") if data.get("acl_enable") else t("label.no")],
            [t("pw2.detection"), data.get("detection") or "—"],
        ])
        return
    if kind == "pw2_nodes" and isinstance(data, dict):
        _table(console, [
            t("col.section"), t("col.remarks"), t("col.group"), t("col.type"),
            t("col.protocol"), t("col.address"), t("col.port"),
            t("col.ping"), t("col.tcping"),
        ], [
            [
                n.get("id") or "",
                n.get("remarks") or "—",
                n.get("group") or "—",
                n.get("type") or "—",
                n.get("protocol") or "—",
                n.get("address") or "—",
                n.get("port") or "—",
                n.get("ping") or "—",
                n.get("tcping") or "—",
            ]
            for n in data.get("nodes") or []
        ], empty=t("empty.pw2_nodes"))
        return
    if kind == "pw2_subscribe" and isinstance(data, dict):
        _table(console, [t("col.section"), t("col.remarks"), t("col.nodes"), t("col.url")], [
            [s.get("id") or "", s.get("remarks") or "—", s.get("node_count") if s.get("node_count") is not None else "—", s.get("url") or "—"]
            for s in data.get("subscribe") or []
        ], empty=t("empty.pw2_subscribe"))
        filt = data.get("filter") or {}
        if filt:
            console.print()
            _dict_lines(console, {"filter": filt})
        return
    if kind == "pw2_rules" and isinstance(data, dict):
        from openwrt_cli.services.passwall2 import format_uci_value, parse_geo_rules, uci_label

        geo = parse_geo_rules(data.get("global_rules") or {})
        if geo:
            _table(console, [t("col.item"), t("col.value")], [
                [uci_label(k), format_uci_value(k, v).replace("\n", " ")]
                for k, v in geo
            ])
            console.print()
        _table(console, [
            t("col.section"), t("col.remarks"), t("col.network"),
            t("col.domains"), t("col.ips"),
        ], [
            [
                r.get("id") or "",
                r.get("remarks") or "—",
                r.get("network") or "—",
                r.get("domain_count") if r.get("domain_count") is not None else 0,
                r.get("ip_count") if r.get("ip_count") is not None else 0,
            ]
            for r in data.get("shunt_rules") or []
        ], empty=t("empty.pw2_rules"))
        return
    if kind == "pw2_components" and isinstance(data, dict):
        _table(console, [t("col.component"), t("col.path"), t("col.local"), t("col.remote")], [
            [
                c.get("title") or c.get("name") or "—",
                c.get("path") or "—",
                c.get("version") or "—",
                c.get("remote") or "—",
            ]
            for c in data.get("components") or []
        ], empty=t("empty.none"))
        return
    if kind == "pw2_acl" and isinstance(data, dict):
        from openwrt_cli.services.passwall2 import summarize_list

        _table(console, [
            t("col.section"), t("col.enabled"), t("col.remarks"), t("col.sources"), t("col.node"),
        ], [
            [
                a.get("id") or "",
                t("label.yes") if a.get("enabled") else t("label.no"),
                a.get("remarks") or "—",
                summarize_list(a.get("sources")),
                a.get("node_remarks") or a.get("node") or "—",
            ]
            for a in data.get("acl") or []
        ], empty=t("empty.pw2_acl"))
        return
    if kind == "pw2_logs" and isinstance(data, dict):
        from openwrt_cli.services.passwall2 import log_messages

        lines = log_messages(data.get("entries"))
        if not lines:
            console.print(f"[muted]{t('empty.pw2_logs')}[/muted]")
            return
        for line in lines:
            console.print(line)
        return
    if kind == "pw2_acl_log" and isinstance(data, dict):
        from openwrt_cli.services.passwall2 import log_messages

        if not data.get("enabled"):
            if result.message:
                console.print(f"[warning]⚠ {result.message}[/warning]")
            return
        lines = log_messages(data.get("entries") or data.get("lines"))
        if not lines:
            console.print(f"[muted]{t('empty.pw2_logs')}[/muted]")
            return
        for line in lines:
            console.print(line)
        return
    if kind == "pw2_acl_rule" and isinstance(data, dict) and data.get("acl"):
        from openwrt_cli.services.passwall2 import format_uci_value, uci_label

        row = data.get("acl") or {}
        keys = (
            "id", "enabled", "sources", "node", "node_remarks",
            "tcp_no_redir_ports", "udp_no_redir_ports", "tcp_redir_ports", "udp_redir_ports",
            "dns_mode", "remote_dns", "log", "loglevel", "log_file",
        )
        _table(console, [t("col.item"), t("col.value")], [
            [uci_label(key), format_uci_value(key, row.get(key))]
            for key in keys
        ])
        return
    if kind == "pw2_acl_rule" and isinstance(data, dict):
        rows = [
            [t("pw2.opt.id"), data.get("id") or "—"],
            [t("pw2.opt.remarks"), data.get("remarks") or "—"],
            [t("pw2.acl.applied"), t("label.yes") if data.get("applied") else t("label.no")],
        ]
        if data.get("deleted"):
            rows.append([t("pw2.acl.deleted"), t("label.yes")])
        _table(console, [t("col.item"), t("col.value")], rows)
        return
    if kind == "pw2_acl_source" and isinstance(data, dict):
        from openwrt_cli.services.passwall2 import format_uci_value, uci_label

        change = data.get("added") if "added" in data else data.get("removed")
        change_key = "pw2.acl.added" if "added" in data else "pw2.acl.removed"
        _table(console, [t("col.item"), t("col.value")], [
            [t("pw2.opt.id"), data.get("id") or "—"],
            [uci_label("sources"), format_uci_value("sources", data.get("sources"))],
            [t(change_key), format_uci_value("sources", change or [])],
            [t("pw2.acl.applied"), t("label.yes") if data.get("applied") else t("label.no")],
        ])
        return
    if kind == "pw2_node" and isinstance(data, dict) and data.get("node"):
        from openwrt_cli.services.passwall2 import format_uci_value, uci_label

        row = data.get("node") or {}
        keys = ("id", "remarks", "group", "type", "protocol", "address", "port")
        extra = [
            (k, v) for k, v in (row.get("options") or {}).items()
            if k not in {"remarks", "group", "type", "protocol", "address", "port", "add_from"}
        ]
        _table(console, [t("col.item"), t("col.value")], [
            [uci_label(key), format_uci_value(key, row.get(key))]
            for key in keys
        ] + [[uci_label(k), format_uci_value(k, v)] for k, v in extra])
        return
    if kind == "pw2_node" and isinstance(data, dict) and not data.get("node"):
        _table(console, [t("col.item"), t("col.value")], [
            [t("pw2.opt.id"), data.get("id") or "—"],
            [t("pw2.opt.remarks"), data.get("remarks") or "—"],
            [t("pw2.opt.type"), data.get("type") or "—"],
            [t("pw2.opt.protocol"), data.get("protocol") or "—"],
            [t("pw2.node.applied"), t("label.yes") if data.get("applied") else t("label.no")],
        ])
        return
    if kind == "pw2_ping" and isinstance(data, dict):
        latency = data.get("latency") or "—"
        console.print(f"{data.get('remarks') or data.get('id') or ''}  {data.get('mode') or ''}  {latency}".strip())
        return
    if kind == "pw2_settings" and isinstance(data, dict):
        from openwrt_cli.services.passwall2 import format_uci_value, uci_label

        rows: list[list[str]] = []
        for block in (
            "global_delay", "global_forwarding", "global_other",
            "global_haproxy", "global_xray", "global_singbox",
        ):
            fields = data.get(block) or {}
            if not fields:
                continue
            rows.append([uci_label(block, section=True), ""])
            for key, value in fields.items():
                rows.append([f"  {uci_label(key)}", format_uci_value(key, value).replace("\n", " ")])
        extra = data.get("extra") or {}
        if extra:
            rows.append([uci_label("extra", section=True), ""])
            for name, sec in extra.items():
                rows.append([f"  {name}", ""])
                if not isinstance(sec, dict):
                    continue
                for key, value in sec.items():
                    if key == "type":
                        continue
                    rows.append([f"    {uci_label(key)}", format_uci_value(key, value).replace("\n", " ")])
        comps = data.get("components") or []
        if comps:
            rows.append([uci_label("components", section=True), ""])
            for c in comps:
                title = c.get("title") or c.get("name") or "—"
                path = c.get("path") or "—"
                ver = c.get("version") or "—"
                rows.append([f"  {title}", f"{path}  {ver}".strip()])
        _table(console, [t("col.item"), t("col.value")], rows)
        return

    if result.message:
        style = "success" if result.ok else "error"
        console.print(f"[{style}]{'✓' if result.ok else '✗'} {result.message}[/{style}]")
    if isinstance(data, dict):
        _dict_lines(console, data)
    elif data is not None:
        console.print(data)


def _fmt_load(values) -> str:
    items = [str(v) for v in (values or []) if v is not None]
    return " / ".join(items) if items else "—"


def _mem_line(memory: dict[str, Any] | None) -> str:
    mem = memory or {}
    total = (mem.get("total") or {}).get("mb")
    avail = (mem.get("available") or mem.get("free") or {}).get("mb")
    if not total:
        return "—"
    used = max(0.0, float(total) - float(avail or 0))
    pct = used / float(total) * 100
    return f"{pct:.0f}%  {avail or 0:.0f}/{total:.0f} MB"


def _ts_label(ts) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return "—"


def _render_config(console: Console, data: dict[str, Any]) -> None:
    transport = (data.get("transport") or "").lower()
    rows = [
        [t("cfg.path"), data.get("path") or "—"],
        [t("cfg.host"), data.get("host") or "—"],
        [t("cfg.user"), data.get("user") or "—"],
        [t("cfg.transport"), data.get("transport") or "—"],
    ]
    if transport == "http":
        rows.append([t("cfg.scheme"), data.get("scheme") or "—"])
    rows.append([t("cfg.port"), data.get("port") if data.get("port") is not None else "—"])
    rows.append([t("cfg.language"), data.get("language") or "—"])
    if data.get("identity_file"):
        rows.append([t("cfg.identity"), data.get("identity_file")])
    if transport == "http":
        verify = data.get("verify_ssl")
        rows.append([t("cfg.verify_ssl"), t("label.yes") if verify else t("label.no")])
        rows.append([t("cfg.url"), f"{data.get('scheme') or 'https'}://{data.get('host') or '—'}:{data.get('port') or '—'}/ubus"])
    rows.append([t("cfg.password"), data.get("password") or "—"])
    _table(console, [t("col.item"), t("col.value")], rows)


def _render_status(console: Console, data: dict[str, Any]) -> None:
    wan = data.get("wan") or {}
    conn = data.get("connections") or {}
    uptime = data.get("uptime") or {}
    rows = [
        [t("status.hostname"), data.get("hostname") or "—"],
        [t("status.version"), data.get("version") or "—"],
        [t("status.arch"), data.get("arch") or "—"],
        [t("status.cpu"), data.get("cpu") or "—"],
        [t("status.load"), _fmt_load(data.get("load"))],
        [t("status.memory"), _mem_line(data.get("memory"))],
        [t("status.uptime"), uptime.get("human") or "—"],
        [
            t("status.wan"),
            (
                f"{wan.get('name') or '—'}  ↓ {wan.get('rx_rate') or '—'}  ↑ {wan.get('tx_rate') or '—'}"
                if wan
                else "—"
            ),
        ],
        [
            t("status.connections"),
            (
                f"udp={conn.get('udp') if conn.get('udp') is not None else '—'}  "
                f"tcp={conn.get('tcp') if conn.get('tcp') is not None else '—'}  "
                f"other={conn.get('other') if conn.get('other') is not None else '—'}  "
                f"total={conn.get('total') if conn.get('total') is not None else '—'}"
            ),
        ],
    ]
    _table(console, [t("col.item"), t("col.value")], rows)


def _render_metrics(console: Console, data: dict[str, Any]) -> None:
    target = data.get("target") or "all"
    mac = data.get("mac") or ""
    obj = t("label.all_devices") if target == "all" else f"{target}"
    if mac and mac != "all" and target != mac:
        obj = f"{obj}  ({mac})"
    console.print(t("metrics.object", obj=obj))
    console.print(t("metrics.range", since=_ts_label(data.get("since")), until=_ts_label(data.get("until"))))
    console.print(t("metrics.retention", seconds=data.get("retention_seconds") or "—"))
    latest = data.get("latest") or {}
    average = data.get("average") or {}
    peak = data.get("peak") or {}
    _table(console, ["", "↓", "↑"], [
        [t("metrics.latest"), latest.get("rx_rate") or "—", latest.get("tx_rate") or "—"],
        [t("metrics.average"), average.get("rx_rate") or "—", average.get("tx_rate") or "—"],
        [t("metrics.peak"), peak.get("rx_rate") or "—", peak.get("tx_rate") or "—"],
    ])


def _render_checks(console: Console, checks: list[dict], result: CommandResult) -> None:
    table = Table(box=None, show_header=True, header_style="accent")
    table.add_column(t("col.item"))
    table.add_column(t("col.status"))
    table.add_column(t("col.detail"))
    marks = {"ok": "[success]ok[/success]", "warn": "[warning]warn[/warning]", "fail": "[error]fail[/error]", "skip": "[muted]skip[/muted]"}
    for c in checks:
        detail = " ".join(c.get("details") or [])[:80]
        table.add_row(t(f"doctor.{c.get('id')}") if c.get("id") else c.get("title", ""), marks.get(c.get("status"), c.get("status", "")), detail)
    console.print(table)
    if result.message:
        console.print(result.message)


def _table(console: Console, headers: list[str], rows: list[list[Any]], empty: str | None = None) -> None:
    empty = empty if empty is not None else t("empty.none")
    if not rows:
        console.print(f"[muted]{empty}[/muted]")
        return
    table = Table(header_style="accent", show_lines=False)
    for h in headers:
        table.add_column(h)
    for row in rows:
        table.add_row(*[str(c) if c is not None else "" for c in row])
    console.print(table)


def _dict_lines(console: Console, data: dict[str, Any], indent: int = 0) -> None:
    skip = {"ok", "transport", "degraded"}
    for key, value in data.items():
        if key in skip:
            continue
        prefix = "  " * indent
        if isinstance(value, dict):
            console.print(f"{prefix}[accent]{key}[/accent]")
            _dict_lines(console, value, indent + 1)
        elif isinstance(value, list):
            console.print(f"{prefix}[accent]{key}[/accent]")
            for item in value[:30]:
                if isinstance(item, dict):
                    _dict_lines(console, item, indent + 1)
                    console.print()
                else:
                    console.print(f"{prefix}  - {item}")
        else:
            console.print(f"{prefix}{key}: {value}")


def setup_complete_panel(rows: list[tuple[str, str]], next_cmds: list[str]) -> Panel:
    table = Table(header_style="accent", show_lines=False, box=None, padding=(0, 2))
    table.add_column(t("col.item"), style="muted")
    table.add_column(t("col.value"))
    for k, v in rows:
        table.add_row(k, f"[cyan]{v}[/cyan]")
    inner = Table.grid()
    inner.add_row(table)
    inner.add_row(Text(""))
    inner.add_row(Text(t("setup.next"), style="muted"))
    for cmd in next_cmds:
        inner.add_row(Text(f"  {cmd}", style="accent"))
    return Panel(inner, title=f"[success]{t('setup.complete')}[/success]", border_style="green")
