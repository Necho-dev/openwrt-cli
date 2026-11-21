from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Button, Input, RichLog, TabbedContent

from openwrt_cli.core.config import ConfigManager
from openwrt_cli.services import runtime_logs as logs
from openwrt_cli.services.sampler import Sampler
from openwrt_cli.services.sample_store import SampleStore


def _await_log(root: Path, event: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows, _ = logs.query_runtime_logs(root)
        if any(row["event"] == event for row in rows):
            return
        time.sleep(0.02)
    raise AssertionError(f"missing runtime event: {event}")


def test_runtime_logs_filter_sources_and_protect_current_files(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "SEGMENT_BYTES", 280)
    root = tmp_path / "logs"
    tui = logs.RuntimeLog("tui", root=root)
    mcp = logs.RuntimeLog("mcp", root=root)
    try:
        tui.emit("tui", "tui.started", profile="shop")
        tui.emit("sampler", "sampler.started", profile="shop")
        tui.emit("sampler", "sampler.read_failed", level="WARNING", profile="shop", sample_source="system",
                 error="TimeoutError", password="secret", message="private")
        mcp.emit("mcp", "mcp.call", profile="other", tool="doctor")
        _await_log(root, "sampler.read_failed")
        _await_log(root, "mcp.call")
        rows, more = logs.query_runtime_logs(root, profile="shop", source="sampler", level="WARNING")
        assert len(rows) == 1 and not more
        assert rows[0]["error"] == "TimeoutError"
        assert "password" not in rows[0] and "message" not in rows[0]
        assert logs.query_runtime_logs(root, since_ms=rows[0]["time_ms"] + 60_000)[0] == []
        assert logs.query_runtime_logs(root, limit=1)[1] is True

        active = list(root.glob("*/*.active"))
        assert len(active) == 2
        removed, size = logs.prune_runtime_logs(root, (7, 50), all_closed=True)
        assert removed >= 1 and size > 0
        assert all(path.is_file() for path in active)
    finally:
        tui.close()
        mcp.close()
    archived = list(root.glob("*/*.jsonl"))
    assert len(archived) == 2 and not list(root.glob("*/*.active"))
    if os.name != "nt":
        assert archived[0].stat().st_mode & 0o777 == 0o600

    old, other = archived
    eight_days_ago = time.time() - 8 * 86_400
    old = old.rename(old.with_suffix(".active"))  # A file left by a terminated process.
    os.utime(old, (eight_days_ago, eight_days_ago))
    assert logs.prune_runtime_logs(root, (7, 50))[0] == 1
    assert other.exists()
    assert logs.prune_runtime_logs(root, (7, 50), all_closed=True)[0] == 1


def test_runtime_prunes_to_cap_and_preserves_other_data(tmp_path):
    root = tmp_path / "logs"
    session = root / ("tui-" + "a" * 32)
    session.mkdir(parents=True)
    (session / "writer.lock").touch()
    old = session / "000001.jsonl"
    new = session / "000002.jsonl"
    for path in (old, new):
        with path.open("wb") as handle:
            handle.truncate(800 * 1024)
    earlier = time.time() - 30
    os.utime(old, (earlier, earlier))
    data = tmp_path / "data.db"
    data.write_bytes(b"samples")
    assert logs.prune_runtime_logs(root, (7, 1)) == (1, 800 * 1024)
    assert not old.exists() and new.exists() and data.read_bytes() == b"samples"
    assert logs.retention_settings({"runtime_logs": {"days": 0, "max_mib": 10}}) == (7, 10)


def test_sampler_reports_database_failure_to_independent_log(tmp_path):
    root = tmp_path / "logs"
    journal = logs.RuntimeLog("tui", root=root)
    path = tmp_path / "data.db"
    path.mkdir()
    sampler = Sampler(SampleStore(path), journal=journal)
    sampler.configure({"host": "router", "active": "shop",
                       "sampling": {"enabled": True, "logs": {"enabled": True}}})
    try:
        assert sampler.start()
        _await_log(root, "sampler.failed")
        assert sampler.status.state == "error"
    finally:
        sampler.stop(wait=2)
        journal.close()
    rows, _ = logs.query_runtime_logs(root, profile="shop", source="sampler")
    failure = next(row for row in rows if row["event"] == "sampler.failed")
    assert failure["error"] == "IsADirectoryError"
    assert failure["operation"] == "sqlite_open"
    assert any(row["event"] == "sampler.stopped" for row in rows)


def test_mcp_runtime_logs_use_metadata_and_flag_timeout(tmp_path, monkeypatch, capsys):
    pytest.importorskip("mcp")
    from openwrt_cli.mcp import server
    from openwrt_cli.mcp.audit import observe_target
    from openwrt_cli.mcp.session import McpSession

    manager = ConfigManager(str(tmp_path / "config.yaml"))
    manager.save({"active": "shop", "profiles": [{"name": "shop", "host": "router.example", "password": "secret"}]})
    journal = logs.RuntimeLog("mcp", root=tmp_path / "logs")
    monkeypatch.setattr(server, "_runtime_log", journal)
    monkeypatch.setattr(server, "_session", McpSession(manager.config_path))

    @server._safe(timeout=0.01)
    async def network_reload(*, profile: str, secret: str) -> dict:
        observe_target(manager.load())
        await asyncio.sleep(1)
        return {"ok": True, "secret": secret}

    try:
        assert asyncio.run(server.network_reload(profile="shop"))["error"] == "mcp_readonly"
        assert asyncio.run(server.config_show())["ok"]
        assert asyncio.run(network_reload(profile="shop", secret="private-argument"))["error"] == "timeout"
    finally:
        journal.close()

    rows, _ = logs.query_runtime_logs(journal.root, source="mcp", profile="shop")
    assert {row["outcome"] for row in rows} == {"success", "denied", "timeout"}
    denied = next(row for row in rows if row["outcome"] == "denied")
    assert denied["mode"] == "readonly"
    assert denied["tool"] == "network_reload" and denied["execution"] == "not_dispatched"
    assert next(row for row in rows if row["outcome"] == "timeout")["execution"] == "unknown"
    assert len({row["request_id"] for row in rows}) == 3
    text = "".join(path.read_text() for path in journal.root.glob("*/*.jsonl"))
    assert all(value not in text for value in ("secret", "private-argument", "router.example"))
    assert capsys.readouterr().out == ""


def test_runtime_logs_settings_content_retention_and_manual_clean(tmp_path, monkeypatch):
    from openwrt_cli.tui.app import ConfirmModal, OpenWrtTUI
    from openwrt_cli.tui.screens.runtime_logs import RetentionDialog, RuntimeLogsPane

    manager = ConfigManager(str(tmp_path / "config.yaml"))
    manager.save({"active": "shop", "profiles": [{"name": "shop", "host": "router.example"}],
                  "runtime_logs": {"max_mib": 30}})
    cfg = manager.load()
    cfg["_config_path"] = manager.config_path
    root = tmp_path / "logs"
    previous = logs.RuntimeLog("mcp", root=root)
    previous.emit("mcp", "mcp.call", profile="shop", tool="doctor", level="ERROR")
    previous.emit("mcp", "mcp.call", profile="other", tool="doctor", level="INFO")
    previous.close()
    journal = logs.RuntimeLog("tui", root=root, policy=lambda: logs.retention_settings(manager.load()))
    device = SimpleNamespace(host="router.example", transport="ssh", close=lambda: None)
    for name in ("refresh_overview", "refresh_data", "refresh_logs", "refresh_process"):
        monkeypatch.setattr(OpenWrtTUI, name, lambda _: None)
    sample = tmp_path / "data.db"
    sample.write_bytes(b"unchanged samples")

    async def exercise():
        app = OpenWrtTUI(device, cfg, journal=journal)
        app.sampler.store = SampleStore(sample)
        async with app.run_test(size=(96, 34)) as pilot:
            app.query_one("#tabs", TabbedContent).active = "settings"
            await pilot.pause()
            app.query_one("#settings-tabs", TabbedContent).active = "settings-logs"
            await app.workers.wait_for_complete()
            await pilot.pause()
            pane = app.query_one(RuntimeLogsPane)
            view = pane.query_one(RichLog)
            assert [button.id for button in pane.query(Button)] == ["runtime-retention", "runtime-clean"]
            assert "doctor" in "".join(line.text for line in view.lines)
            await pilot.press("f")
            assert not app.query_one("#filter-bar").display
            journal.emit("tui", "tui.refresh_failed", level="WARNING", error="DeviceConnectionError")
            await asyncio.to_thread(_await_log, root, "tui.refresh_failed")
            pane._refresh_visible()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "tui.refresh_failed" in "".join(line.text for line in view.lines)
            await pilot.press("e")
            assert isinstance(app.screen, RetentionDialog)
            assert [field.id for field in app.screen.query(Input)] == ["runtime-option-days"]
            app.screen.query_one("#runtime-option-days", Input).value = "3"
            await pilot.press("ctrl+s")
            assert manager.load()["runtime_logs"] == {"days": 3, "max_mib": 30}
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("delete")
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("escape")
            assert list(root.glob("mcp-*/*.jsonl"))
            await pilot.press("delete", "y")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not list(root.glob("mcp-*/*.jsonl"))
            assert list(root.glob("tui-*/*.active"))
            assert "doctor" not in "".join(line.text for line in view.lines)
            assert sample.read_bytes() == b"unchanged samples"

    try:
        asyncio.run(exercise())
    finally:
        journal.close()
