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

    from openwrt_cli.mcp.guard import TOOLS

    tools = registered_tools()
    assert tools
    assert set(tools) == {item.name for item in TOOLS}
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


def test_session_reloads_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "cfg.yaml"
    ConfigManager(str(path)).save(normalize_config({"host": "192.0.2.1", "mcp": {"mode": "readonly"}}))
    session = McpSession(str(path))
    denied = session.invoke("network.reload", lambda _c: (_ for _ in ()).throw(AssertionError("should not run")))
    assert denied["error"] == "mcp_readonly"
    assert denied["profile"] == "default"
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

    monkeypatch.setattr("openwrt_cli.mcp.session.open_connection", lambda cfg: Fake())
    out = session.invoke("network.reload", fn)
    assert called.get("ok") is True
    assert out["ok"] is True
    assert out["profile"] == "default"
    session.close()


def test_write_tool_registry_covers_handlers():
    assert "network.set_hostname" in WRITE_TOOLS
    assert "user.add" not in WRITE_TOOLS


def test_router_tools_take_optional_profile():
    pytest.importorskip("mcp")
    import inspect

    from openwrt_cli.mcp import server
    from openwrt_cli.mcp.guard import TOOLS

    local = {"config_show", "profiles_list", "profiles_current"}
    for item in TOOLS:
        fn = getattr(server, item.name)
        params = inspect.signature(fn).parameters
        if item.name in local:
            assert "profile" not in params
        else:
            assert params["profile"].default is None
            assert "this call only" in (fn.__doc__ or "")
    assert "profiles_list" in {item.name for item in TOOLS}
    assert "profiles_current" in {item.name for item in TOOLS}


def test_profile_argument_is_per_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import json
    import threading
    import time

    from openwrt_cli.mcp import handlers
    from openwrt_cli.services.result import CommandResult

    path = tmp_path / "cfg.yaml"
    ConfigManager(str(path)).save(normalize_config({
        "mcp": {"mode": "readonly"},
        "active": "home",
        "profiles": [
            {
                "name": "home",
                "host": "192.0.2.1",
                "user": "root",
                "password": "home-secret",
                "mcp": {"mode": "readwrite"},
            },
            {
                "name": "lab",
                "host": "192.0.2.2",
                "user": "root",
                "password": "lab-secret",
                "identity_file": "~/.ssh/lab",
                "mcp": {"mode": "readonly"},
            },
        ],
    }))
    opened: list[str] = []
    clients: dict[str, Fake] = {}

    class Fake:
        def __init__(self, host: str):
            self.host = host
            self.transport = "ssh"
            self.closed = False

        def close(self) -> None:
            self.closed = True

    def fake_open(cfg):
        host = str(cfg.get("host"))
        opened.append(host)
        client = Fake(host)
        clients[host] = client
        return client

    monkeypatch.setattr("openwrt_cli.mcp.session.open_connection", fake_open)
    session = McpSession(str(path))

    home = session.invoke("doctor", lambda c: CommandResult.ok_data({"host": c.host}, transport="ssh"))
    assert home["profile"] == "home"
    assert home["host"] == "192.0.2.1"

    home_write = session.invoke(
        "network.reload",
        lambda c: CommandResult.ok_data({"host": c.host}, transport="ssh"),
        profile="home",
    )
    assert home_write["ok"] is True
    assert home_write["profile"] == "home"

    denied = session.invoke(
        "network.reload",
        lambda c: (_ for _ in ()).throw(AssertionError("should not dial")),
        profile="lab",
    )
    assert denied["error"] == "mcp_readonly"
    assert denied["profile"] == "lab"
    assert opened == ["192.0.2.1"]

    lab = session.invoke(
        "doctor",
        lambda c: CommandResult.ok_data({"host": c.host}, transport="ssh"),
        profile="  lab  ",
    )
    assert lab["profile"] == "lab"
    assert lab["host"] == "192.0.2.2"
    assert clients["192.0.2.1"].closed is False

    missing = session.invoke(
        "doctor",
        lambda c: (_ for _ in ()).throw(AssertionError("should not dial")),
        profile="nope",
    )
    assert missing["error"] == "profile_missing"
    assert missing["profile"] == "nope"
    assert session.load_cfg()["active"] == "home"

    again = session.invoke("doctor", lambda c: CommandResult.ok_data({"host": c.host}, transport="ssh"))
    assert again["profile"] == "home"
    assert opened.count("192.0.2.1") == 1

    listed = handlers.profiles_list(cfg=session.load_cfg())
    blob = json.dumps(listed.data)
    assert "password" not in blob
    assert "identity_file" not in blob
    assert "home-secret" not in blob
    assert "lab-secret" not in blob
    assert listed.data["active"] == "home"
    lab_row = next(row for row in listed.data["profiles"] if row["name"] == "lab")
    assert lab_row["current"] is False
    assert lab_row["mcp_effective"] == "readonly"
    assert "192.0.2.2" in lab_row["target"]

    current = handlers.profiles_current(cfg=session.load_cfg())
    assert current.ok is True
    assert current.data["name"] == "home"
    assert current.data["current"] is True
    assert current.data["mcp_effective"] == "readwrite"

    barrier = threading.Barrier(2)

    def slow(label: str):
        def fn(client):
            barrier.wait(timeout=2)
            return CommandResult.ok_data({"host": client.host, "label": label}, transport="ssh")
        return fn

    results: dict[str, dict] = {}

    def run(name: str) -> None:
        results[name] = session.invoke("doctor", slow(name), profile=name)

    threads = [threading.Thread(target=run, args=(name,)) for name in ("home", "lab")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert results["home"]["host"] == "192.0.2.1"
    assert results["lab"]["host"] == "192.0.2.2"

    overlap = {"n": 0, "max": 0}

    def hold(_client):
        overlap["n"] += 1
        overlap["max"] = max(overlap["max"], overlap["n"])
        time.sleep(0.05)
        overlap["n"] -= 1
        return CommandResult.ok_data({}, transport="ssh")

    pair = [
        threading.Thread(target=lambda: session.invoke("doctor", hold, profile="home"))
        for _ in range(2)
    ]
    for thread in pair:
        thread.start()
    for thread in pair:
        thread.join(3)
    assert overlap["max"] == 1

    session.close()
    assert clients["192.0.2.1"].closed is True
    assert clients["192.0.2.2"].closed is True


def test_profiles_current_when_none_saved(tmp_path: Path):
    from openwrt_cli.mcp import handlers

    path = tmp_path / "cfg.yaml"
    path.write_text("language: en\nmcp:\n  mode: readonly\n", encoding="utf-8")
    session = McpSession(str(path))
    current = handlers.profiles_current(cfg=session.load_cfg())
    assert current.ok is False
    assert current.data["error"] == "no_profiles"
    listed = handlers.profiles_list(cfg=session.load_cfg())
    assert listed.data["active"] is None
    assert listed.data["profiles"] == []


def test_main_rejects_invalid_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("mcp")
    path = tmp_path / "cfg.yaml"
    path.write_text("host: 192.0.2.1\nmcp:\n  mode: full\n", encoding="utf-8")
    monkeypatch.setenv("OPENWRT_CONFIG", str(path))
    from openwrt_cli.mcp.server import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
