from __future__ import annotations

from openwrt_cli.services.network import _proto_label
from openwrt_cli.ui.logo import render_logo_compact
from openwrt_cli.ui.render import display_iface, display_route_iface
from openwrt_cli.ui.wizard import _prompt_label


def test_iface_role_device_format():
    assert display_iface("eth0", role="wan") == "WAN(eth0)"
    assert display_iface("eth1", role="lan") == "LAN(eth1)"
    assert display_iface("lo") == "LOOPBACK(lo)"
    assert display_iface("lo", role="loopback") == "LOOPBACK(lo)"
    assert display_iface("wan", role="wan", device="eth0") == "WAN(eth0)"
    assert display_iface("br-lan") == "br-lan"


def test_route_iface_unmatched_is_parenthesized():
    assert display_route_iface({"iface": "wan", "dev": "eth0"}) == "WAN(eth0)"
    assert display_route_iface({"iface": "", "dev": "lo"}) == "(lo)"
    assert display_route_iface({}) == "—"


def test_compact_logo_is_aligned_art():
    lines = str(render_logo_compact()).splitlines()
    assert 3 <= len(lines) <= 4
    assert all("V0." not in line for line in lines)
    assert lines[0].startswith("  ___")
    assert max(len(line) for line in lines) <= 42


def test_static_proto_is_short():
    assert _proto_label("static") == "Static"
    assert _proto_label("dhcp") == "DHCP client"
    assert _proto_label("") == "—"


def test_wizard_prompt_has_colon():
    assert _prompt_label("Language") == "Language:"
    assert _prompt_label("Host") == "Host:"
    assert _prompt_label("主机") == "主机："
    assert _prompt_label("Save this path anyway?") == "Save this path anyway?"
