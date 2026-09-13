"""Detect the optional mcp extra without importing the server."""

from __future__ import annotations

import shutil
import sys


def mcp_extra_installed() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


def resolve_mcp_launch(override: str | None = None) -> dict[str, list[str] | str]:
    if override:
        parts = override.split()
        if len(parts) == 1:
            return {"command": parts[0]}
        return {"command": parts[0], "args": parts[1:]}
    if shutil.which("openwrt-mcp"):
        return {"command": "openwrt-mcp"}
    return {"command": sys.executable, "args": ["-m", "openwrt_cli.mcp"]}
