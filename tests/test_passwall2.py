from __future__ import annotations

from datetime import datetime, timedelta

from rich.text import Text
from typer.testing import CliRunner

from openwrt_cli.app import app
from openwrt_cli.core.http_client import luci_action_allowed
from openwrt_cli.i18n import set_language
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError
from openwrt_cli.services.passwall2 import (
    PassWall2Service,
    acl_enable,
    acl_log_path,
    can_measure,
    current_node,
    detection_mode,
    filter_pw2_log_entries,
    log_messages,
    parse_acl,
    parse_acl_log_text,
    parse_log_text,
    parse_pw2_log_entry,
    parse_geo_rules,
    parse_nodes,
    parse_ping_payload,
    parse_pkg_version,
    parse_rules,
    parse_settings,
    parse_subscribe,
    format_uci_value,
    snake_to_pascal,
    summarize_list,
    uci_label,
)
from openwrt_cli.tui.screens.passwall2 import (
    acl_row,
    format_acl_detail,
    format_acl_log_line,
    format_settings,
    node_row,
    subscribe_row,
)
from openwrt_cli.tui.optional import OptionalApp, PassWall2App, probe_optional_apps

runner = CliRunner()

FIXTURE = {
    "cfgglobal": {".type": "global", "node": "cfghk1"},
    "cfgother": {".type": "global_other", "auto_detection_time": "tcping"},
    "cfgdelay": {".type": "global_delay", "start_daemon": "1"},
    "cfgfwd": {
        ".type": "global_forwarding",
        "tcp_proxy_way": "redirect",
        "tcp_no_redir_ports": "31116,31117",
        "udp_no_redir_ports": "31116,31117",
        "tcp_redir_ports": "1:65535",
        "udp_redir_ports": "1:65535",
    },
    "cfgapp": {".type": "global_app", "xray_file": "/usr/bin/xray"},
    "cfgsubg": {".type": "global_subscribe", "filter_keyword_discarded": "1"},
    "cfgswitch": {".type": "global_acl", "acl_enable": "1"},
    "cfghaproxy": {".type": "global_haproxy", "balancing_enable": "0"},
    "cfgxray": {".type": "global_xray", "sniffing_override_dest": "0"},
    "cfgsingbox": {".type": "global_singbox", "record_fragment": "0", "fragment": "0"},
    "cfghk1": {
        ".type": "nodes",
        "remarks": "HK-100M",
        "group": "HK杭州-100M",
        "type": "Xray",
        "protocol": "vless",
        "address": "1.2.3.4",
        "port": "443",
        "add_from": "ignored",
    },
    "cfgshunt": {
        ".type": "nodes",
        "remarks": "Shunt",
        "type": "Sing-Box",
        "protocol": "_shunt",
    },
    "cfgsub1": {".type": "subscribe_list", "remark": "HK杭州-100M", "url": "https://example.test/sub"},
    "cfgsub2": {".type": "subscribe_list", "remarks": "legacy-name", "url": "https://example.test/old"},
    "cfgrules": {
        ".type": "global_rules",
        "geoip_url": "https://example.test/geoip.dat",
        "geosite_url": "https://example.test/geosite.dat",
        "v2ray_location_asset": "/usr/share/v2ray/",
        "update_week_mode": "",
        "geoip_update": "1",
        "geosite_update": "1",
    },
    "cfgchina": {
        ".type": "shunt_rules",
        "remarks": "China",
        "network": "tcp,udp",
        "domain_list": "geosite:cn",
        "ip_list": "geoip:cn",
    },
    "cfgacl1": {
        ".type": "acl_rule",
        "enabled": "1",
        "remarks": "IoT",
        "sources": ["192.168.1.50"],
        "node": "cfghk1",
        "log": "1",
        "loglevel": "debug",
    },
    "cfgacl2": {
        ".type": "acl_rule",
        "enabled": "1",
        "remarks": "Direct",
        "sources": ["192.168.1.80"],
        "tcp_no_redir_ports": "disable",
        "tcp_redir_ports": "80,443",
    },
}


class FakeUci:
    def __init__(self, values):
        self.values = values
        self._seq = 0

    def show(self, config):
        return self.values if config == "passwall2" else {}

    def set(self, path, value):
        _config, section, option = path.split(".", 2)
        self.set_values("passwall2", section, {option: value})

    def set_values(self, config, section, values):
        sec = self.values.setdefault(section, {".type": "nodes"})
        sec.update(values)

    def add(self, config, typ, values=None, name=None):
        self._seq += 1
        sid = name or f"cfgnew{self._seq}"
        self.values[sid] = {".type": typ, **dict(values or {})}
        return sid

    def delete(self, config, section, option=None, options=None):
        names = [item for item in (options or ([option] if option else [])) if item]
        if names:
            sec = self.values.get(section) or {}
            for key in names:
                sec.pop(key, None)
            return
        self.values.pop(section, None)

    def commit(self, config=None):
        return None


class FakeDevice:
    transport = "http"
    capabilities = frozenset()
    host = "router"
    user = "root"

    def __init__(self, values=None, luci=None, services=None):
        self.uci = FakeUci(values if values is not None else FIXTURE)
        self._luci = luci or {}
        self._services = services if services is not None else {"passwall2": {"running": True}}
        self.calls: list[tuple] = []

    def list_services(self):
        return self._services

    def luci_call(self, action, params=None, timeout=None):
        self.calls.append((action, params))
        assert luci_action_allowed(action), action
        return self._luci.get(action, "")


class FakeFs:
    def __init__(self, files=None):
        self.files = files or {}

    def exists(self, path):
        return path in self.files

    def read(self, path):
        if path in self.files:
            return self.files[path]
        raise CapabilityError(path, missing=("file_read",))


def test_luci_allowlist():
    assert luci_action_allowed("get_log")
    assert luci_action_allowed("get_acl_log")
    assert luci_action_allowed("ping_node")
    assert luci_action_allowed("urltest_node")
    assert luci_action_allowed("version_xray")
    assert luci_action_allowed("check_sing-box")
    assert luci_action_allowed("check_passwall2")
    assert not luci_action_allowed("clear_log")
    assert not luci_action_allowed("subscribe_manual")
    assert not luci_action_allowed("com_update")
    assert not luci_action_allowed("version_evil")
    assert not luci_action_allowed("../clear_log")


def test_parse_nodes_and_skip_shunt():
    nodes = parse_nodes(FIXTURE)
    by_id = {n["id"]: n for n in nodes}
    assert set(by_id) == {"cfghk1", "cfgshunt"}
    assert can_measure(by_id["cfghk1"])
    assert not can_measure(by_id["cfgshunt"])
    assert detection_mode(FIXTURE) == "tcping"
    node_id, remarks = current_node(FIXTURE)
    assert node_id == "cfghk1"
    assert remarks == "HK-100M"


def _cell(value) -> str:
    return value.plain if isinstance(value, Text) else str(value)


def _plain(renderable) -> str:
    from rich.console import Console

    buf = Console(width=100, record=True, force_terminal=False, no_color=True)
    buf.print(renderable)
    return buf.export_text()


def test_parse_subscribe_acl_settings():
    subs = {s["id"]: s for s in parse_subscribe(FIXTURE)}
    assert subs["cfgsub1"]["remarks"] == "HK杭州-100M"
    assert subs["cfgsub1"]["url"] == "https://example.test/sub"
    assert subs["cfgsub1"]["node_count"] == 1
    assert subs["cfgsub2"]["remarks"] == "legacy-name"
    assert subs["cfgsub2"]["node_count"] == 0
    set_language("en")
    acls = {a["id"]: a for a in parse_acl(FIXTURE)}
    assert acls["cfgacl1"]["node_remarks"] == "HK-100M"
    assert acls["cfgacl1"]["loglevel"] == "debug"
    assert acls["cfgacl1"]["log_file"] == acl_log_path("cfgacl1")
    assert acls["cfgacl1"]["log_file"] == "/tmp/log/passwall2_acl_cfgacl1.log"
    assert acls["cfgacl2"]["loglevel"] == ""
    assert acls["cfgacl2"]["log_file"] == "/tmp/log/passwall2_acl_cfgacl2.log"
    no_level = {**FIXTURE, "cfgacl3": {".type": "acl_rule", "enabled": "1", "log": "1", "remarks": "NoLevel"}}
    assert next(a["loglevel"] for a in parse_acl(no_level) if a["id"] == "cfgacl3") == "warning"
    detail = _plain(format_acl_detail(acls["cfgacl1"]))
    assert "Log Level" in detail
    assert "debug" in detail
    assert "/tmp/log/passwall2_acl_cfgacl1.log" in detail
    assert "Press [l]" not in detail
    assert "a Add" in detail
    assert "Direct DNS Query Strategy" not in detail
    assert acls["cfgacl1"]["tcp_no_redir_ports"] == "Use global config (31116,31117)"
    assert acls["cfgacl1"]["tcp_redir_ports"] == "Use global config (1:65535)"
    assert acls["cfgacl2"]["tcp_no_redir_ports"] == "No patterns"
    assert acls["cfgacl2"]["tcp_redir_ports"] == "80,443"
    assert acls["cfgacl2"]["node_remarks"] == "Use global config (HK-100M)"
    assert summarize_list(["192.168.9.151"]) == "192.168.9.151"
    assert summarize_list(["192.168.9.151", "192.168.9.155"]) == "192.168.9.151 +1"
    assert summarize_list(["192.168.9.151", "192.168.9.155", "10.0.0.1"]) == "192.168.9.151 +2"
    assert summarize_list([]) == "—"
    assert format_uci_value("sources", ["192.168.9.151", "192.168.9.155"]) == "192.168.9.151\n192.168.9.155"
    multi = {
        **FIXTURE,
        "cfgacl4": {
            ".type": "acl_rule",
            "enabled": "1",
            "remarks": "RPA",
            "sources": ["192.168.9.151", "192.168.9.155"],
        },
    }
    multi_acl = next(a for a in parse_acl(multi) if a["id"] == "cfgacl4")
    assert _cell(acl_row(multi_acl)[3]) == "192.168.9.151 +1"
    assert _cell(acl_row(acls["cfgacl1"])[3]) == "192.168.1.50"
    multi_detail = _plain(format_acl_detail(multi_acl))
    assert "192.168.9.151 +1" not in multi_detail
    assert "192.168.9.151" in multi_detail
    assert "192.168.9.155" in multi_detail
    assert "192.168.9.151 · 192.168.9.155" not in multi_detail
    assert "cfgswitch" not in acls
    assert acl_enable(FIXTURE) is True
    settings = parse_settings(FIXTURE)
    assert settings["global_other"]["auto_detection_time"] == "tcping"
    assert settings["global_haproxy"]["balancing_enable"] == "0"
    assert settings["global_xray"]["sniffing_override_dest"] == "0"
    assert settings["extra"] == {}
    assert "cfghk1" not in settings["extra"]
    assert "cfgchina" not in settings["extra"]
    rules = parse_rules(FIXTURE)
    assert rules["global_rules"]["v2ray_location_asset"] == "/usr/share/v2ray/"
    assert rules["shunt_rules"][0]["remarks"] == "China"
    assert rules["shunt_rules"][0]["domain_count"] == 1
    assert rules["shunt_rules"][0]["ip_count"] == 1
    geo = dict(parse_geo_rules(rules["global_rules"]))
    assert list(geo)[:4] == ["geoip_url", "geosite_url", "v2ray_location_asset", "update_week_mode"]
    assert geo["update_options"] == "geoip  geosite"
    assert "geoip_update" not in geo
    assert "geosite_update" not in geo


def test_parse_logs_and_ping():
    assert parse_log_text("a\nb\nc\n", tail=2) == ["b", "c"]
    ok, lines, msg = parse_acl_log_text("<script>alert('未启用日志');window.close();</script>")
    assert ok is False
    assert msg
    ok, lines, _ = parse_acl_log_text("one<br />two<br>three")
    assert ok is True
    assert lines == ["one", "two", "three"]
    tailed = PassWall2Service(FakeDevice(luci={"get_acl_log": "one<br />two<br>three"})).acl_log("cfgacl1", tail=2)
    assert log_messages(tailed.data["entries"]) == ["two", "three"]
    assert tailed.data["count"] == 2
    assert tailed.data["since"] is None
    assert tailed.kind == "pw2_acl_log"
    entry = parse_pw2_log_entry("+0000 2026-09-13 01:31:39 DEBUG router: match")
    assert entry["level"] == "DEBUG"
    assert entry["ts"] == "2026-09-13 01:31:39"
    assert isinstance(entry["time"], int)
    stamp = datetime.fromtimestamp(entry["time"] / 1000)
    assert filter_pw2_log_entries([entry], since=stamp - timedelta(seconds=1), until=stamp + timedelta(seconds=1))
    assert filter_pw2_log_entries([entry], until=stamp - timedelta(seconds=1)) == []
    assert filter_pw2_log_entries([parse_pw2_log_entry("no stamp")], since=datetime(2020, 1, 1))[0]["msg"] == "no stamp"
    boom = FakeDevice(luci={"get_redir_log": "fallback<br />line"})

    def _fail_acl(action, params=None, timeout=None):
        boom.calls.append((action, params))
        if action == "get_acl_log":
            raise DeviceCommandError("LuCI get_acl_log failed (HTTP 500)", status=500)
        return boom._luci.get(action, "")

    boom.luci_call = _fail_acl  # type: ignore[method-assign]
    try:
        PassWall2Service(boom).acl_log("cfgacl1", verify=False)
    except DeviceCommandError as exc:
        assert exc.status == 500
    else:
        raise AssertionError("expected HTTP 500")
    assert [c[0] for c in boom.calls] == ["get_acl_log"]
    line = format_acl_log_line("+0000 2026-09-13 01:31:39 DEBUG router: match", "router")
    assert line.plain.startswith("+0000 2026-09-13 01:31:39 DEBUG")
    assert any("8aa4b8" in str(span.style) for span in line.spans)
    assert any("on #7ec8ff" in str(span.style) or "reverse" in str(span.style) for span in line.spans)
    assert parse_ping_payload({"ping": "12.3ms"}) == "12.3ms"
    assert parse_ping_payload({"ping": "4"}) == "4ms"
    assert parse_ping_payload("") == "—"


def test_service_status_and_nodes_measure():
    device = FakeDevice(luci={"ping_node": {"ping": "8ms"}, "get_log": "line1\nline2\n"})
    svc = PassWall2Service(device)
    assert svc.available()
    status = svc.status()
    assert status.ok
    assert status.data["node_remarks"] == "HK-100M"
    assert status.data["running"] is True
    nodes = svc.nodes(measure=True)
    by_id = {n["id"]: n for n in nodes.data["nodes"]}
    assert by_id["cfghk1"]["ping"] == "8ms"
    assert by_id["cfghk1"]["tcping"] == "8ms"
    assert by_id["cfgshunt"]["ping"] == "—"
    assert by_id["cfgshunt"]["tcping"] == "—"
    assert [c[0] for c in device.calls if c[0] == "ping_node"] == ["ping_node", "ping_node"]
    assert [c[1].get("type") for c in device.calls if c[0] == "ping_node"] == ["icmp", "tcping"]
    logs = svc.logs(tail=1)
    assert log_messages(logs.data["entries"]) == ["line2"]
    assert logs.data["count"] == 1
    assert logs.kind == "pw2_logs"
    from openwrt_cli.ui.render import _to_payload

    payload = _to_payload(logs)
    assert payload["ok"] is True
    assert payload["kind"] == "pw2_logs"
    assert payload["count"] == 1


def test_snake_to_pascal_and_settings_labels():
    assert format_uci_value("ip_list", "10.0.0.0/8 127.0.0.0/8") == "10.0.0.0/8\n127.0.0.0/8"
    assert format_uci_value("domain_list", ["geosite:cn", "geosite:private"]) == "geosite:cn\ngeosite:private"
    assert format_uci_value("password", "s3cret") == "******"
    assert format_uci_value("uuid", "u-1") == "******"
    assert format_uci_value("password", "") == "—"
    assert snake_to_pascal("tcp_no_redir_ports") == "TCPNoRedirPorts"
    assert snake_to_pascal("auto_detection_time") == "AutoDetectionTime"
    assert snake_to_pascal("ipv6_tproxy") == "IPv6TProxy"
    set_language("en")
    assert uci_label("tcp_no_redir_ports") == "TCPNoRedirPorts"
    assert uci_label("global_delay", section=True) == "Delay"
    text = _plain(format_settings({
        **parse_settings(FIXTURE),
        "components": [{"name": "passwall2", "title": "PassWall2", "path": "", "version": "26.7.16"}],
    }))
    assert "StartDaemon" in text
    assert "TCPProxyWay" in text
    assert "PassWall2" in text
    assert "26.7.16" in text
    assert "HAProxy" in text
    assert "BalancingEnable" in text
    assert "China" not in text
    assert "cfghaproxy" not in text
    assert "global_delay" not in text
    assert "tcp_proxy_way" not in text
    row = node_row(next(n for n in parse_nodes(FIXTURE) if n["id"] == "cfghk1"))
    assert len(row) == 9
    assert "ignored" not in [_cell(c) for c in row]
    sub = subscribe_row(next(s for s in parse_subscribe(FIXTURE) if s["id"] == "cfgsub1"))
    assert _cell(sub[3]) == "https://example.test/sub"
    set_language("en")


def test_pw2_table_highlights():
    set_language("en")
    node = next(n for n in parse_nodes(FIXTURE) if n["id"] == "cfghk1")
    fast = node_row({**node, "ping": "12ms", "tcping": "380ms"})
    assert "#67c23a" in str(fast[7].style)
    assert "#f0a000" in str(fast[8].style)
    assert "#7ec8ff" in str(fast[3].style)
    slow = node_row({**node, "ping": "520ms", "tcping": "—"})
    assert "#ed5c5c" in str(slow[7].style)
    assert "dim" in str(slow[8].style)
    shunt = node_row(next(n for n in parse_nodes(FIXTURE) if n["id"] == "cfgshunt"))
    assert "dim" in str(shunt[4].style)
    acls = {a["id"]: a for a in parse_acl(FIXTURE)}
    on = acl_row(acls["cfgacl1"])
    off = acl_row({**acls["cfgacl1"], "enabled": False})
    assert "#67c23a" in str(on[1].style)
    assert "dim" in str(off[1].style)
    inherit = acl_row(acls["cfgacl2"])
    assert "dim" in str(inherit[4].style)
    multi = acl_row({**acls["cfgacl1"], "sources": ["192.168.9.151", "192.168.9.155"]})
    assert _cell(multi[3]) == "192.168.9.151 +1"
    assert any("dim" in str(span.style) for span in multi[3].spans)
    sub = subscribe_row(next(s for s in parse_subscribe(FIXTURE) if s["id"] == "cfgsub2"))
    assert "dim" in str(sub[2].style)
    assert "dim" in str(sub[3].style)


def test_parse_pkg_version_and_components():
    assert parse_pkg_version("luci-app-passwall2 - 26.7.16") == "26.7.16"
    assert parse_pkg_version("Package: luci-app-passwall2\nVersion: 26.7.16-1\n") == "26.7.16-1"
    device = FakeDevice(luci={
        "version_geoview": {"data": "0.2.6"},
        "version_sing-box": {"data": "1.13.15"},
        "version_xray": "",
        "version_hysteria": "",
    })
    device.fs = FakeFs({
        "/usr/lib/opkg/info/luci-app-passwall2.control": "Version: 26.7.16\n",
    })
    rows = PassWall2Service(device).components().data["components"]
    names = [r["name"] for r in rows]
    assert names == ["passwall2", "geoview", "sing-box", "xray", "hysteria"]
    assert rows[0]["title"] == "PassWall2"
    assert rows[0]["version"] == "26.7.16"
    assert rows[1]["path"] == "/usr/bin/geoview"
    assert rows[2]["version"] == "1.13.15"
    assert "version_passwall2" not in [c[0] for c in device.calls]
    settings = PassWall2Service(device).settings()
    assert [c["name"] for c in settings.data["components"]] == names
    assert PassWall2Service(device).rules().data["shunt_rules"][0]["remarks"] == "China"


def test_service_missing_package():
    svc = PassWall2Service(FakeDevice(values={}))
    assert not svc.available()
    result = svc.status()
    assert result.ok is False
    assert result.data["error"] == "pw2_missing"


def test_optional_app_hook():
    app = PassWall2App(FakeDevice())
    assert app.id == "passwall2"
    assert app.available()
    assert isinstance(app, OptionalApp)
    missing = PassWall2App(FakeDevice(values={}))
    assert not missing.available()
    assert probe_optional_apps(FakeDevice(values={})) == []
    assert probe_optional_apps(FakeDevice())[0].id == "passwall2"


def test_missing_passwall2_leaves_other_services_alone():
    from openwrt_cli.core.device import Capability
    from openwrt_cli.services.doctor import DoctorService
    from openwrt_cli.services.service import ServiceService
    from openwrt_cli.tui.app import OpenWrtTUI

    class BoomUci:
        def show(self, config):
            raise RuntimeError("uci probe failed")

    boom = FakeDevice(values={})
    boom.uci = BoomUci()
    assert PassWall2Service(boom).available() is False
    assert probe_optional_apps(boom) == []

    class RcDevice:
        transport = "http"
        capabilities = frozenset({Capability.INITD})
        host = "router"
        user = "root"

        def require(self, *caps):
            return None

        class ubus:
            @staticmethod
            def call(obj, method, params=None):
                return {
                    "network": {"running": True, "enabled": True, "start": 20},
                    "firewall": {"running": True, "enabled": True, "start": 19},
                }

    names = [s["name"] for s in ServiceService(RcDevice()).list().data["services"]]
    assert names == ["firewall", "network"]
    assert "passwall2" not in names

    class DoctorDev:
        transport = "http"
        host = "router"
        capabilities = frozenset()

        class uci:
            @staticmethod
            def show(config):
                if config == "passwall2":
                    return {}
                if config == "dhcp":
                    return {"lan": {".type": "dhcp", "interface": "lan"}}
                raise DeviceCommandError(config)

            @staticmethod
            def get(path):
                raise KeyError(path)

        class ubus:
            @staticmethod
            def call(obj, method, params=None):
                if obj == "system":
                    return {
                        "uptime": 60,
                        "load": [1000, 1000, 1000],
                        "memory": {"total": 1024, "available": 512},
                    }
                raise DeviceCommandError(f"{obj}.{method}")

    doctor = DoctorService(DoctorDev()).run(quick=True)
    by_id = {c["id"]: c for c in doctor.data["checks"]}
    assert by_id["passwall2"]["status"] == "skip"
    assert by_id["conn"]["status"] == "ok"
    assert by_id["load"]["status"] == "ok"
    assert doctor.ok is True

    tui = OpenWrtTUI(FakeDevice(values={}))
    assert tui._pw2 is False
    assert tui._optional == []


def test_cli_help_nested():
    result = runner.invoke(app, ["passwall2", "--help"])
    assert result.exit_code == 0
    for name in ("status", "nodes", "node", "subscribe", "settings", "rules", "components", "acl", "logs"):
        assert name in result.stdout
    node = runner.invoke(app, ["passwall2", "node", "--help"])
    assert node.exit_code == 0
    assert "show" in node.stdout
    assert "ping" in node.stdout
    assert "add" in node.stdout
    assert "set" in node.stdout
    assert "delete" in node.stdout
    acl = runner.invoke(app, ["passwall2", "acl", "--help"])
    assert acl.exit_code == 0
    assert "source" in acl.stdout
    assert "add" in acl.stdout


def test_node_schema_and_share_url():
    from openwrt_cli.services.pw2_node_schema import validate_node
    from openwrt_cli.services.pw2_share_url import parse_share_url

    ok, errors, _ = validate_node({"type": "Xray", "protocol": "vless", "remarks": "A", "address": "1.1.1.1"})
    assert not ok
    assert any("port" in e for e in errors)
    ok, errors, _ = validate_node({"type": "Nope", "protocol": "vless", "remarks": "A", "address": "1.1.1.1", "port": "443"})
    assert not ok
    vless = parse_share_url("vless://uuid-1@ex.com:443?type=ws&security=tls&sni=ex.com&path=%2Fvless&flow=xtls-rprx-vision#HK")
    assert vless["type"] == "Xray"
    assert vless["protocol"] == "vless"
    assert vless["address"] == "ex.com"
    assert vless["port"] == "443"
    assert vless["uuid"] == "uuid-1"
    assert vless["tls"] == "1"
    assert vless["tls_serverName"] == "ex.com"
    assert vless["transport"] == "ws"
    assert vless["remarks"] == "HK"
    ss = parse_share_url("ss://aes-256-gcm:s3cret@ex.com:8388#SS")
    assert ss["protocol"] == "shadowsocks"
    assert ss["method"] == "aes-256-gcm"
    assert ss["password"] == "s3cret"
    ok, errors, norm = validate_node(vless)
    assert ok, errors
    assert norm["port"] == "443"
    ok, errors, socks = validate_node({
        "type": "sing-box",
        "protocol": "socks",
        "remarks": "A",
        "address": "1.1.1.1",
        "port": "1080",
        "uot": "1",
        "transport": "tcp",
        "tcp_guise": "none",
        "domain_strategy": "prefer_ipv4",
        "chain_proxy": "1",
    })
    assert ok, errors
    assert socks["uot"] == "1"
    assert socks["tcp_guise"] == "none"


def test_node_write_and_delete_refs():
    from copy import deepcopy

    from openwrt_cli.services.passwall2 import node_references

    device = FakeDevice(values=deepcopy(FIXTURE))
    svc = PassWall2Service(device)
    added = svc.node_add({
        "type": "Xray",
        "protocol": "vless",
        "remarks": "New",
        "address": "2.2.2.2",
        "port": "443",
        "uuid": "u-1",
    })
    assert added.ok
    nid = added.data["id"]
    assert added.data["applied"] is False
    assert "uuid" not in added.data
    assert device.uci.values[nid]["address"] == "2.2.2.2"
    patched = svc.node_set(nid, {"remarks": "Renamed"}, unset=("flow",))
    assert patched.ok
    assert device.uci.values[nid]["remarks"] == "Renamed"
    assert node_references(device.uci.values, "cfghk1") == ["global.node", "acl:cfgacl1"]
    blocked = svc.node_delete("cfghk1")
    assert blocked.ok is False
    assert blocked.data["error"] == "pw2_node_in_use"
    forced = svc.node_delete("cfghk1", force=True)
    assert forced.ok
    assert "cfghk1" not in device.uci.values
    assert "node" not in device.uci.values["cfgglobal"]
    assert "node" not in device.uci.values["cfgacl1"]
    gone = svc.node_delete(nid)
    assert gone.ok

    denied = FakeDevice(values=deepcopy(FIXTURE))

    def _deny(*_a, **_k):
        raise DeviceCommandError("ubus uci.add: Access denied")

    denied.uci.add = _deny  # type: ignore[method-assign]
    fail = PassWall2Service(denied).node_add({
        "type": "Xray", "protocol": "vless", "remarks": "X", "address": "1.1.1.1", "port": "443",
    })
    assert fail.ok is False
    assert fail.data["error"] == "pw2_write_denied"


def test_acl_schema_and_write():
    from copy import deepcopy

    from openwrt_cli.services.pw2_acl_schema import parse_sources, validate_acl

    assert parse_sources("192.168.9.151-192.168.9.155 192.168.9.160") == [
        "192.168.9.151-192.168.9.155",
        "192.168.9.160",
    ]
    assert parse_sources(["192.168.9.151-192.168.9.155", "192.168.9.151-192.168.9.155"]) == [
        "192.168.9.151-192.168.9.155",
    ]
    ok, errors, _ = validate_acl({"enabled": "1"})
    assert not ok
    assert any("remarks" in e for e in errors)
    ok, errors, _ = validate_acl({
        "remarks": "IoT",
        "tcp_no_redir_ports": "Use global config (1:65535)",
    })
    assert not ok
    assert any("tcp_no_redir_ports" in e or "display" in e.lower() or "展示" in e for e in errors)
    ok, errors, norm = validate_acl({
        "remarks": "IoT",
        "sources": "192.168.9.151-192.168.9.155",
        "node": "cfghk1",
        "log": "1",
        "loglevel": "debug",
        "remote_dns_protocol": "tcp",
        "remote_dns": "223.5.5.5",
    })
    assert ok, errors
    assert norm["sources"] == ["192.168.9.151-192.168.9.155"]
    assert "log_file" not in norm

    device = FakeDevice(values=deepcopy(FIXTURE))
    svc = PassWall2Service(device)
    added = svc.acl_add({
        "remarks": "IoT",
        "sources": ["192.168.9.151-192.168.9.155"],
        "node": "cfghk1",
        "tcp_redir_ports": "80,443",
    })
    assert added.ok
    aid = added.data["id"]
    assert added.data["applied"] is False
    sec = device.uci.values[aid]
    assert sec[".type"] == "acl_rule"
    assert sec["sources"] == ["192.168.9.151-192.168.9.155"]
    assert isinstance(sec["sources"], list)
    assert sec["node"] == "cfghk1"
    assert sec["tcp_redir_ports"] == "80,443"
    assert "Use global" not in str(sec)

    patched = svc.acl_set(aid, {"enabled": "0", "tcp_redir_ports": ""})
    assert patched.ok
    assert device.uci.values[aid]["enabled"] == "0"
    assert "tcp_redir_ports" not in device.uci.values[aid]
    assert device.uci.values[aid]["node"] == "cfghk1"

    blocked = svc.acl_set(aid, {"udp_no_redir_ports": "Use global config (1:65535)"})
    assert blocked.ok is False
    assert blocked.data["error"] == "pw2_acl_invalid"
    assert device.uci.values[aid].get("udp_no_redir_ports") != "Use global config (1:65535)"

    readonly = svc.acl_set(aid, {"log_file": "/tmp/nope.log"})
    assert readonly.ok is False

    device.uci.values[aid]["remarks"] = "KeepMe"
    device.uci.values[aid]["node"] = "cfghk1"
    device.uci.values[aid]["tcp_redir_ports"] = "80,443"
    snap = {k: deepcopy(v) for k, v in device.uci.values[aid].items() if k != "sources"}
    src_add = svc.acl_source_add(aid, ["192.168.9.160", "192.168.9.151-192.168.9.155"])
    assert src_add.ok
    assert src_add.data["sources"] == ["192.168.9.151-192.168.9.155", "192.168.9.160"]
    assert src_add.data["added"] == ["192.168.9.160"]
    assert "node" not in src_add.data
    assert "remarks" not in src_add.data
    for key, value in snap.items():
        assert device.uci.values[aid][key] == value

    again = svc.acl_source_add(aid, ["192.168.9.160"])
    assert again.ok
    assert device.uci.values[aid]["sources"].count("192.168.9.160") == 1
    assert again.data["added"] == []

    missing = svc.acl_source_remove(aid, ["10.0.0.1"])
    assert missing.ok is False
    assert missing.data["error"] == "pw2_acl_source_missing"
    assert device.uci.values[aid]["sources"] == ["192.168.9.151-192.168.9.155", "192.168.9.160"]
    assert device.uci.values[aid]["node"] == "cfghk1"
    assert device.uci.values[aid]["tcp_redir_ports"] == "80,443"

    removed = svc.acl_source_remove(aid, ["192.168.9.151-192.168.9.155"])
    assert removed.ok
    assert removed.data["removed"] == ["192.168.9.151-192.168.9.155"]
    assert device.uci.values[aid]["sources"] == ["192.168.9.160"]
    assert device.uci.values[aid]["node"] == "cfghk1"
    assert device.uci.values[aid]["remarks"] == "KeepMe"
    assert device.uci.values[aid]["tcp_redir_ports"] == "80,443"

    emptied = svc.acl_source_remove(aid, ["192.168.9.160"])
    assert emptied.ok
    assert emptied.data["sources"] == []
    assert "sources" not in device.uci.values[aid]
    assert device.uci.values[aid]["node"] == "cfghk1"

    deleted = svc.acl_delete(aid)
    assert deleted.ok
    assert aid not in device.uci.values

    denied = FakeDevice(values=deepcopy(FIXTURE))

    def _deny_acl(*_a, **_k):
        raise DeviceCommandError("ubus uci.add: Access denied")

    denied.uci.add = _deny_acl  # type: ignore[method-assign]
    fail = PassWall2Service(denied).acl_add({"remarks": "X", "sources": ["1.1.1.1"]})
    assert fail.ok is False
    assert fail.data["error"] == "pw2_write_denied"
