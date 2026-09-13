from __future__ import annotations

from pathlib import Path

import pytest

from openwrt_cli.core.config import ConfigManager, normalize_config
from openwrt_cli.mcp.guard import WRITE_TOOLS
from openwrt_cli.mcp.session import McpSession


def test_write_tools_have_destructive_hint():
    pytest.importorskip("mcp")
    from mcp.types import ToolAnnotations

    from openwrt_cli.mcp.server import registered_tools

    tools = registered_tools()
    assert tools
    writes = {
        "network_set_hostname",
        "wifi_set",
        "lan_set",
        "network_reload",
        "system_hostname",
        "service_action",
        "passwall2_node_add",
        "backup_create",
        "user_key_add",
    }
    for name in writes:
        tool = tools[name]
        ann: ToolAnnotations = tool.annotations
        assert ann.destructiveHint is True
        assert ann.readOnlyHint is False
    assert tools["doctor"].annotations.readOnlyHint is True
    assert "system_reboot" not in tools
    assert "backup_restore" not in tools


def test_session_reloads_mode(tmp_path: Path):
    path = tmp_path / "cfg.yaml"
    ConfigManager(str(path)).save(normalize_config({"host": "192.0.2.1", "mcp": {"mode": "readonly"}}))
    session = McpSession(str(path))
    denied = session.invoke("network.reload", lambda _c: (_ for _ in ()).throw(AssertionError("should not run")))
    assert denied["error"] == "mcp_readonly"
    ConfigManager(str(path)).save(normalize_config({"host": "192.0.2.1", "mcp": {"mode": "readwrite"}}))
    called = {}

    class Fake:
        transport = "ssh"

        def close(self) -> None:
            return None

    def fn(client):
        called["ok"] = True
        from openwrt_cli.services.result import CommandResult
        return CommandResult.ok_data({"ran": True}, transport="ssh")

    session._client = Fake()  # type: ignore[assignment]
    session._key = ("192.0.2.1", 22, "ssh", "root", None, None)
    out = session.invoke("network.reload", fn)
    assert called.get("ok") is True
    assert out["ok"] is True
    session.close()


def test_write_tool_registry_covers_handlers():
    assert "network.set_hostname" in WRITE_TOOLS
    assert "user.add" not in WRITE_TOOLS


def test_main_rejects_invalid_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("mcp")
    path = tmp_path / "cfg.yaml"
    path.write_text("host: 192.0.2.1\nmcp:\n  mode: full\n", encoding="utf-8")
    monkeypatch.setenv("OPENWRT_CONFIG", str(path))
    from openwrt_cli.mcp.server import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
