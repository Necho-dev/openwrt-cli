"""CommandResult → Agent dict, plus output caps. No FastMCP dependency."""

from __future__ import annotations

from typing import Any

from openwrt_cli.core.config import MASKED_SECRET
from openwrt_cli.services.result import CommandResult

_SECRET_KEYS = frozenset({"password", "identity_file"})


def to_payload(result: CommandResult) -> dict[str, Any]:
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
    return _scrub(payload)


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS and item not in (None, "", MASKED_SECRET):
                out[key] = MASKED_SECRET
            else:
                out[key] = _scrub(item)
        return out
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def cap_payload(payload: dict[str, Any], *, str_limit: int = 20000, list_limit: int = 200) -> dict[str, Any]:
    out = dict(payload)
    for key, value in list(out.items()):
        if isinstance(value, str) and len(value) > str_limit:
            out[key] = value[:str_limit]
            out[f"{key}_truncated"] = True
        elif isinstance(value, list) and len(value) > list_limit:
            out[key] = value[:list_limit]
            out[f"{key}_truncated"] = True
    return out
