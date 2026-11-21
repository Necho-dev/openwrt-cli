"""OpenWrt FastMCP server. Importing this module requires the mcp extra."""

from __future__ import annotations

import asyncio
import functools
import re
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from openwrt_cli.core.config import ConfigManager, McpModeError, mcp_mode
from openwrt_cli.i18n import init_language, t
from openwrt_cli.mcp import handlers
from openwrt_cli.mcp.audit import CallMetadata, current_call, observe_target
from openwrt_cli.mcp.guard import TOOLS, WRITE_TOOLS, check_call
from openwrt_cli.mcp.payload import cap_payload, to_payload
from openwrt_cli.mcp.session import McpSession
from openwrt_cli.services.result import CommandResult
from openwrt_cli.services.runtime_logs import RuntimeLog, retention_settings

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)
_TIMEOUT = 60.0
_PING_TIMEOUT = 30.0

_session = McpSession()
_runtime_log: RuntimeLog | None = None
_WRITE_NAMES = frozenset(tool.name for tool in TOOLS if tool.level == "write")
mcp = FastMCP("OpenWrt")


def _bind_session(session: McpSession) -> McpSession:
    global _session
    _session = session
    return _session


def _safe(timeout: float = _TIMEOUT):
    def deco(fn: Callable[..., Any]):
        @functools.wraps(fn)
        async def wrap(*args: Any, **kwargs: Any):
            runtime_log = _runtime_log
            trace = CallMetadata()
            token = current_call.set(trace)
            start = time.monotonic()
            request_id = uuid.uuid4().hex
            result: dict = {}
            cancelled = False
            try:
                result = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "timeout", "message": f"tool timed out after {timeout:.0f}s"}
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": "exec", "message": f"{type(exc).__name__}: {exc}"}
            finally:
                current_call.reset(token)
                if runtime_log is not None:
                    _log_call(runtime_log, fn.__name__, trace, result, start, request_id, cancelled)
            return result
        return wrap
    return deco


def _log_call(log: RuntimeLog, tool: str, trace: CallMetadata, result: dict,
              start: float, request_id: str, cancelled: bool) -> None:
    profile, mode = trace.target
    raw = "cancelled" if cancelled else result.get("error")
    error = raw if isinstance(raw, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", raw) else None
    if result.get("ok"):
        outcome, level = "success", "INFO"
    elif error in {"mcp_readonly", "mcp_forbidden", "mcp_mode_invalid"}:
        outcome, level = "denied", "WARNING"
    elif error in {"timeout", "cancelled"}:
        outcome, level = error, "WARNING"
    else:
        outcome, level = "failed", "ERROR"
    execution = ""
    if tool in _WRITE_NAMES:
        execution = "unknown" if outcome in {"timeout", "cancelled"} else (
            "dispatched" if trace.dispatched else "not_dispatched"
        )
    log.emit("mcp", "mcp.call", level=level, profile=profile, tool=tool, mode=mode,
             outcome=outcome, error=error, duration_ms=round((time.monotonic() - start) * 1000),
             request_id=request_id, execution=execution or None)


def _run(logical: str, fn, *, need_host: bool = True, profile: str | None = None) -> dict[str, Any]:
    if not need_host:
        cfg = _session.load_cfg()
        observe_target(cfg)
        denied = check_call(logical, cfg)
        if denied is not None:
            return cap_payload(to_payload(denied))
        result = fn()
        if isinstance(result, CommandResult):
            return cap_payload(to_payload(result))
        return result
    return _session.invoke(logical, fn, profile=profile)


def _thread(logical: str, fn, *, need_host: bool = True, timeout: float = _TIMEOUT, profile: str | None = None):
    async def go():
        return await asyncio.to_thread(_run, logical, fn, need_host=need_host, profile=profile)
    return go()


@mcp.tool(annotations=_READ)
@_safe()
async def doctor(quick: bool = False, profile: str | None = None) -> dict:
    """OpenWrt health and capability check. First call on connect failure, missing LuCI apps, or 'is the router OK'. Returns structured checks (ok/warn/fail/skip). Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("doctor", lambda c: handlers.doctor(c, quick=quick), profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def system_status(profile: str | None = None) -> dict:
    """Router snapshot: hostname, load, memory, WAN, connections, disk, temperature, CPU, uptime. Use for 断网 triage and overview. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("system.status", handlers.system_status, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def system_processes(profile: str | None = None) -> dict:
    """Process table (CPU / memory). Read-only. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("system.processes", handlers.system_processes, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def logs_read(source: str = "system", tail: int = 80, since: str | None = None, until: str | None = None, profile: str | None = None) -> dict:
    """Read system (logd) or kernel (dmesg) logs. tail/since/until only — no follow. since examples: 10m, 2h. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("logs.read", lambda c: handlers.logs_read(c, source=source, tail=tail, since=since, until=until), profile=profile)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
@_safe()
async def samples_query(
    source: str, since: str | None = None, until: str | None = None, profile: str | None = None,
    subject: str | None = None, limit: int = 100, offset: int = 0, cursor: str | None = None,
) -> dict:
    """Read local SQLite sampling history and recorded coverage/gaps without connecting to a router.

    Sources: system, kernel, passwall2.runtime, passwall2.acl (all ACLs), passwall2.acl.<section>,
    openwrt.metrics, bandix.devices, bandix.history. Pass profile for this call only, or omit for active.
    Time bounds are inclusive and accept ISO 8601, Unix seconds/ms, or relative values (e.g. 3h).
    The default window is one hour ending at until (default now). Metrics can filter subject by MAC,
    all, or router as applicable. To continue, pass next_cursor as cursor with the same source and profile.
    The cursor preserves the original time bounds, subject, and collection snapshot. Offset starts a fresh query.
    Coverage reflects recorded collection batches, with no coverage inferred from missing data.
    """
    return await asyncio.to_thread(
        _session.invoke, "samples.query",
        lambda cfg: handlers.samples_query(cfg, source=source, since=since, until=until, subject=subject,
                                           limit=limit, offset=offset, cursor=cursor),
        profile=profile, local=True,
    )


@mcp.tool(annotations=_READ)
@_safe()
async def network_overview(profile: str | None = None) -> dict:
    """Interfaces, routes, DNS, and LAN address. Use for WAN/LAN/DHCP connectivity. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("network.overview", handlers.network_overview, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def network_neighbors(ip: str | None = None, mac: str | None = None, profile: str | None = None) -> dict:
    """IPv4 neighbors / ARP, with Bandix overlay when luci-app-bandix is present. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("network.neighbors", lambda c: handlers.network_neighbors(c, ip=ip, mac=mac), profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def network_leases(profile: str | None = None) -> dict:
    """DHCP leases with MAC vendors. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("network.leases", handlers.network_leases, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def network_static_leases(profile: str | None = None) -> dict:
    """List configured DHCP reservations. Pass profile to use that saved profile for this call only."""
    return await _thread("network.static_leases", handlers.network_static_leases, profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def network_set_static_lease(mac: str, ip: str, hostname: str, profile: str | None = None) -> dict:
    """Create or update a DHCP IPv4 reservation by MAC. Requires readwrite and user approval. Pass profile to use that saved profile for this call only."""
    return await _thread(
        "network.set_static_lease", lambda c: handlers.network_set_static_lease(c, mac, ip, hostname), profile=profile,
    )


@mcp.tool(annotations=_READ)
@_safe()
async def network_metrics(ip: str | None = None, mac: str | None = None, since: str | None = None, until: str | None = None, profile: str | None = None) -> dict:
    """Bandix traffic history (needs luci-app-bandix). Omit ip/mac for all-device total. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("network.metrics", lambda c: handlers.network_metrics(c, ip=ip, mac=mac, since=since, until=until), profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def firewall_view(profile: str | None = None) -> dict:
    """Firewall zones, UCI status, and redirects. iptables filter/NAT need SSH — this tool stays on UCI so HTTP works. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("firewall.view", handlers.firewall_view, profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def firewall_set_redirect(
    name: str, src_port: int, dest_ip: str, dest_port: int, src: str = "wan", dest: str = "lan",
    proto: str = "tcp", enabled: bool = True, profile: str | None = None,
) -> dict:
    """Create or update a DNAT rule by unique name. Requires readwrite and user approval. Pass profile to use that saved profile for this call only."""
    return await _thread(
        "firewall.set_redirect",
        lambda c: handlers.firewall_set_redirect(c, name, src_port, dest_ip, dest_port,
                                                src=src, dest=dest, proto=proto, enabled=enabled),
        profile=profile,
    )


@mcp.tool(annotations=_READ)
@_safe()
async def qos_view(profile: str | None = None) -> dict:
    """SQM / qos status. tc details need SSH; HTTP returns UCI and may be degraded. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("qos.view", handlers.qos_view, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def service_list(running: bool = False, profile: str | None = None) -> dict:
    """init.d / rc services. Set running=true to hide stopped ones. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("service.list", lambda c: handlers.service_list(c, running=running), profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def service_show(name: str, profile: str | None = None) -> dict:
    """One service: running state and UCI package summary (not a full dump). Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("service.show", lambda c: handlers.service_show(c, name), profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def passwall2_status(profile: str | None = None) -> dict:
    """PassWall2 current node, ACL switch, running state. Needs luci-app-passwall2. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.status", handlers.passwall2_status, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def passwall2_nodes(profile: str | None = None) -> dict:
    """PassWall2 node table without Ping/TCPing. Use passwall2_node_ping for latency. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.nodes", handlers.passwall2_nodes, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def passwall2_node_show(node_id: str, profile: str | None = None) -> dict:
    """All UCI options for one PassWall2 node. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.node_show", lambda c: handlers.passwall2_node_show(c, node_id), profile=profile)


@mcp.tool(annotations=_READ)
@_safe(_PING_TIMEOUT)
async def passwall2_node_ping(node_id: str, tcp: bool = False, profile: str | None = None) -> dict:
    """Ping one PassWall2 node (ICMP, or TCPing when tcp=true). Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.node_ping", lambda c: handlers.passwall2_node_ping(c, node_id, tcp=tcp), timeout=_PING_TIMEOUT, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def passwall2_acl(profile: str | None = None) -> dict:
    """PassWall2 ACL rules (acl_rule only). Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.acl", handlers.passwall2_acl, profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def passwall2_acl_show(acl_id: str, profile: str | None = None) -> dict:
    """One PassWall2 ACL rule. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.acl_show", lambda c: handlers.passwall2_acl_show(c, acl_id), profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def passwall2_logs(tail: int = 80, since: str | None = None, until: str | None = None, profile: str | None = None) -> dict:
    """PassWall2 runtime log (tail / since / until). Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.logs", lambda c: handlers.passwall2_logs(c, tail=tail, since=since, until=until), profile=profile)


@mcp.tool(annotations=_READ)
@_safe()
async def profiles_list() -> dict:
    """Saved router profiles: name, target, effective MCP permission, and which one is active. No passwords or key paths."""
    cfg = _session.load_cfg()
    return _run("profiles.list", lambda: handlers.profiles_list(cfg=cfg), need_host=False)


@mcp.tool(annotations=_READ)
@_safe()
async def profiles_current() -> dict:
    """The active profile: name, target, and effective MCP permission. Does not switch profiles or dial the router."""
    cfg = _session.load_cfg()
    return _run("profiles.current", lambda: handlers.profiles_current(cfg=cfg), need_host=False)


@mcp.tool(annotations=_READ)
@_safe()
async def config_show() -> dict:
    """Local ~/.openwrt/config.yaml globals: language, active profile name, mcp.mode and mcp.effective. No passwords."""
    cfg = _session.load_cfg()
    return _run("config.show", lambda: handlers.config_show(cfg=cfg, path=_session.config_path), need_host=False)


@mcp.tool(annotations=_WRITE)
@_safe()
async def network_set_hostname(mac: str, name: str, profile: str | None = None) -> dict:
    """Set or clear a Bandix neighbor hostname (empty name clears). Requires mcp.mode=readwrite. Ask the user before calling. Needs luci-app-bandix. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("network.set_hostname", lambda c: handlers.network_set_hostname(c, mac, name), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def wifi_set(section: str, ssid: str | None = None, key: str | None = None, profile: str | None = None) -> dict:
    """Change a wireless iface SSID/key. Can drop Wi-Fi clients. Requires readwrite and a user yes. section is the UCI wifi-iface name. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("network.wifi_set", lambda c: handlers.wifi_set(c, section, ssid=ssid, key=key), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def lan_set(ipaddr: str, profile: str | None = None) -> dict:
    """Set LAN IPv4. Can knock this computer off the router. Requires readwrite and an explicit user yes. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("network.lan_set", lambda c: handlers.lan_set(c, ipaddr), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def network_reload(interface: str | None = None, profile: str | None = None) -> dict:
    """Reload the network service (optionally one interface). Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("network.reload", lambda c: handlers.network_reload(c, interface), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def system_hostname(hostname: str, profile: str | None = None) -> dict:
    """Set the OpenWrt system hostname (not Bandix). Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("system.hostname_set", lambda c: handlers.system_hostname_set(c, hostname), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def service_action(name: str, action: str, profile: str | None = None) -> dict:
    """start/stop/restart/reload/enable/disable an init.d service. Requires readwrite. Confirm with the user first. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("service.action", lambda c: handlers.service_action(c, name, action), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def passwall2_node_add(from_url: str | None = None, fields: dict | None = None, apply: bool = False, profile: str | None = None) -> dict:
    """Create a PassWall2 node from a share URL and/or fields. apply restarts the service (confirm again). Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.node_add", lambda c: handlers.passwall2_node_add(c, from_url=from_url, fields=fields, apply=apply), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def passwall2_node_set(node_id: str, fields: dict | None = None, unset: list[str] | None = None, apply: bool = False, profile: str | None = None) -> dict:
    """Patch a PassWall2 node. Requires readwrite. apply restarts PassWall2. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.node_set", lambda c: handlers.passwall2_node_set(c, node_id, fields=fields, unset=unset, apply=apply), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def passwall2_node_delete(node_id: str, apply: bool = False, force: bool = False, profile: str | None = None) -> dict:
    """Delete a PassWall2 node. force skips the in-use guard. Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.node_delete", lambda c: handlers.passwall2_node_delete(c, node_id, apply=apply, force=force), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def passwall2_acl_add(fields: dict | None = None, apply: bool = False, profile: str | None = None) -> dict:
    """Create a PassWall2 ACL rule. Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.acl_add", lambda c: handlers.passwall2_acl_add(c, fields=fields, apply=apply), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def passwall2_acl_set(acl_id: str, fields: dict | None = None, unset: list[str] | None = None, apply: bool = False, profile: str | None = None) -> dict:
    """Patch a PassWall2 ACL rule. Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.acl_set", lambda c: handlers.passwall2_acl_set(c, acl_id, fields=fields, unset=unset, apply=apply), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def passwall2_acl_delete(acl_id: str, apply: bool = False, profile: str | None = None) -> dict:
    """Delete a PassWall2 ACL rule. Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.acl_delete", lambda c: handlers.passwall2_acl_delete(c, acl_id, apply=apply), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def passwall2_acl_source_add(acl_id: str, sources: list[str] | str, apply: bool = False, profile: str | None = None) -> dict:
    """Append Source items on an ACL. Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.acl_source_add", lambda c: handlers.passwall2_acl_source_add(c, acl_id, sources, apply=apply), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def passwall2_acl_source_remove(acl_id: str, sources: list[str] | str, apply: bool = False, profile: str | None = None) -> dict:
    """Remove Source items from an ACL. Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("passwall2.acl_source_remove", lambda c: handlers.passwall2_acl_source_remove(c, acl_id, sources, apply=apply), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def backup_create(output: str | None = None, profile: str | None = None) -> dict:
    """Create a config backup on the router under /tmp (basename only). Requires readwrite. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("backup.create", lambda c: handlers.backup_create(c, output), profile=profile)


@mcp.tool(annotations=_WRITE)
@_safe()
async def user_key_add(key_file: str | None = None, profile: str | None = None) -> dict:
    """Install an SSH public key on the router (local .pub path, or generate default). Requires readwrite. Does not add/delete users. Pass profile to use that saved profile for this call only; omit it to use the active profile."""
    return await _thread("user.key_add", lambda c: handlers.user_key_add(c, key_file), profile=profile)


def registered_tools() -> dict[str, Any]:
    """FastMCP tool registry (name → Tool). Layout differs across mcp versions."""
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        return {}
    tools = getattr(manager, "_tools", None)
    if isinstance(tools, dict) and tools:
        return tools
    listed = getattr(manager, "list_tools", None)
    if callable(listed):
        rows = listed()
        if isinstance(rows, dict):
            return rows
        out: dict[str, Any] = {}
        for tool in rows or []:
            name = getattr(tool, "name", None)
            if name:
                out[name] = tool
        return out
    return {}


def tool_annotations() -> dict[str, ToolAnnotations]:
    """For tests: name → annotations on registered tools."""
    out: dict[str, ToolAnnotations] = {}
    for name, tool in registered_tools().items():
        ann = getattr(tool, "annotations", None)
        if ann is not None:
            out[name] = ann
    return out


def write_tool_names() -> frozenset[str]:
    return frozenset(WRITE_TOOLS)


def main() -> None:
    global _runtime_log
    init_language()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    _bind_session(McpSession())
    journal = RuntimeLog("mcp", policy=lambda: retention_settings(ConfigManager().load()))
    _runtime_log = journal
    journal.emit("mcp", "mcp.starting")
    try:
        cfg = _session.load_cfg()
        try:
            mode = mcp_mode(cfg)
        except McpModeError as e:
            print(t("err.mcp_mode", mode=e.mode), file=sys.stderr)
            raise SystemExit(2) from e
        journal.emit("mcp", "mcp.started", profile=cfg.get("active"), mode=mode)
        print(
            f"openwrt-mcp  mode={mode}  config={_session.config_path}\n"
            "stdio JSON-RPC (stdout reserved)",
            file=sys.stderr,
            flush=True,
        )
        mcp.run()
    except (Exception, SystemExit) as exc:
        journal.emit("mcp", "mcp.failed", level="ERROR", error=type(exc).__name__)
        raise
    finally:
        try:
            _session.close()
        finally:
            journal.emit("mcp", "mcp.stopped")
            journal.close()
            _runtime_log = None


if __name__ == "__main__":
    main()
