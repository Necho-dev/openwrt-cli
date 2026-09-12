from __future__ import annotations

from openwrt_cli.tui.app import OpenWrtTUI, _iface_cell, _rule_rows, _route_rows
from openwrt_cli.tui.bandwidth import DualRateChart
from openwrt_cli.ui.render import display_iface
from openwrt_cli.version import GITHUB_ICON, REPO_URL, display_version, package_version


def test_tui_layout_contract():
    css = OpenWrtTUI.CSS
    for token in ("#rules-block", "#bandix-trend", "#bw-panel", "#load-panel", "#net-stack", "#footer-ver", "#footer-gh"):
        assert token in css
    assert "#ifaces-block { height: 2fr; }" in css
    assert "#routes-block { height: 3fr;" in css
    assert "#rules-block { height: 2fr;" in css
    actions = " ".join(binding.action for binding in OpenWrtTUI.BINDINGS)
    for pane in ("overview", "network", "leases", "services", "process", "logs"):
        assert f"show_tab('{pane}')" in actions


def test_tui_row_helpers_match_cli_labels():
    routes = _route_rows([{
        "iface": "wan", "dev": "eth0", "dest": "0.0.0.0/0",
        "via": "183.134.100.225", "src": "", "metric": "",
        "table": "main", "proto": "static",
    }])
    assert str(routes[0][0]) == display_iface("eth0", role="wan")
    assert routes[0][1] == "0.0.0.0/0"
    assert routes[0][6] == "static"
    rules = _rule_rows([{
        "rule": "#", "priority": 0, "iif": "", "src": "all", "sport": "",
        "action": "", "ipproto": "", "oif": "", "dest": "any", "dport": "",
        "table": "local",
    }])
    assert rules[0][0] == "#"
    assert rules[0][3] == "all"
    assert rules[0][10] == "local"
    assert "WAN" in str(_iface_cell("eth0", "wan", "eth0"))


def test_display_version_and_repo():
    assert display_version() == f"V{package_version()}"
    assert REPO_URL == "https://github.com/Necho-dev/openwrt-cli"
    assert GITHUB_ICON


def test_dual_rate_chart_widget():
    assert issubclass(DualRateChart, object)
    assert "Sparkline" in DualRateChart.DEFAULT_CSS or "dr-spark" in DualRateChart.DEFAULT_CSS
