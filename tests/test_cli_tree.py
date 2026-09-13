from __future__ import annotations

import re

from typer.testing import CliRunner

from openwrt_cli.app import app
from openwrt_cli.version import package_version

runner = CliRunner()


def _plain(text: str) -> str:
    """Rich help inserts ANSI between flag fragments at 80 columns."""
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", text or "")

_GROUPS = (
    "network", "firewall", "qos", "service", "passwall2", "user", "backup",
    "system", "config", "doctor", "skill", "mcp",
)
_TOP = ("setup", "tui", "wizard", "logs")
_GONE = ("interactive", "monitor", "conf")


def test_help_lists_current_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in _GROUPS + _TOP:
        assert name in result.stdout
    for name in _GONE:
        assert f"  {name}" not in result.stdout


def test_network_help_has_routes_rules_metrics():
    result = runner.invoke(app, ["network", "--help"])
    assert result.exit_code == 0
    for name in ("interfaces", "routes", "rules", "neighbors", "set-hostname", "metrics", "leases"):
        assert name in result.stdout


def test_system_help_has_status_not_monitor():
    result = runner.invoke(app, ["system", "--help"])
    assert result.exit_code == 0
    assert "status" in result.stdout
    assert "processes" in result.stdout
    gone = runner.invoke(app, ["monitor", "--help"])
    assert gone.exit_code != 0


def test_version_flags():
    for flag in ("--version", "-v"):
        result = runner.invoke(app, [flag])
        assert result.exit_code == 0
        assert "openwrt " in result.stdout
        assert package_version() in result.stdout
    js = runner.invoke(app, ["--json", "--version"])
    assert js.exit_code == 0
    assert '"version"' in js.stdout
    assert package_version() in js.stdout


def test_passwall2_help_is_readonly():
    result = runner.invoke(app, ["passwall2", "--help"])
    assert result.exit_code == 0
    for name in ("status", "nodes", "subscribe", "settings", "rules", "components", "acl", "logs"):
        assert name in result.stdout
    assert "clear" not in result.stdout
    assert "subscribe_manual" not in result.stdout
    node = runner.invoke(app, ["passwall2", "node", "--help"])
    assert node.exit_code == 0
    for name in ("add", "set", "delete"):
        assert name in node.stdout
    add = runner.invoke(app, ["passwall2", "node", "add", "--help"])
    assert add.exit_code == 0
    add_help = _plain(add.stdout)
    for flag in ("--from-url", "--type", "--protocol", "--remarks", "--group", "--address", "--port", "--username", "--password"):
        assert flag in add_help
    patch = runner.invoke(app, ["passwall2", "node", "set", "--help"])
    assert patch.exit_code == 0
    patch_help = _plain(patch.stdout)
    for flag in ("--remarks", "--group", "--type", "--address", "--username", "--unset"):
        assert flag in patch_help
    logs = runner.invoke(app, ["passwall2", "logs", "--help"])
    assert logs.exit_code == 0
    logs_help = _plain(logs.stdout)
    assert "--since" in logs_help
    assert "--until" in logs_help
    assert "--tail" in logs_help
    acl_log = runner.invoke(app, ["passwall2", "acl", "log", "--help"])
    assert acl_log.exit_code == 0
    acl_log_help = _plain(acl_log.stdout)
    assert "--since" in acl_log_help
    assert "--until" in acl_log_help
    acl = runner.invoke(app, ["passwall2", "acl", "--help"])
    assert acl.exit_code == 0
    for name in ("add", "set", "delete", "source", "show", "log"):
        assert name in acl.stdout
    src = runner.invoke(app, ["passwall2", "acl", "source", "--help"])
    assert src.exit_code == 0
    assert "add" in src.stdout
    assert "remove" in src.stdout


def test_config_no_args_is_help():
    result = runner.invoke(app, ["config"])
    assert result.exit_code in {0, 2}
    assert "show" in result.stdout
    assert "set" in result.stdout
