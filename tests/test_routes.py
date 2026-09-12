from __future__ import annotations

from openwrt_cli.services.network import parse_ip_routes, parse_ip_rules


_IP_ROUTE = """
local default dev lo table 999 scope host
default via 183.134.100.225 dev eth0 proto static
183.134.100.224/27 dev eth0 proto kernel scope link src 183.134.100.233
192.168.9.0/24 dev eth1 proto kernel scope link src 192.168.9.1
local 127.0.0.0/8 dev lo table local proto kernel scope host src 127.0.0.1
local 192.168.9.1 dev eth1 table local proto kernel scope host src 192.168.9.1
broadcast 192.168.9.255 dev eth1 table local proto kernel scope link src 192.168.9.1
"""

_NETWORKS = [
    {
        "interface": "wan",
        "l3_device": "eth0",
        "device": "eth0",
        "ipv4-address": [{"address": "183.134.100.233", "mask": 27}],
        "route": [{"target": "0.0.0.0", "mask": 0, "nexthop": "183.134.100.225"}],
    },
    {
        "interface": "lan",
        "l3_device": "eth1",
        "device": "eth1",
        "ipv4-address": [{"address": "192.168.9.1", "mask": 24}],
    },
    {
        "interface": "loopback",
        "l3_device": "lo",
        "device": "lo",
        "ipv4-address": [{"address": "127.0.0.1", "mask": 8}],
    },
]


def test_parse_ip_routes_matches_luci_columns():
    rows = parse_ip_routes(_IP_ROUTE, _NETWORKS)
    assert len(rows) == 7
    first = rows[0]
    assert first["dest"] == "0.0.0.0/0"
    assert first["dev"] == "lo"
    assert first["iface"] == ""
    assert first["table"] == "999"
    default = next(r for r in rows if r["via"] == "183.134.100.225")
    assert default["iface"] == "wan"
    assert default["proto"] == "static"
    assert default["table"] == "main"
    lan = next(r for r in rows if r["dest"] == "192.168.9.0/24")
    assert lan["iface"] == "lan"
    assert lan["src"] == "192.168.9.1"
    assert lan["proto"] == "kernel"


def test_parse_ip_rules_matches_luci_columns():
    raw = (
        "0:\tfrom all lookup local\n"
        "999:\tfrom all fwmark 0x50535732 lookup 999\n"
        "32766:\tfrom all lookup main\n"
        "32767:\tfrom all lookup default\n"
    )
    rows = parse_ip_rules(raw)
    assert [r["priority"] for r in rows] == [0, 999, 32766, 32767]
    assert rows[0]["rule"] == "#"
    assert rows[0]["src"] == "all"
    assert rows[0]["dest"] == "any"
    assert rows[0]["table"] == "local"
    assert rows[1]["rule"] == "# fwmark:0x50535732"
    assert rows[1]["table"] == "999"
    assert rows[2]["table"] == "main"
    assert rows[3]["table"] == "default"
