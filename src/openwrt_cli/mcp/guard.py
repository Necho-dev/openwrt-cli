"""MCP permission gates. No FastMCP dependency."""

from __future__ import annotations

from openwrt_cli.core.config import MCP_MODE_DEFAULT, McpModeError, mcp_mode
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult

FORBIDDEN = frozenset({
    "system.reboot",
    "system.shutdown",
    "backup.restore",
    "user.add",
    "user.passwd",
    "user.delete",
})

WRITE_TOOLS = frozenset({
    "network.set_hostname",
    "network.wifi_set",
    "network.lan_set",
    "network.reload",
    "system.hostname_set",
    "service.action",
    "passwall2.node_add",
    "passwall2.node_set",
    "passwall2.node_delete",
    "passwall2.acl_add",
    "passwall2.acl_set",
    "passwall2.acl_delete",
    "passwall2.acl_source_add",
    "passwall2.acl_source_remove",
    "backup.create",
    "user.key_add",
})


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
