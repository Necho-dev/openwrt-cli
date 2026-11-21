from __future__ import annotations

import asyncio
import json
import os
import sys
from threading import Event
from types import SimpleNamespace

from textual.widgets import Button, Checkbox, Input, Select, Static, Tab, TabbedContent

from openwrt_cli.core.config import ConfigManager
from openwrt_cli.core.errors import DeviceConnectionError
from openwrt_cli.core.sampling_config import sampling_settings
from openwrt_cli.i18n import current_language, set_language
from openwrt_cli.services import sampler
from openwrt_cli.services.sample_store import SampleStore
from openwrt_cli.tui.app import AccountSwitchModal, ConfirmModal, OpenWrtTUI
from openwrt_cli.tui.optional import PassWall2App
from openwrt_cli.tui.screens.settings import ProfileForm, SettingsPane, SettingsTable


def test_tui_and_headless_sampling_share_writer_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("OPENWRT_CONFIG", str(path))
    manager = ConfigManager(str(path))
    manager.save({
        "active": "lab",
        "profiles": [{"name": "lab", "host": "192.0.2.1"}],
        "sampling": {"enabled": True, "logs": {"enabled": True}},
    })
    cfg = manager.load()
    cfg["_config_path"] = str(path)
    device = SimpleNamespace(host="192.0.2.1", transport="ssh", close=lambda: None)
    monkeypatch.setattr("openwrt_cli.tui.app.probe_optional_apps", lambda _: [])
    for name in ("refresh_overview", "refresh_data", "refresh_logs", "refresh_process", "refresh_sample_capabilities"):
        monkeypatch.setattr(OpenWrtTUI, name, lambda _: None)
    monkeypatch.setattr(sampler, "_log_jobs", lambda *_: [("system", lambda: [{"msg": "marker"}], "")])
    connected = Event()
    log_written = Event()

    class Store(SampleStore):
        def record_logs(self, *args, **kwargs):
            result = super().record_logs(*args, **kwargs)
            log_written.set()
            return result

    store = Store()

    def connect(_):
        connected.set()
        return device

    async def exercise():
        app = OpenWrtTUI(device, cfg)
        app.sampler = sampler.Sampler(store, connect=connect)
        app.sampler.configure(cfg)
        async with app.run_test(size=(120, 42)) as pilot:
            app.query_one("#tabs", TabbedContent).active = "settings"
            await pilot.pause()
            app.query_one("#settings-tabs", TabbedContent).active = "settings-sample"
            await pilot.pause()
            with store.writer() as db:
                store.record_logs(db, sampler.router_key(cfg), "system", sampler.now_ms(), [{"msg": "retained"}])
                log_written.clear()
                assert await pilot.click("#sample-start")
                assert await asyncio.to_thread(app.sampler.wait, 3)
                assert not connected.is_set()
                app._refresh_sampling_status()
                note = str(app.query_one("#sample-status", Static).render())
                assert "Another sampler is writing" in note
                assert app.query_one("#sample-stop", Button).disabled
                assert await pilot.click("#sample-history")
                assert app.screen.query_one("#history-view")
                await pilot.press("escape")
            assert await pilot.click("#sample-start")
            assert await asyncio.to_thread(log_written.wait, 3)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "openwrt_cli.app", "--json", "sample", "run",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            assert proc.returncode == 1, stderr
            assert any(json.loads(line).get("error") == "sampling_busy" for line in stdout.splitlines())
            assert app.sampler.status.state == "running"
            app.sampler.stop()
            assert await asyncio.to_thread(app.sampler.wait, 3)
            assert {row[1]["msg"] for row in store.logs(sampler.router_key(cfg), "system", 0)} == {
                "retained", "marker",
            }

    original = current_language()
    set_language("en")
    try:
        asyncio.run(exercise())
    finally:
        set_language(original)


def test_config_migrates_only_default(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENWRT_CONFIG", raising=False)
    legacy, target = tmp_path / "legacy.yaml", tmp_path / "new" / "config.yaml"
    monkeypatch.setattr(ConfigManager, "LEGACY_CONFIG_PATH", str(legacy))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", str(target))
    contents = "host: old-router\ntransport: ssh\nlanguage: zh\n"
    legacy.write_text(contents)
    assert ConfigManager().load()["host"] == "old-router"
    assert not target.exists()
    assert ConfigManager(str(legacy)).load(migrate=True)["host"] == "old-router"
    assert not target.exists()
    manager = ConfigManager()
    assert manager.load(migrate=True)["host"] == "old-router"
    assert manager.config_path == str(target)
    assert target.read_text() == legacy.read_text() == contents
    manager.save({"host": "new-router"})
    assert ConfigManager().load()["host"] == "new-router"
    assert legacy.read_text() == contents
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
        assert target.parent.stat().st_mode & 0o777 == 0o700

    monkeypatch.setenv("OPENWRT_CONFIG", str(legacy))
    assert ConfigManager().load(migrate=True)["host"] == "old-router"
    assert ConfigManager(str(target)).load()["host"] == "new-router"


def test_config_migration_falls_back_on_write_error(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENWRT_CONFIG", raising=False)
    legacy, target = tmp_path / "legacy.yaml", tmp_path / "config.yaml"
    monkeypatch.setattr(ConfigManager, "LEGACY_CONFIG_PATH", str(legacy))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", str(target))
    legacy.write_text("host: old-router\n")

    def denied(*_args, **_kwargs):
        raise PermissionError("config directory is read-only")

    monkeypatch.setattr(ConfigManager, "_write", staticmethod(denied))
    manager = ConfigManager()
    assert manager.load(migrate=True)["host"] == "old-router"
    assert manager.config_path == str(legacy)
    assert "read-only" in manager.migration_warning
    assert not target.exists()


def test_defaults_and_profile_sections():
    cfg = {"sampling": {"logs": {"passwall2": {"acls": {"sections": ["default"]}}},
                        "profiles": {"shop": {"sections": ["one", "two", "one"]},
                                     "home": {"sections": []}}}}
    assert sampling_settings({})["logs"]["system"] is True
    assert sampling_settings({})["metrics"]["bandix"]["enabled"] is False
    for name, sections in [("shop", ["one", "two"]), ("home", []), ("other", ["default"])]:
        assert sampling_settings(cfg, name)["logs"]["passwall2"]["acls"]["sections"] == sections


def test_checkpoints_resume_per_router_and_preserve_repeated_lines(tmp_path):
    store = SampleStore(tmp_path / "data.db")
    a, b, c = ({"msg": item, "time": stamp} for stamp, item in enumerate("ABC", 1))
    with store.writer() as db:
        assert store.record_logs(db, "r1", "system", 100, [a, b, b]).status == "initial"
        assert store.record_logs(db, "r1", "system", 200, [a, b, b]).status == "continuous"
        assert store.record_logs(db, "r2", "system", 201, [a, b, b]).status == "initial"
    with store.writer() as db:
        assert store.record_logs(db, "r1", "system", 300, [a, b, b, c]).status == "continuous"
        assert store.record_logs(db, "r1", "system", 400, [dict(a, msg="new")]).status == "gap_rollover"
        assert store.record_metrics(db, "r1", "openwrt.metrics", 5_000, [("router", 5_000, {"load": 1})],
                                    interval=2).status == "continuous"
        assert store.record_metrics(db, "r1", "openwrt.metrics", 10_000, [("router", 10_000, {"load": 2})],
                                    interval=2).status == "gap_interval"
    assert [row[1]["msg"] for row in store.logs("r1", "system", 0)] == ["A", "new", "B", "B", "C"]
    assert len(store.logs("r2", "system", 0)) == 3
    assert store.metrics("r1", "openwrt.metrics", "router", 6_000) == [(10_000, {"load": 2})]
    assert store.history("r1", 0) == [("openwrt.metrics", 5000, 10000, 2, 1), ("system", 100, 400, 4, 1)]


def test_sampler_switch_and_stop_discard_in_flight_reads(monkeypatch, tmp_path):
    store = SampleStore(tmp_path / "data.db")
    started = {"old": Event(), "new": Event()}
    release = {"old": Event(), "new": Event()}
    closed = []

    def fake_connect(cfg):
        host = cfg["host"]
        return SimpleNamespace(host=host, close=lambda: closed.append(host))

    def jobs(device, _):
        def read():
            started[device.host].set()
            assert release[device.host].wait(5)
            return [{"msg": device.host}]
        yield "system", read, ""

    monkeypatch.setattr(sampler, "_log_jobs", jobs)
    runner = sampler.Sampler(store, connect=fake_connect)
    cfg = {"host": "old", "sampling": {"enabled": True, "logs": {"enabled": True}}}
    other = {**cfg, "host": "new"}
    try:
        runner.configure(cfg)
        assert runner.start()
        assert started["old"].wait(5)
        runner.configure(other)
        release["old"].set()
        assert started["new"].wait(5)
        runner.stop()
        release["new"].set()
    finally:
        for event in release.values():
            event.set()
        runner.stop(wait=5)
    assert runner.status.state == "stopped"
    assert closed == ["old", "new"]
    for config in (cfg, other):
        assert store.logs(sampler.router_key(config), "system", 0) == []


def test_sampler_writes_periodic_logs_and_metrics(monkeypatch, tmp_path):
    class ObservedStore(SampleStore):
        def __init__(self, path):
            super().__init__(path)
            self.log_written = Event()
            self.metrics_written = Event()

        def record_logs(self, *args, **kwargs):
            status = super().record_logs(*args, **kwargs)
            self.log_written.set()
            return status

        def record_metrics(self, *args, **kwargs):
            status = super().record_metrics(*args, **kwargs)
            self.metrics_written.set()
            return status

    store = ObservedStore(tmp_path / "data.db")
    monkeypatch.setattr(sampler, "_log_jobs", lambda *_: [("system", lambda: [{"msg": "first"}], "")])
    monkeypatch.setattr(sampler.Sampler, "_openwrt_metrics", staticmethod(
        lambda _device, at: [("router", at, {"wan": {"rx_bps": 123}})],
    ))
    runner = sampler.Sampler(store, connect=lambda _: SimpleNamespace(close=lambda: None))
    cfg = {"host": "router", "sampling": {"enabled": True, "logs": {"enabled": True, "interval": 0.1},
                                        "metrics": {"enabled": True, "interval": 0.1}}}
    runner.configure(cfg)
    try:
        assert runner.start()
        assert store.metrics_written.wait(5)
        store.log_written.clear()
        assert store.log_written.wait(5)
    finally:
        runner.stop(wait=5)

    key = sampler.router_key(cfg)
    assert [entry["msg"] for _, entry, _ in store.logs(key, "system", 0)] == ["first"]
    assert store.metrics(key, "openwrt.metrics", "router", 0)[0][1]["wan"]["rx_bps"] == 123
    assert runner.status.state == "stopped"


def test_settings_in_running_tui(monkeypatch, tmp_path):
    original = current_language()
    set_language("en")
    manager = ConfigManager(str(tmp_path / "config.yaml"))
    manager.save({"active": "first", "profiles": [{"name": "first", "host": "first.example"},
                                                 {"name": "next", "host": "next.example"}]})
    cfg = manager.load()
    cfg["_config_path"] = manager.config_path
    device = SimpleNamespace(
        host="first.example", transport="ssh", close=lambda: None,
        ubus=SimpleNamespace(call=lambda *_args, **_kw: {}),
        uci=SimpleNamespace(show=lambda _name: {
            "acl1": {".type": "acl_rule", "enabled": "1", "log": "1"},
            "acl2": {".type": "acl_rule", "enabled": "1", "log": "0"},
        }),
    )
    monkeypatch.setattr("openwrt_cli.tui.app.probe_optional_apps", lambda _: [])
    for name in ("refresh_overview", "refresh_data", "refresh_logs", "refresh_process"):
        monkeypatch.setattr(OpenWrtTUI, name, lambda _: None)

    async def exercise():
        app = OpenWrtTUI(device, cfg)
        app.sampler.store = SampleStore(tmp_path / "data.db")
        async with app.run_test(size=(120, 42)) as pilot:
            tabs = app.query_one("#tabs", TabbedContent)
            tabs.active = "settings"
            await pilot.pause()
            assert list(tabs.children[0].query(Tab))[-1] is tabs.get_tab("settings")
            language = app.query_one("#settings-language-table", SettingsTable)
            assert app.focused is language
            await pilot.press("down")
            assert language.selected == "zh"
            assert current_language() == "en"
            await pilot.press("enter")
            assert isinstance(app.screen, ConfirmModal)
            assert current_language() == "en"
            assert "language" not in manager.load()
            await pilot.press("escape")
            assert current_language() == "en"
            await pilot.press("enter", "y")
            await pilot.pause()
            assert current_language() == manager.load()["language"] == "zh"
            assert str(tabs.get_tab("settings").label) == "设置"
            assert tabs.active == "settings"
            nested = app.query_one("#settings-tabs", TabbedContent)
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert nested.active == "settings-mcp"
            mcp_modes = app.query_one("#settings-mcp-table", SettingsTable)
            assert app.focused is mcp_modes
            assert [str(column.label) for column in mcp_modes.ordered_columns] == ["模式", "状态"]
            assert mcp_modes.get_cell("readonly", "status") == "已保存"
            assert app.query_one("#mcp-save", Button).disabled
            await pilot.press("down")
            assert mcp_modes.selected == "readwrite"
            assert manager.load()["mcp"]["mode"] == "readonly"
            assert app.query_one("#mcp-save", Button).region.height == 1
            assert await pilot.click("#mcp-save")
            assert isinstance(app.screen, ConfirmModal)
            assert manager.load()["mcp"]["mode"] == "readonly"
            await pilot.press("enter")
            assert manager.load()["mcp"]["mode"] == "readonly"
            assert not isinstance(app.screen, ConfirmModal)
            assert await pilot.click("#mcp-save")
            await pilot.press("y")
            await pilot.pause()
            assert manager.load()["mcp"]["mode"] == "readwrite"
            mcp_modes.focus()
            await pilot.pause()
            await pilot.press("up", "enter", "y")
            await pilot.pause()
            assert manager.load()["mcp"]["mode"] == "readonly"
            await pilot.resize_terminal(64, 27)
            await pilot.press("down")
            assert mcp_modes.region.height == 3
            assert await pilot.click("#mcp-save")
            assert isinstance(app.screen, ConfirmModal)
            await pilot.click("#ok")
            await pilot.pause()
            assert manager.load()["mcp"]["mode"] == "readwrite"
            mcp_modes.focus()
            await pilot.pause()
            await pilot.press("up", "enter", "y")
            assert manager.load()["mcp"]["mode"] == "readonly"
            await pilot.resize_terminal(120, 42)
            nested.active = "settings-profiles"
            await pilot.pause()
            assert app.query_one("#profiles-edit", Button).disabled
            await pilot.press("e", "delete")
            assert not isinstance(app.screen, (ProfileForm, ConfirmModal))
            profiles = app.query_one("#settings-profile-table", SettingsTable)
            await pilot.press("down")
            await pilot.pause()
            assert profiles.selected == "next"
            assert not app.query_one("#profiles-edit", Button).disabled
            await pilot.press("e")
            assert isinstance(app.screen, ProfileForm)
            transport = app.screen.query_one("#profile-transport", Select)
            transport.value = "https"
            await pilot.pause()
            assert app.screen.query_one("#profile-tls", Checkbox).display
            assert not app.screen.query_one("#profile-identity-field").display
            assert app.screen.query_one("#profile-port", Input).placeholder == "443"
            app.screen.query_one("#profile-port", Input).value = "99999"
            await pilot.press("ctrl+s")
            assert isinstance(app.screen, ProfileForm)
            assert app.focused is app.screen.query_one("#profile-port", Input)
            await pilot.press("escape")
            await pilot.press("delete")
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("escape")
            assert profiles.selected == "next"
            assert len(manager.load()["profiles"]) == 2
            await pilot.press("a")
            assert isinstance(app.screen, ProfileForm)
            app.screen.query_one("#profile-name", Input).value = "spare"
            app.screen.query_one("#profile-host", Input).value = "spare.example"
            await pilot.press("ctrl+s")
            assert profiles.selected == "spare"
            assert manager.load()["profiles"][-1]["name"] == "spare"
            await pilot.press("e")
            assert isinstance(app.screen, ProfileForm)
            app.screen.query_one("#profile-name", Input).value = "backup"
            await pilot.press("ctrl+s")
            assert profiles.selected == "backup"
            await pilot.press("delete")
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("y")
            assert len(manager.load()["profiles"]) == 2
            assert profiles.selected == "first"
            await pilot.resize_terminal(72, 32)
            await pilot.pause()
            assert app.query_one(SettingsPane).has_class("-narrow")
            assert profiles.region.y < app.query_one("#settings-profile-detail").region.y
            await pilot.resize_terminal(120, 42)
            app._sync_optional_tabs([PassWall2App(device)])
            await pilot.pause()
            assert list(tabs.children[0].query(Tab))[-2:] == [tabs.get_tab("passwall2"), tabs.get_tab("settings")]
            nested.active = "settings-sample"
            await pilot.pause()
            assert app.sample_bandix
            assert [row["sampling_available"] for row in app.sample_acls] == [True, False]
            assert app.query_one("#sample-start", Button).disabled
            assert app.query_one("#sample-form").region.x < app.query_one("#sample-detail").region.x
            assert app.query_one("#sample-save", Button).region.height == 1
            app.query_one("#sample-enabled", Checkbox).value = True
            app.query_one("#sample-logs", Checkbox).value = True
            interval = app.query_one("#sample-log-interval", Input)
            interval.value = "12"
            interval.focus()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert manager.load()["sampling"]["logs"]["enabled"]
            assert manager.load()["sampling"]["logs"]["interval"] == 12
            assert not app.query_one("#sample-start", Button).disabled
            await pilot.resize_terminal(64, 27)
            await pilot.pause()
            assert app.query_one("#sample-form").region.y < app.query_one("#sample-detail").region.y
            interval.value = "13"
            assert await pilot.click("#sample-save")
            await pilot.pause()
            assert manager.load()["sampling"]["logs"]["interval"] == 13
            await pilot.resize_terminal(120, 42)
            app.show_sampling_history()
            await pilot.pause()
            assert app.screen.query_one("#history-view")
            await pilot.press("escape")

            def refused(_config):
                raise DeviceConnectionError("unreachable")

            monkeypatch.setattr("openwrt_cli.core.connection.open_connection", refused)
            await app._switch_account("next").wait()
            assert app.device is device and manager.load()["active"] == "first"
            switched = SimpleNamespace(**{**vars(device), "host": "next.example"})
            started, release = Event(), Event()

            def connect(_config):
                started.set()
                assert release.wait(5)
                return switched

            monkeypatch.setattr("openwrt_cli.core.connection.open_connection", connect)
            nested.active = "settings-profiles"
            await pilot.pause()
            profiles.select("next")
            try:
                await pilot.press("enter")
                assert isinstance(app.screen, ConfirmModal)
                assert not started.is_set()
                await pilot.press("escape")
                assert not started.is_set()
                await pilot.press("u")
                assert isinstance(app.screen, AccountSwitchModal)
                await pilot.press("down", "enter")
                assert isinstance(app.screen, ConfirmModal)
                assert not started.is_set()
                await pilot.press("escape")
                await pilot.press("enter", "y")
                assert await asyncio.to_thread(started.wait, 5)
                assert app.query_one(SettingsPane).connecting_profile == "next"
                assert app.query_one("#profiles-switch", Button).disabled
                assert manager.load()["active"] == "first"
            finally:
                release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.device is switched
            assert app.cfg["active"] == manager.load()["active"] == "next"
            assert app.query_one(SettingsPane).connecting_profile is None
            assert not app._pw2
            assert list(tabs.children[0].query(Tab))[-1] is tabs.get_tab("settings")

    try:
        asyncio.run(exercise())
    finally:
        set_language(original)
