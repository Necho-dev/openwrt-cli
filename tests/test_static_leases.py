from contextlib import contextmanager
from copy import deepcopy

import pytest
from typer.testing import CliRunner

from openwrt_cli.app import app
from openwrt_cli.core.errors import DeviceCommandError
from openwrt_cli.core.http_client import HTTPDevice
from openwrt_cli.mcp.guard import check_call
from openwrt_cli.services.network import NetworkService
from openwrt_cli.services.result import CommandResult

MAC = "00:0c:29:a5:46:e4"
IP = "192.168.9.100"
HOST = "market-ops"
OTHER_MAC = "00:0c:29:a5:46:e5"


class Router:
    transport = "ssh"

    def __init__(self, config=None):
        self.config = deepcopy(config or {})
        self.calls = []
        self.pending = False
        self.uci = self.ubus = self.shell = self

    def show(self, config):
        assert config == "dhcp"
        return deepcopy(self.config)

    def add(self, config, kind, values):
        self.calls.append("add")
        self.config["new"] = {".type": kind, **values}
        return "new"

    def set_values(self, config, section, values):
        self.calls.append("set")
        self.config[section].update(values)

    def exec(self, command, *, check):
        assert check
        self.calls.append(command)
        return "pending" if self.pending and command == "uci changes dhcp" else ""

    def call(self, obj, method, params=None):
        assert obj == "uci"
        self.calls.append((method, params))
        return {"changes": ["pending"]} if self.pending and method == "changes" else {}


class HttpRouter(Router, HTTPDevice):
    transport = "http"
    uci = ubus = shell = None

    @contextmanager
    def hold_session(self):
        self.calls.append("hold")
        yield
        self.calls.append("release")


def service(router, monkeypatch, active=()):
    net = NetworkService(router)
    monkeypatch.setattr(net, "leases", lambda: CommandResult.ok_data(
        {"leases": list(active)}, transport=router.transport,
    ))
    return net


def host(mac=MAC, ip=IP, name=HOST):
    return {".type": "host", "mac": mac, "ip": ip, "name": name}


@pytest.mark.parametrize("router_cls", [Router, HttpRouter])
def test_create_and_idempotent_retry(router_cls, monkeypatch):
    router = router_cls()
    net = service(router, monkeypatch, [{"mac": MAC.upper(), "ip": IP}])
    assert net.set_static_lease(MAC, IP, HOST).data["action"] == "created"
    if router.transport == "http":
        assert router.calls == [
            "hold", ("changes", None), "add",
            ("apply", {"rollback": True, "timeout": 60}), ("confirm", None), "release",
        ]
    else:
        assert router.calls == [
            "uci changes dhcp", "add", "uci commit dhcp", "/etc/init.d/dnsmasq reload",
        ]
    router.calls.clear()
    assert net.set_static_lease(MAC.upper(), IP, HOST).data["action"] == "unchanged"
    assert router.calls == (["hold", "release"] if router.transport == "http" else [])


def test_update_legacy_scalar_mac_preserves_other_options(monkeypatch):
    router = Router({"cfg": {**host(mac=MAC.upper(), ip="192.168.9.101"), "dns": "1"}})
    net = service(router, monkeypatch)
    assert net.set_static_lease(MAC, IP, HOST).data["action"] == "updated"
    assert router.config == {"cfg": {**host(mac=MAC.upper()), "dns": "1"}}
    assert net.static_leases().data["leases"] == [
        {"section": "cfg", "macs": [MAC], "ip": IP, "hostname": HOST},
    ]


@pytest.mark.parametrize("config, active", [
    ({"other": host(mac=OTHER_MAC)}, []),
    ({"first": host(), "second": host(ip="192.168.9.101")}, []),
    ({"shared": host(mac=[MAC, OTHER_MAC], name="shared")}, []),
    ({}, [{"ip": IP, "mac": OTHER_MAC}]),
])
def test_conflicts_do_not_write(config, active, monkeypatch):
    router = Router(config)
    result = service(router, monkeypatch, active).set_static_lease(MAC, IP, HOST)
    assert result.data["error"] == "conflict"
    assert router.calls == []
    assert router.config == config


@pytest.mark.parametrize("router_cls", [Router, HttpRouter])
def test_pending_changes_prevent_write(router_cls, monkeypatch):
    router = router_cls()
    router.pending = True
    assert service(router, monkeypatch).set_static_lease(MAC, IP, HOST).data["error"] == "pending"
    assert router.config == {}


def test_http_never_confirms_failed_readback(monkeypatch):
    router = HttpRouter()
    call = router.call

    def bad_apply(obj, method, params=None):
        if method == "apply":
            router.config["new"]["ip"] = "192.168.9.101"
        return call(obj, method, params)

    monkeypatch.setattr(router, "call", bad_apply)
    with pytest.raises(DeviceCommandError):
        service(router, monkeypatch).set_static_lease(MAC, IP, HOST)
    assert ("confirm", None) not in router.calls


@pytest.mark.parametrize("args, code", [
    (("bad", IP, HOST), "invalid_mac"),
    ((MAC, "::1", HOST), "invalid_ip"),
    ((MAC, IP, "bad name"), "invalid_hostname"),
])
def test_invalid_input(args, code, monkeypatch):
    router = Router()
    assert service(router, monkeypatch).set_static_lease(*args).data["error"] == code
    assert router.calls == []


def test_cli_routes_and_mcp_permissions():
    for name in ("static-leases", "set-static-lease"):
        assert CliRunner().invoke(app, ["network", name, "--help"]).exit_code == 0
    cfg = {"mcp": {"mode": "readonly"}}
    assert check_call("network.static_leases", cfg) is None
    assert check_call("network.set_static_lease", cfg).data["error"] == "mcp_readonly"


def test_ssh_commit_failure_reverts_without_reload(monkeypatch):
    router = Router()
    execute = router.exec

    def fail_commit(command, *, check):
        if command == "uci commit dhcp":
            raise DeviceCommandError("commit failed")
        return execute(command, check=check)

    monkeypatch.setattr(router, "exec", fail_commit)
    with pytest.raises(DeviceCommandError, match="commit failed"):
        service(router, monkeypatch).set_static_lease(MAC, IP, HOST)
    assert router.calls == ["uci changes dhcp", "add", "uci revert dhcp"]
