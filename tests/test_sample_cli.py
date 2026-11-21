from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from openwrt_cli.app import app
from openwrt_cli.core.config import ConfigManager, connection_for
from openwrt_cli.mcp.handlers import samples_query
from openwrt_cli.services.runtime_logs import query_runtime_logs
from openwrt_cli.services.sample_store import SampleStore
from openwrt_cli.services.sampler import router_key


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("OPENWRT_CONFIG", str(path))
    manager = ConfigManager(str(path))
    manager.save({
        "active": "first",
        "profiles": [{"name": "first", "host": "192.0.2.1"}, {"name": "second", "host": "192.0.2.2"}],
    })
    return manager


def test_sample_cli_configuration_and_preflight(config):
    runner = CliRunner()

    def invoke(*args, code=0):
        result = runner.invoke(app, ["--json", "sample", *args])
        assert result.exit_code == code, result.output
        return json.loads(result.stdout)

    assert invoke("run", code=2)["error"] == "sampling_disabled"
    invoke("config", "set", "--enable")
    assert invoke("run", code=2)["error"] == "sampling_no_sources"
    assert invoke("run", "--profile", "absent", code=2)["error"] == "profile_missing"
    assert not SampleStore().path.exists()
    response = invoke(
        "config", "set", "--enable", "--on-start", "--logs", "--log-interval", "2.5",
        "--no-system", "--kernel", "--passwall2", "--no-passwall2-runtime", "--passwall2-acls",
        "--acl-section", "cfg1", "--metrics", "--metric-interval", "12", "--bandix",
    )
    assert response["saved"] is True
    settings = response["sampling"]
    assert settings == {
        "enabled": True, "on_start": True,
        "logs": {
            "enabled": True, "interval": 2.5, "system": False, "kernel": True,
            "passwall2": {"enabled": True, "runtime": False, "acls": {"enabled": True, "sections": ["cfg1"]}},
        },
        "metrics": {"enabled": True, "interval": 12, "bandix": {"enabled": True}},
    }
    assert invoke("config", "set", "--log-interval", "nan", code=2)["error"] == "sampling_interval"
    assert invoke("config", "set", "--profile", "second", "--disable", code=2)["error"] == "sampling_scope"
    assert invoke("config", "set", code=2)["error"] == "sampling_no_changes"
    invoke("config", "set", "--profile", "second", "--all-acls")
    shown = invoke("config", "show", "--profile", "second")
    assert shown["acl_scope"] == "profile"
    assert shown["shared_acl_sections"] == ["cfg1"]
    assert shown["sampling"]["logs"]["passwall2"]["acls"]["sections"] == []
    invoke("config", "set", "--profile", "second", "--inherit-acls")
    assert invoke("config", "show", "--profile", "second")["sampling"] == settings
    assert config.load()["active"] == "first"
    text = runner.invoke(app, ["-L", "en", "sample", "config", "show"])
    assert text.exit_code == 0 and "Sampling configuration" in text.stdout and "metrics.interval" in text.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX process signal integration")
@pytest.mark.parametrize("signum, as_json", [(signal.SIGINT, True), (signal.SIGTERM, True), (signal.SIGTERM, False)])
def test_sample_cli_streams_and_releases_writer(config, tmp_path, signum, as_json):
    cfg = config.load()
    cfg["sampling"] = {
        "enabled": True, "on_start": False, "logs": {"enabled": True, "interval": 0.08},
        "metrics": {"enabled": True, "interval": 0.08},
    }
    config.save(cfg)
    trial = connection_for(cfg, "second")
    router = router_key(trial)
    store = SampleStore()
    # Resume from an existing checkpoint. Older lines must not be printed again.
    with store.writer() as db:
        store.record_logs(db, router, "system", time.time_ns() // 1_000_000 - 1000, [{"msg": "A"}])
    scripted = """
from types import SimpleNamespace
from openwrt_cli.app import main
from openwrt_cli.commands import sample
from openwrt_cli.core.errors import DeviceCommandError
from openwrt_cli.services import sampler

class FakeSampler(sampler.Sampler):
    def __init__(self, **opts):
        super().__init__(connect=lambda _: SimpleNamespace(close=lambda: None), **opts)

def jobs(*_):
    yield "system", read, ""
    yield "kernel", lambda: [{"msg": "initial backlog"}], ""

def read():
    global polls
    polls += 1
    if polls == 1:
        raise DeviceCommandError("fixture")
    return [{"msg": msg} for msg in ("AB" if polls == 2 else "C")]

polls = 0
sample.Sampler = FakeSampler
sampler._log_jobs = jobs
sampler.Sampler._openwrt_metrics = staticmethod(lambda _, at: [("router", at, {"connections": 7})])
main()
"""
    out = tmp_path / "events"
    args = [sys.executable, "-c", scripted, "-L", "en", "sample", "run", "--profile", "second", "--show-logs"]
    if as_json:
        args.append("--json")
    with out.open("w") as output:
        proc = subprocess.Popen(args, stdout=output, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            marker = '"msg":"C"' if as_json else "system  C"
            while marker not in out.read_text():
                if proc.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail(f"sampler did not produce the expected output: {out.read_text()}")
                time.sleep(0.025)
            busy = CliRunner().invoke(app, ["--json", "sample", "run"])
            assert busy.exit_code == 1
            errors = [json.loads(line) for line in busy.stdout.splitlines()]
            assert any(row.get("error") == "sampling_busy" and not row["ok"] for row in errors)
            proc.send_signal(signum)
            _, stderr = proc.communicate(timeout=10)
            assert proc.returncode == 0, stderr
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)
    text = out.read_text()
    if as_json:
        events = [json.loads(line) for line in text.splitlines()]
        assert events[-1]["event"] == "sampler.stopped"
        assert all(row["profile"] == "second" for row in events)
        assert {row["event"] for row in events} >= {"sampler.read_failed", "sampler.recovered", "sampler.gap"}
        assert [row["entry"]["msg"] for row in events if row["event"] == "sampler.log"] == ["B", "C"]
        assert any(row.get("sample_source") == "openwrt.metrics" and row.get("added") == 1 for row in events)
    else:
        assert "INFO    second  system  B" in text
        assert "WARNING second  system  Collection gap recorded" in text
        assert "Sampler finished (stopped)." in text
        assert "initial backlog" not in text
    assert config.load()["active"] == "first"
    assert store.metrics(router, "openwrt.metrics", "router", 0)[0][1] == {"connections": 7}
    history = samples_query(trial, source="system", since="1h")
    assert history.ok
    assert [row["entry"]["msg"] for row in history.data["records"]] == ["C", "B", "A"]
    assert {gap["type"] for gap in history.data["gaps"]} >= {"gap_rollover", "error"}
    journal, _ = query_runtime_logs(tmp_path / ".openwrt" / "logs", profile="second")
    assert any(row["event"] == "sampler.stopped" for row in journal)
    with store.writer():
        pass
