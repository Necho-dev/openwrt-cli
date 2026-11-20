from copy import deepcopy

import pytest
from typer.testing import CliRunner

from openwrt_cli.app import app
from openwrt_cli.core.errors import DeviceCommandError
from openwrt_cli.mcp.guard import check_call
from openwrt_cli.services.firewall import FirewallService
from test_static_leases import HttpRouter, Router

PORT = 32485
DEST = "192.168.9.100"
ZONES = {"wan": {".type": "zone", "name": "wan"}, "lan": {".type": "zone", "name": "lan"}}


class FirewallRouter:
    def show(self, config):
        assert config == "firewall"
        return deepcopy(self.config)


class SSHFirewall(FirewallRouter, Router):
    pass


class HTTPFirewall(FirewallRouter, HttpRouter):
    pass


def legacy(**overrides):
    return {
        ".type": "redirect", "name": "ssh", "src": "wan", "src_dport": str(PORT),
        "dest": "lan", "dest_ip": DEST, "dest_port": "22", "proto": "tcp", **overrides,
    }


@pytest.mark.parametrize("router_cls", [HTTPFirewall, SSHFirewall])
def test_create_update_and_unchanged(router_cls):
    router = router_cls(ZONES)
    fw = FirewallService(router)
    result = fw.set_redirect("ssh", PORT, DEST, 22)
    assert result.data["action"] == "created"
    assert result.data["redirects"][0]["family"] == "ipv4"
    assert result.data["redirects"][0]["enabled"] is True
    if router.transport == "http":
        assert router.calls == [
            "hold", ("changes", None), "add",
            ("apply", {"rollback": True, "timeout": 60}), ("confirm", None), "release",
        ]
    else:
        assert router.calls == [
            "uci changes firewall", "add", "uci commit firewall", "/etc/init.d/firewall reload",
        ]
    assert fw.set_redirect("ssh", PORT, DEST, 2222, enabled=False).data["action"] == "updated"
    assert len(fw.redirects().data["redirects"]) == 1
    router.calls.clear()
    assert fw.set_redirect("ssh", PORT, DEST, 2222, enabled=False).data["action"] == "unchanged"
    assert router.calls == (["hold", "release"] if router.transport == "http" else [])


@pytest.mark.parametrize("options, conflicts", [
    ({"src_dport": "32480-32490", "proto": ["tcp", "udp"]}, True),
    ({"src_dport": "32480:32490", "proto": "6"}, True),
    ({"src_dport": "", "proto": "all"}, True),
    ({"src_dport": str(PORT), "proto": "!udp"}, True),
    ({"proto": "udp"}, False),
    ({"enabled": "0"}, False),
    ({"dest_ip": "fd00::100"}, False),
    ({"src": "lan"}, False),
    ({"target": "SNAT"}, False),
])
def test_overlap(options, conflicts):
    router = SSHFirewall({**ZONES, "other": legacy(**{"name": "other", **options})})
    result = FirewallService(router).set_redirect("ssh", PORT, DEST, 22)
    if conflicts:
        assert result.data["error"] == "conflict"
        assert not router.calls
    else:
        assert result.data["action"] == "created"


def test_legacy_defaults_and_preserved_options():
    router = SSHFirewall({**ZONES, "cfg": legacy(reflection="0")})
    fw = FirewallService(router)
    row = fw.redirects().data["redirects"][0]
    assert (row["enabled"], row["family"]) == (True, "ipv4")
    assert fw.set_redirect("ssh", PORT, DEST, 22).data["action"] == "unchanged"
    assert not router.calls
    assert fw.set_redirect("ssh", PORT, DEST, 2222).data["action"] == "updated"
    assert router.config["cfg"]["reflection"] == "0"


def test_http_failed_readback_leaves_rollback_unconfirmed(monkeypatch):
    router = HTTPFirewall(ZONES)
    call = router.call

    def apply(obj, method, params=None):
        if method == "apply":
            router.config["new"]["dest_port"] = "2222"
        return call(obj, method, params)

    monkeypatch.setattr(router, "call", apply)
    with pytest.raises(DeviceCommandError):
        FirewallService(router).set_redirect("ssh", PORT, DEST, 22)
    assert ("confirm", None) not in router.calls


def test_pending_ssh_changes_prevent_write(monkeypatch):
    router = SSHFirewall(ZONES)
    monkeypatch.setattr(router, "exec", lambda *args, **kwargs: "pending")
    assert FirewallService(router).set_redirect("ssh", PORT, DEST, 22).data["error"] == "pending"
    assert router.config == ZONES


def test_cli_and_mcp_surface():
    assert CliRunner().invoke(app, ["firewall", "set-redirect", "--help"]).exit_code == 0
    cfg = {"mcp": {"mode": "readonly"}}
    assert check_call("firewall.view", cfg) is None
    assert check_call("firewall.set_redirect", cfg).data["error"] == "mcp_readonly"
