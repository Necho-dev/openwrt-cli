from __future__ import annotations

import json
import re
from pathlib import Path

from typer.main import get_command
from typer.testing import CliRunner

from openwrt_cli.app import app, hoist_global_options
from openwrt_cli.version import package_version


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", text)

runner = CliRunner()

# Every non-interactive leaf that talks to the device (or local config).
_DEVICE_CMDS = (
    ["doctor"],
    ["doctor", "--quick"],
    ["logs", "system", "--tail", "1"],
    ["system", "status"],
    ["system", "info"],
    ["system", "board"],
    ["system", "cpu"],
    ["system", "memory"],
    ["system", "processes"],
    ["system", "disk"],
    ["system", "temperature"],
    ["system", "uptime"],
    ["system", "hostname"],
    ["network", "interfaces"],
    ["network", "routes"],
    ["network", "rules"],
    ["network", "dns"],
    ["network", "dhcp"],
    ["network", "leases"],
    ["network", "neighbors"],
    ["network", "metrics"],
    ["network", "stats"],
    ["network", "traffic"],
    ["network", "wifi", "list"],
    ["network", "lan", "show"],
    ["firewall", "rules"],
    ["firewall", "nat"],
    ["firewall", "zones"],
    ["firewall", "redirects"],
    ["firewall", "status"],
    ["qos", "status"],
    ["qos", "rules"],
    ["qos", "classes"],
    ["qos", "stats"],
    ["qos", "interrupts"],
    ["service", "list"],
    ["user", "list"],
    ["user", "groups"],
    ["user", "key", "list"],
    ["backup", "list"],
    ["backup", "create"],
)

_INTERACTIVE = ("setup", "tui", "wizard")


def _parse(stdout: str) -> dict:
    return json.loads(stdout)


def _empty_config(tmp_path: Path) -> Path:
    path = tmp_path / "openwrt-cli.yaml"
    path.write_text("language: en\n", encoding="utf-8")
    return path


def test_help_lists_json_flag():
    opts = [name for p in get_command(app).params for name in (*p.opts, *p.secondary_opts)]
    assert "--json" in opts
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    collapsed = re.sub(r"\s+", "", _plain(result.stdout))
    assert "--json" in collapsed or "--[no-]json" in collapsed


def test_version_json_object():
    result = runner.invoke(app, ["--json", "-v"])
    assert result.exit_code == 0
    data = _parse(result.stdout)
    assert data["ok"] is True
    assert data["name"] == "openwrt"
    assert data["version"] == package_version()


def test_config_path_and_show_json(tmp_path: Path):
    cfg = _empty_config(tmp_path)
    path = runner.invoke(app, ["--config", str(cfg), "--json", "config", "path"])
    assert path.exit_code == 0
    data = _parse(path.stdout)
    assert data["ok"] is True
    assert data["path"].endswith("openwrt-cli.yaml")
    shown = runner.invoke(app, ["--config", str(cfg), "--json", "config", "show"])
    assert shown.exit_code == 0
    body = _parse(shown.stdout)
    assert body["ok"] is True
    assert "path" in body
    assert "password" not in json.dumps(body).replace("***", "")


def test_device_commands_json_need_host(tmp_path: Path):
    cfg = _empty_config(tmp_path)
    for cmd in _DEVICE_CMDS:
        argv = hoist_global_options(["--config", str(cfg), *cmd, "--json"])
        result = runner.invoke(app, argv)
        assert result.exit_code == 1, cmd
        data = _parse(result.stdout)
        assert data["ok"] is False, cmd
        assert data.get("error") == "need_host", (cmd, data)


def test_interactive_commands_refuse_json(tmp_path: Path):
    cfg = _empty_config(tmp_path)
    for name in _INTERACTIVE:
        result = runner.invoke(app, ["--config", str(cfg), "--json", name])
        assert result.exit_code == 2, name
        data = _parse(result.stdout)
        assert data["ok"] is False
        assert data.get("error") == "interactive"


def test_destructive_json_is_object(tmp_path: Path):
    cfg = _empty_config(tmp_path)
    result = runner.invoke(app, ["--config", str(cfg), "--json", "system", "reboot"])
    assert result.exit_code == 2
    data = _parse(result.stdout)
    assert data["ok"] is False
    assert data.get("error") == "need_yes"
