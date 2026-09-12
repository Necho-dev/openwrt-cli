from __future__ import annotations

from typer.testing import CliRunner

from openwrt_cli.app import app

runner = CliRunner()


def test_json_destructive_requires_yes():
    for args in (
        ["--json", "system", "reboot"],
        ["-f", "json", "system", "reboot"],
        ["-f", "json", "system", "shutdown"],
        ["-f", "json", "system", "hostname", "NewName"],
        ["-f", "json", "network", "reload"],
        ["-f", "json", "service", "restart", "firewall"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, args
        assert "yes" in (result.stderr + result.stdout).lower()
