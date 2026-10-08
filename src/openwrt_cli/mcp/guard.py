"""MCP permission gates. No FastMCP dependency."""

from __future__ import annotations

from typing import NamedTuple

from openwrt_cli.core.config import MCP_MODE_DEFAULT, McpModeError, mcp_mode
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult

PRIVILEGE_MODES = ("readonly", "readwrite")


class ToolPrivilege(NamedTuple):
    name: str
    logical: str
    level: str
    capability: str


# Callable MCP tool name, guard key, and level (read = both modes, write = readwrite only).
TOOLS: tuple[ToolPrivilege, ...] = (
    ToolPrivilege("doctor", "doctor", "read", "mcp.cap.doctor"),
    ToolPrivilege("system_status", "system.status", "read", "mcp.cap.system_status"),
    ToolPrivilege("system_processes", "system.processes", "read", "mcp.cap.system_processes"),
    ToolPrivilege("logs_read", "logs.read", "read", "mcp.cap.logs_read"),
    ToolPrivilege("network_overview", "network.overview", "read", "mcp.cap.network_overview"),
    ToolPrivilege("network_neighbors", "network.neighbors", "read", "mcp.cap.network_neighbors"),
    ToolPrivilege("network_leases", "network.leases", "read", "mcp.cap.network_leases"),
    ToolPrivilege("network_metrics", "network.metrics", "read", "mcp.cap.network_metrics"),
    ToolPrivilege("firewall_view", "firewall.view", "read", "mcp.cap.firewall_view"),
    ToolPrivilege("qos_view", "qos.view", "read", "mcp.cap.qos_view"),
    ToolPrivilege("service_list", "service.list", "read", "mcp.cap.service_list"),
    ToolPrivilege("service_show", "service.show", "read", "mcp.cap.service_show"),
    ToolPrivilege("passwall2_status", "passwall2.status", "read", "mcp.cap.passwall2_status"),
    ToolPrivilege("passwall2_nodes", "passwall2.nodes", "read", "mcp.cap.passwall2_nodes"),
    ToolPrivilege("passwall2_node_show", "passwall2.node_show", "read", "mcp.cap.passwall2_node_show"),
    ToolPrivilege("passwall2_node_ping", "passwall2.node_ping", "read", "mcp.cap.passwall2_node_ping"),
    ToolPrivilege("passwall2_acl", "passwall2.acl", "read", "mcp.cap.passwall2_acl"),
    ToolPrivilege("passwall2_acl_show", "passwall2.acl_show", "read", "mcp.cap.passwall2_acl_show"),
    ToolPrivilege("passwall2_logs", "passwall2.logs", "read", "mcp.cap.passwall2_logs"),
    ToolPrivilege("profiles_list", "profiles.list", "read", "mcp.cap.profiles_list"),
    ToolPrivilege("profiles_current", "profiles.current", "read", "mcp.cap.profiles_current"),
    ToolPrivilege("config_show", "config.show", "read", "mcp.cap.config_show"),
    ToolPrivilege("network_set_hostname", "network.set_hostname", "write", "mcp.cap.network_set_hostname"),
    ToolPrivilege("wifi_set", "network.wifi_set", "write", "mcp.cap.wifi_set"),
    ToolPrivilege("lan_set", "network.lan_set", "write", "mcp.cap.lan_set"),
    ToolPrivilege("network_reload", "network.reload", "write", "mcp.cap.network_reload"),
    ToolPrivilege("system_hostname", "system.hostname_set", "write", "mcp.cap.system_hostname"),
    ToolPrivilege("service_action", "service.action", "write", "mcp.cap.service_action"),
    ToolPrivilege("passwall2_node_add", "passwall2.node_add", "write", "mcp.cap.passwall2_node_add"),
    ToolPrivilege("passwall2_node_set", "passwall2.node_set", "write", "mcp.cap.passwall2_node_set"),
    ToolPrivilege("passwall2_node_delete", "passwall2.node_delete", "write", "mcp.cap.passwall2_node_delete"),
    ToolPrivilege("passwall2_acl_add", "passwall2.acl_add", "write", "mcp.cap.passwall2_acl_add"),
    ToolPrivilege("passwall2_acl_set", "passwall2.acl_set", "write", "mcp.cap.passwall2_acl_set"),
    ToolPrivilege("passwall2_acl_delete", "passwall2.acl_delete", "write", "mcp.cap.passwall2_acl_delete"),
    ToolPrivilege("passwall2_acl_source_add", "passwall2.acl_source_add", "write", "mcp.cap.passwall2_acl_source_add"),
    ToolPrivilege("passwall2_acl_source_remove", "passwall2.acl_source_remove", "write", "mcp.cap.passwall2_acl_source_remove"),
    ToolPrivilege("backup_create", "backup.create", "write", "mcp.cap.backup_create"),
    ToolPrivilege("user_key_add", "user.key_add", "write", "mcp.cap.user_key_add"),
)

# Not registered as MCP tools. check_call still rejects them if a caller uses the logical name.
FORBIDDEN = frozenset({
    "system.reboot",
    "system.shutdown",
    "backup.restore",
    "user.add",
    "user.passwd",
    "user.delete",
})
WRITE_TOOLS = frozenset(item.logical for item in TOOLS if item.level == "write")


def privilege_allowed(level: str, mode: str) -> bool:
    if level == "read":
        return True
    return level == "write" and mode == "readwrite"


def privilege_rows() -> list[dict]:
    rows = []
    for item in TOOLS:
        row = {
            "tool": item.name,
            "capability": t(item.capability),
            "level": item.level,
        }
        for mode in PRIVILEGE_MODES:
            row[mode] = privilege_allowed(item.level, mode)
        rows.append(row)
    return rows


def check_call(logical: str, cfg: dict) -> CommandResult | None:
    """Return a failure result if the call must not reach services; else None."""
    if logical in FORBIDDEN:
        return CommandResult.fail(
            t("err.mcp_forbidden"),
            data={"error": "mcp_forbidden", "op": logical},
        )
    try:
        mode = mcp_mode(cfg)
    except McpModeError as e:
        return CommandResult.fail(
            t("err.mcp_mode", mode=e.mode),
            data={"error": "mcp_mode_invalid", "mode": e.mode},
        )
    if logical in WRITE_TOOLS and mode != "readwrite":
        return CommandResult.fail(
            t("err.mcp_readonly"),
            data={
                "error": "mcp_readonly",
                "mode": mode or MCP_MODE_DEFAULT,
                "hint": t("hint.mcp_readwrite"),
                "op": logical,
            },
        )
    return None
