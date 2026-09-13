"""Local MCP / Skill packaging. Server import stays lazy (needs the mcp extra)."""

from __future__ import annotations

from openwrt_cli.mcp.guard import FORBIDDEN, WRITE_TOOLS, check_call

__all__ = ["FORBIDDEN", "WRITE_TOOLS", "check_call"]
