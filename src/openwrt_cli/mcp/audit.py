"""Allowlisted metadata shared between the MCP response and worker thread."""

from contextvars import ContextVar
from dataclasses import dataclass

from openwrt_cli.core.config import McpModeError, mcp_mode


@dataclass
class CallMetadata:
    target: tuple[str | None, str] = (None, "unknown")
    dispatched: bool = False


current_call: ContextVar[CallMetadata | None] = ContextVar("openwrt_mcp_call", default=None)


def observe_target(cfg: dict) -> None:
    call = current_call.get()
    if call is not None:
        try:
            mode = mcp_mode(cfg)
        except McpModeError:
            mode = "invalid"
        call.target = (cfg.get("active"), mode)


def observe_dispatch() -> None:
    call = current_call.get()
    if call is not None:
        call.dispatched = True
