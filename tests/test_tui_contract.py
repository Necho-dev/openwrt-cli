from __future__ import annotations

from openwrt_cli.tui.app import OpenWrtTUI, _iface_cell, _rule_rows, _route_rows
from openwrt_cli.tui.keyhint import KEY_STYLE, highlight_keys
from openwrt_cli.tui.bandwidth import DualRateChart
from openwrt_cli.ui.render import display_iface
from openwrt_cli.version import GITHUB_ICON, REPO_URL, display_version, package_version


def test_tui_layout_contract():
    css = OpenWrtTUI.CSS
    for token in ("#rules-block", "#bandix-trend", "#bw-panel", "#load-panel", "#net-stack", "#footer-ver", "#footer-gh", "#footer-edit"):
        assert token in css
    assert "#ifaces-block { height: 2fr; }" in css
    assert "#routes-block { height: 3fr;" in css
    assert "#rules-block { height: 2fr;" in css
    actions = " ".join(binding.action for binding in OpenWrtTUI.BINDINGS)
    for pane in ("overview", "network", "leases", "services", "process", "logs", "passwall2"):
        assert f"show_tab('{pane}')" in actions
    assert "#pw2-tabs" in css
    from openwrt_cli.tui.screens.passwall2 import AclFormModal, AclLogModal, NodeFormModal, _ACL_FORM_KEYS, _FORM_KEYS

    assert "#acl-log-view" in AclLogModal.CSS
    assert "#acl-log-head" in AclLogModal.CSS
    assert "#acl-log-filter-input" in AclLogModal.CSS
    assert "filter" not in {binding.action for binding in AclLogModal.BINDINGS}
    assert "refresh" in actions
    assert "refresh_or_rename" not in actions
    from openwrt_cli.tui.app import HostnameModal

    assert "#hn-box" in HostnameModal.CSS
    assert "pw2_node_add" in actions
    assert "edit_or_enable" in actions
    assert "pw2_node_delete" in actions
    assert "#pw2-nf-box" in NodeFormModal.CSS
    assert ".pw2-nf-label" in NodeFormModal.CSS
    assert "#pw2-nf-foot" in NodeFormModal.CSS
    assert "Select" in NodeFormModal.CSS
    assert _FORM_KEYS == (
        "remarks", "group", "type", "protocol", "address", "port", "username", "password",
    )
    from openwrt_cli.tui.screens.passwall2 import _form_value

    assert _form_value({"protocol": "socks", "options": {}}, "protocol") == "socks"
    assert _form_value({"options": {"singbox_protocol": "socks"}}, "protocol") == "socks"
    assert "#pw2-node-split" in OpenWrtTUI.CSS
    assert "#pw2-af-box" in AclFormModal.CSS
    assert "#pw2-af-foot" in AclFormModal.CSS
    assert "height: 90%" in AclFormModal.CSS
    assert "#pw2-af-fields { height: 1fr;" in AclFormModal.CSS
    assert ".pw2-af-label" in AclFormModal.CSS
    assert "Select" in AclFormModal.CSS
    assert _ACL_FORM_KEYS[0] == "enabled"
    assert "sources" in _ACL_FORM_KEYS
    assert "remote_dns_protocol" in _ACL_FORM_KEYS
    assert "dns_redirect" in _ACL_FORM_KEYS
    from openwrt_cli.tui.screens.passwall2 import _acl_raw

    assert _acl_raw({
        "tcp_no_redir_ports": "Use global config (1:65535)",
        "options": {"tcp_no_redir_ports": ""},
    }, "tcp_no_redir_ports") == ""
    assert _acl_raw({
        "options": {"sources": ["192.168.9.151-192.168.9.155"], "dns_mode": "tcp"},
    }, "sources") == "192.168.9.151-192.168.9.155"
    assert _acl_raw({
        "options": {"sources": "192.168.9.151-192.168.9.155"},
    }, "sources") == "192.168.9.151-192.168.9.155"
    assert "\n" not in _acl_raw({"options": {"sources": "192.168.9.151-192.168.9.155"}}, "sources")
    assert _acl_raw({"options": {"dns_mode": "tcp"}}, "remote_dns_protocol") == "tcp"


def test_highlight_keys_marks_shortcuts():
    text = highlight_keys("a Add · e Edit · Del Delete · Ctrl+S save")
    assert text.plain == "a Add · e Edit · Del Delete · Ctrl+S save"
    marked = {text.plain[span.start:span.end]: str(span.style) for span in text.spans}
    for key in ("a", "e", "Del", "Ctrl+S"):
        assert key in marked
        assert "#7ec8ff" in marked[key]
        assert "bold" in marked[key]
    zh = highlight_keys("回车保存   Esc 取消")
    zh_marked = {zh.plain[span.start:span.end]: str(span.style) for span in zh.spans}
    assert "#7ec8ff" in zh_marked["回车"]
    assert "#7ec8ff" in zh_marked["Esc"]


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
