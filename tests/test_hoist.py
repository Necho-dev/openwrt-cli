from __future__ import annotations

from openwrt_cli.app import hoist_global_options


def test_hoist_moves_host_and_format():
    assert hoist_global_options(["network", "leases", "-f", "json"]) == [
        "-f", "json", "network", "leases",
    ]
    assert hoist_global_options(["network", "leases", "-H", "192.0.2.1"]) == [
        "-H", "192.0.2.1", "network", "leases",
    ]
    assert hoist_global_options(["config", "set", "--language", "zh"]) == [
        "--language", "zh", "config", "set",
    ]


def test_hoist_keeps_logs_follow():
    assert hoist_global_options(["logs", "system", "-f"]) == ["logs", "system", "-f"]
    assert hoist_global_options(["logs", "system", "-f", "json"]) == [
        "-f", "json", "logs", "system",
    ]


def test_hoist_version_flags():
    assert hoist_global_options(["doctor", "-v"]) == ["-v", "doctor"]
    assert hoist_global_options(["--version"]) == ["--version"]
    assert hoist_global_options(["system", "status", "--json"]) == ["--json", "system", "status"]


def test_hoist_keeps_account_flags_on_users_and_config():
    assert hoist_global_options(["profiles", "add", "home", "-H", "192.0.2.1", "--ssh"]) == [
        "profiles", "add", "home", "-H", "192.0.2.1", "--ssh",
    ]
    assert hoist_global_options(["profiles", "add", "home", "--json", "-H", "192.0.2.1"]) == [
        "--json", "profiles", "add", "home", "-H", "192.0.2.1",
    ]
    assert hoist_global_options(["config", "set", "-H", "192.0.2.1"]) == [
        "config", "set", "-H", "192.0.2.1",
    ]
    assert hoist_global_options(["config", "set", "--language", "zh"]) == [
        "--language", "zh", "config", "set",
    ]


def test_hoist_stops_at_double_dash():
    assert hoist_global_options(["system", "hostname", "--", "-H"]) == [
        "system", "hostname", "--", "-H",
    ]
