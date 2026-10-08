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


def test_privilege_matrix_matches_gates():
    from openwrt_cli.mcp.guard import PRIVILEGE_MODES, TOOLS, privilege_allowed, privilege_rows

    assert PRIVILEGE_MODES == ("readonly", "readwrite")
    levels = {item.logical: item.level for item in TOOLS}
    assert {name for name, level in levels.items() if level == "write"} == WRITE_TOOLS
    assert FORBIDDEN.isdisjoint(item.logical for item in TOOLS)
    rows = {row["tool"]: row for row in privilege_rows()}
    assert set(rows) == {item.name for item in TOOLS}
    assert rows["doctor"]["readonly"] is True and rows["doctor"]["readwrite"] is True
    assert rows["profiles_list"]["readonly"] is True and rows["profiles_current"]["readonly"] is True
    assert rows["wifi_set"]["readonly"] is False and rows["wifi_set"]["readwrite"] is True
    assert "system.reboot" not in rows
    assert privilege_allowed("write", "readonly") is False
    assert privilege_allowed("read", "readonly") is True


def test_privilege_command_highlights_effective_mode(tmp_path):
    import json

    from typer.testing import CliRunner

    from openwrt_cli.app import app

    cfg = tmp_path / "openwrt-cli.yaml"
    cfg.write_text(
        "language: en\nmcp:\n  mode: readonly\nactive: lab\nprofiles:\n"
        "- name: lab\n  host: 192.0.2.2\n  user: root\n  transport: ssh\n  mcp:\n    mode: readwrite\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    shown = runner.invoke(app, ["--config", str(cfg), "--json", "mcp", "privilege"])
    assert shown.exit_code == 0, shown.stdout
    body = json.loads(shown.stdout)
    assert body["mode"] == "readwrite"
    names = [row["tool"] for row in body["tools"]]
    assert "system.reboot" not in names
    assert "user.add" not in names
    wifi = next(row for row in body["tools"] if row["tool"] == "wifi_set")
    assert wifi["readonly"] is False and wifi["readwrite"] is True
    text = runner.invoke(app, ["--config", str(cfg), "mcp", "privilege"])
    assert text.exit_code == 0, text.stdout
    assert "● readwrite" in text.stdout
    assert "✓" in text.stdout
    assert "✗" in text.stdout
    assert "wifi_set" in text.stdout


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
