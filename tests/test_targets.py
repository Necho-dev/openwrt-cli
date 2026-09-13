from __future__ import annotations

from openwrt_cli.mcp.guide import json_snippet
from openwrt_cli.mcp.targets import (
    CLIENTS,
    client_by_id,
    client_ids,
    detect_via,
    expand,
    mcp_client_ids,
)


def test_registry_has_new_clients_and_unique_ids():
    ids = client_ids()
    assert ids == tuple(c.id for c in CLIENTS)
    assert len(ids) == len(set(ids))
    for name in ("cursor", "claude-code", "codex", "trae", "windsurf", "qoder", "opencode"):
        assert client_by_id(name) is not None
    assert client_by_id("claude").id == "claude-code"
    assert client_by_id("codeium").id == "windsurf"
    assert client_by_id("open-code").id == "opencode"
    assert mcp_client_ids()[0] == "generic"
    assert "windsurf" in mcp_client_ids()
    cursor = client_by_id("cursor")
    assert cursor is not None
    via = detect_via(cursor)
    assert all(item in {"home", "bin"} for item in via)


def test_env_rewrites_home_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("QODER_CONFIG_DIR", str(tmp_path / "qoder-home"))
    qoder = client_by_id("qoder")
    assert qoder is not None
    assert expand("~/.qoder/skills", qoder) == str(tmp_path / "qoder-home" / "skills")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    opencode = client_by_id("opencode")
    assert opencode is not None
    assert expand("~/.config/opencode/skills", opencode) == str(tmp_path / "xdg" / "opencode" / "skills")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    codex = client_by_id("codex")
    assert expand("~/.codex/skills", codex) == str(tmp_path / "codex" / "skills")


def test_snippets_for_new_formats():
    wind = json_snippet("windsurf", "openwrt-mcp")
    assert wind["format"] == "json"
    assert wind["snippet"]["mcpServers"]["openwrt"]["command"] == "openwrt-mcp"
    oc = json_snippet("opencode", "openwrt-mcp")
    assert oc["format"] == "opencode"
    assert oc["snippet"]["mcp"]["openwrt"]["command"] == ["openwrt-mcp"]
    assert oc["snippet"]["mcp"]["openwrt"]["type"] == "local"
    qoder = json_snippet("qoder", "openwrt-mcp")
    assert qoder["snippet"]["mcpServers"]["openwrt"]["command"] == "openwrt-mcp"
