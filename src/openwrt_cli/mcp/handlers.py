"""Service-facing MCP handlers. No FastMCP dependency."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from openwrt_cli.core.config import public_config
from openwrt_cli.core.device import DeviceClient
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError
from openwrt_cli.i18n import t
from openwrt_cli.services.backup import BackupService
from openwrt_cli.services.doctor import DoctorService
from openwrt_cli.services.firewall import FirewallService
from openwrt_cli.services.monitor import MonitorService
from openwrt_cli.services.network import NetworkService
from openwrt_cli.services.passwall2 import PassWall2Service
from openwrt_cli.services.qos import QoSService
from openwrt_cli.services.result import CommandResult
from openwrt_cli.services.service import ServiceService
from openwrt_cli.services.system import SystemService, parse_log_time
from openwrt_cli.services.user import UserService


def _dt(label: str, value: str | None) -> datetime | None:
    if not value:
        return None
    return parse_log_time(value)


def _merge_ok(*results: CommandResult) -> tuple[dict[str, Any], list[str], bool, str]:
    data: dict[str, Any] = {}
    warnings: list[str] = []
    degraded = False
    transport = ""
    for result in results:
        transport = transport or result.transport
        degraded = degraded or result.degraded
        warnings.extend(result.warnings or [])
        if not result.ok:
            return {}, [result.message or "failed"], True, transport
        if isinstance(result.data, dict):
            data.update(result.data)
    return data, warnings, degraded, transport


def doctor(device: DeviceClient, *, quick: bool = False) -> CommandResult:
    return DoctorService(device).run(quick=quick)


def system_status(device: DeviceClient) -> CommandResult:
    mon = MonitorService(device)
    status = mon.status()
    extras: list[CommandResult] = []
    for fn in (mon.disk, mon.temperature, mon.uptime):
        try:
            extras.append(fn())
        except (DeviceCommandError, CapabilityError, KeyError, TypeError, ValueError):
            continue
    blob = dict(status.data or {})
    warnings = list(status.warnings or [])
    degraded = status.degraded
    for extra in extras:
        if extra.ok and isinstance(extra.data, dict):
            blob[extra.kind or "extra"] = extra.data
        warnings.extend(extra.warnings or [])
        degraded = degraded or extra.degraded
    return CommandResult.ok_data(
        blob,
        transport=device.transport,
        kind="system_status",
        warnings=warnings,
        degraded=degraded,
    )


def system_processes(device: DeviceClient) -> CommandResult:
    return MonitorService(device).processes()


def logs_read(
    device: DeviceClient,
    *,
    source: str = "system",
    tail: int = 80,
    since: str | None = None,
    until: str | None = None,
) -> CommandResult:
    src = (source or "system").lower()
    if src not in {"system", "kernel"}:
        return CommandResult.fail(t("err.logs_source"), data={"error": "bad_source"})
    try:
        since_dt = _dt("since", since)
        until_dt = _dt("until", until)
    except ValueError as e:
        return CommandResult.fail(t("err.time_parse", label="time", error=e), data={"error": "time_parse"})
    return SystemService(device).logs(
        kernel=src == "kernel",
        tail=max(1, min(int(tail or 80), 500)),
        since=since_dt,
        until=until_dt,
    )


def network_overview(device: DeviceClient) -> CommandResult:
    net = NetworkService(device)
    parts: list[CommandResult] = []
    for fn in (net.interfaces, net.routes, net.dns, net.lan_show):
        try:
            parts.append(fn())
        except (DeviceCommandError, CapabilityError) as e:
            parts.append(CommandResult.fail(str(e), transport=device.transport))
    data: dict[str, Any] = {}
    warnings: list[str] = []
    degraded = False
    for result, key in zip(parts, ("interfaces", "routes", "dns", "lan"), strict=True):
        warnings.extend(result.warnings or [])
        if not result.ok:
            data[key] = {"ok": False, "message": result.message}
            degraded = True
            continue
        blob = result.data if isinstance(result.data, dict) else {"data": result.data}
        data[key] = blob
        degraded = degraded or result.degraded
    return CommandResult.ok_data(data, transport=device.transport, kind="network_overview", warnings=warnings, degraded=degraded)


def network_neighbors(device: DeviceClient, *, ip: str | None = None, mac: str | None = None) -> CommandResult:
    return NetworkService(device).neighbors(ip=ip or None, mac=mac or None, usage=True, live=False)


def network_leases(device: DeviceClient) -> CommandResult:
    return NetworkService(device).leases()


def network_metrics(
    device: DeviceClient,
    *,
    ip: str | None = None,
    mac: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> CommandResult:
    try:
        since_dt = _dt("since", since)
        until_dt = _dt("until", until)
    except ValueError as e:
        return CommandResult.fail(t("err.time_parse", label="time", error=e), data={"error": "time_parse"})
    return NetworkService(device).metrics(ip=ip or None, mac=mac or None, since=since_dt, until=until_dt)


def firewall_view(device: DeviceClient) -> CommandResult:
    fw = FirewallService(device)
    zones = fw.zones()
    status = fw.status()
    redirects = fw.redirects()
    data, warnings, degraded, transport = _merge_ok(zones, status, redirects)
    if not data and warnings:
        return CommandResult.fail(warnings[0], transport=transport)
    return CommandResult.ok_data(data, transport=transport or device.transport, kind="firewall", warnings=warnings, degraded=degraded)


def qos_view(device: DeviceClient) -> CommandResult:
    return QoSService(device).status()


def service_list(device: DeviceClient, *, running: bool = False) -> CommandResult:
    return ServiceService(device).list(running_only=running)


def service_show(device: DeviceClient, name: str) -> CommandResult:
    return ServiceService(device).show(name)


def passwall2_status(device: DeviceClient) -> CommandResult:
    return PassWall2Service(device).status()


def passwall2_nodes(device: DeviceClient) -> CommandResult:
    return PassWall2Service(device).nodes(measure=False)


def passwall2_node_show(device: DeviceClient, node_id: str) -> CommandResult:
    return PassWall2Service(device).node_show(node_id)


def passwall2_node_ping(device: DeviceClient, node_id: str, *, tcp: bool = False) -> CommandResult:
    return PassWall2Service(device).node_ping(node_id, mode="tcp" if tcp else None)


def passwall2_acl(device: DeviceClient) -> CommandResult:
    return PassWall2Service(device).acl()


def passwall2_acl_show(device: DeviceClient, acl_id: str) -> CommandResult:
    return PassWall2Service(device).acl_show(acl_id)


def passwall2_logs(
    device: DeviceClient,
    *,
    tail: int = 80,
    since: str | None = None,
    until: str | None = None,
) -> CommandResult:
    try:
        since_dt = _dt("since", since)
        until_dt = _dt("until", until)
    except ValueError as e:
        return CommandResult.fail(t("err.time_parse", label="time", error=e), data={"error": "time_parse"})
    return PassWall2Service(device).logs(tail=max(1, min(int(tail or 80), 500)), since=since_dt, until=until_dt)


def config_show(device: DeviceClient | None = None, *, cfg: dict | None = None, path: str | None = None) -> CommandResult:
    data = public_config(cfg or {}, path=path)
    return CommandResult.ok_data(data, transport=str((cfg or {}).get("transport") or ""), kind="config")


def network_set_hostname(device: DeviceClient, mac: str, name: str) -> CommandResult:
    return NetworkService(device).set_neighbor_hostname(mac, name)


def wifi_set(device: DeviceClient, section: str, ssid: str | None = None, key: str | None = None) -> CommandResult:
    return NetworkService(device).wifi_set(section, ssid=ssid, key=key)


def lan_set(device: DeviceClient, ipaddr: str) -> CommandResult:
    return NetworkService(device).lan_set(ipaddr)


def network_reload(device: DeviceClient, interface: str | None = None) -> CommandResult:
    return NetworkService(device).reload(interface)


def system_hostname_set(device: DeviceClient, hostname: str) -> CommandResult:
    return SystemService(device).hostname(hostname)


def service_action(device: DeviceClient, name: str, action: str) -> CommandResult:
    allowed = {"start", "stop", "restart", "reload", "enable", "disable"}
    if action not in allowed:
        return CommandResult.fail(t("err.mcp_bad_action", action=action), data={"error": "bad_action"})
    return ServiceService(device).action(name, action)


def passwall2_node_add(
    device: DeviceClient,
    *,
    from_url: str | None = None,
    fields: dict[str, Any] | None = None,
    apply: bool = False,
) -> CommandResult:
    return PassWall2Service(device).node_add(fields, apply=apply, from_url=from_url)


def passwall2_node_set(
    device: DeviceClient,
    node_id: str,
    *,
    fields: dict[str, Any] | None = None,
    unset: list[str] | None = None,
    apply: bool = False,
) -> CommandResult:
    return PassWall2Service(device).node_set(node_id, fields, apply=apply, unset=unset or ())


def passwall2_node_delete(device: DeviceClient, node_id: str, *, apply: bool = False, force: bool = False) -> CommandResult:
    return PassWall2Service(device).node_delete(node_id, apply=apply, force=force)


def passwall2_acl_add(device: DeviceClient, *, fields: dict[str, Any] | None = None, apply: bool = False) -> CommandResult:
    return PassWall2Service(device).acl_add(fields, apply=apply)


def passwall2_acl_set(
    device: DeviceClient,
    acl_id: str,
    *,
    fields: dict[str, Any] | None = None,
    unset: list[str] | None = None,
    apply: bool = False,
) -> CommandResult:
    return PassWall2Service(device).acl_set(acl_id, fields, apply=apply, unset=unset or ())


def passwall2_acl_delete(device: DeviceClient, acl_id: str, *, apply: bool = False) -> CommandResult:
    return PassWall2Service(device).acl_delete(acl_id, apply=apply)


def passwall2_acl_source_add(device: DeviceClient, acl_id: str, sources: list[str] | str, *, apply: bool = False) -> CommandResult:
    return PassWall2Service(device).acl_source_add(acl_id, sources, apply=apply)


def passwall2_acl_source_remove(device: DeviceClient, acl_id: str, sources: list[str] | str, *, apply: bool = False) -> CommandResult:
    return PassWall2Service(device).acl_source_remove(acl_id, sources, apply=apply)


def backup_create(device: DeviceClient, output: str | None = None) -> CommandResult:
    name = os.path.basename(output or "openwrt-backup.tar.gz") or "openwrt-backup.tar.gz"
    if not name.endswith(".tar.gz"):
        name += ".tar.gz"
    return BackupService(device).create(f"/tmp/{name}")


def user_key_add(device: DeviceClient, key_file: str | None = None) -> CommandResult:
    return UserService(device).key_add(key_file)
