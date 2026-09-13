from __future__ import annotations

from openwrt_cli.core.config import MASKED_SECRET, normalize_config
from openwrt_cli.mcp.guard import FORBIDDEN, WRITE_TOOLS, check_call
from openwrt_cli.mcp.payload import to_payload
from openwrt_cli.services.result import CommandResult


def test_denylist_blocks_even_in_readwrite():
    cfg = normalize_config({"mcp": {"mode": "readwrite"}})
    for op in FORBIDDEN:
        result = check_call(op, cfg)
        assert result is not None
        assert result.ok is False
        assert result.data["error"] == "mcp_forbidden"


def test_readonly_blocks_writes():
    cfg = normalize_config({})
    for op in ("network.reload", "service.action", "passwall2.node_add"):
        result = check_call(op, cfg)
        assert result is not None
        payload = to_payload(result)
        assert payload["error"] == "mcp_readonly"
        assert "readwrite" in payload["hint"]


def test_readwrite_allows_listed_writes():
    cfg = normalize_config({"mcp": {"mode": "readwrite"}})
    for op in WRITE_TOOLS:
        assert check_call(op, cfg) is None


def test_reads_unblocked():
    cfg = normalize_config({})
    assert check_call("doctor", cfg) is None
    assert check_call("system.status", cfg) is None


def test_invalid_mode_is_mcp_mode_invalid():
    cfg = normalize_config({"mcp": {"mode": "full"}})
    result = check_call("doctor", cfg)
    assert result is not None
    assert to_payload(result)["error"] == "mcp_mode_invalid"


def test_to_payload_masks_password():
    result = CommandResult.ok_data({"password": "secret", "host": "x"}, transport="ssh")
    payload = to_payload(result)
    assert payload["password"] == MASKED_SECRET
    assert payload["ok"] is True
