from __future__ import annotations

from typer.testing import CliRunner

from openwrt_cli.app import app
from openwrt_cli.version import package_version

runner = CliRunner()

_GROUPS = (
    "network", "firewall", "qos", "service", "user", "backup",
    "system", "config", "doctor",
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
    for name in ("interfaces", "routes", "rules", "neighbors", "metrics", "leases"):
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


def test_config_no_args_is_help():
    result = runner.invoke(app, ["config"])
    assert result.exit_code in {0, 2}
    assert "show" in result.stdout
    assert "set" in result.stdout
