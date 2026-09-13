from __future__ import annotations

import json
import os

import pytest

from openwrt_cli.core.config import MASKED_SECRET

pytestmark = pytest.mark.live

# Read-only CLI: no --yes mutations, no TUI, no setup/wizard prompts.
_REQUIRED = (
    (["-f", "json", "config", "show"], {"ok", "host", "transport", "port"}),
    (["-f", "json", "system", "status"], {"ok", "hostname"}),
    (["-f", "json", "system", "info"], {"ok"}),
    (["-f", "json", "system", "board"], {"ok"}),
    (["-f", "json", "system", "hostname"], {"ok"}),
    (["-f", "json", "network", "interfaces"], {"ok", "interfaces"}),
    (["-f", "json", "network", "routes"], {"ok", "routes"}),
    (["-f", "json", "network", "rules"], {"ok", "rules"}),
    (["-f", "json", "network", "neighbors"], {"ok"}),
    (["-f", "json", "network", "leases"], {"ok"}),
    (["-f", "json", "network", "lan", "show"], {"ok"}),
    (["-f", "json", "service", "list"], {"ok"}),
    (["-f", "json", "doctor", "--quick"], {"ok"}),
    (["-f", "json", "logs", "system", "--tail", "5"], {"ok"}),
    (["-f", "json", "firewall", "zones"], {"ok"}),
)

_OPTIONAL = (
    (["-f", "json", "network", "metrics"], {"ok"}),
    (["-f", "json", "qos", "status"], {"ok"}),
    (["-f", "json", "network", "wifi", "list"], {"ok"}),
    (["-f", "json", "network", "dns"], {"ok"}),
    (["-f", "json", "passwall2", "status"], {"ok"}),
    (["-f", "json", "passwall2", "nodes"], {"ok"}),
    (["-f", "json", "passwall2", "subscribe"], {"ok"}),
    (["-f", "json", "passwall2", "settings"], {"ok"}),
    (["-f", "json", "passwall2", "rules"], {"ok"}),
    (["-f", "json", "passwall2", "components"], {"ok"}),
    (["-f", "json", "passwall2", "acl"], {"ok"}),
    (["-f", "json", "passwall2", "logs", "--tail", "5"], {"ok"}),
)


def _require_live() -> None:
    if os.environ.get("OPENWRT_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("set OPENWRT_LIVE=1 to run against a real router")


@pytest.mark.parametrize("args,keys", _REQUIRED, ids=[" ".join(a) for a, _ in _REQUIRED])
def test_readonly_json(args: list[str], keys: set[str], run_cli, parse_json, redact) -> None:
    _require_live()
    proc = run_cli(*args, timeout=90)
    assert proc.returncode == 0, redact(proc.stderr or proc.stdout)
    data = parse_json(proc)
    missing = keys - set(data)
    assert not missing, (missing, sorted(data))
    blob = json.dumps(data)
    assert "password" not in blob.lower() or MASKED_SECRET in blob
    if "routes" in data:
        assert data["routes"], "expected kernel routes"
        sample = data["routes"][0]
        for key in ("dest", "table", "dev"):
            assert key in sample, sample
    if "rules" in data:
        assert data["rules"], "expected policy rules"
        sample = data["rules"][0]
        for key in ("priority", "table", "rule"):
            assert key in sample, sample


@pytest.mark.parametrize("args,keys", _OPTIONAL, ids=[" ".join(a) for a, _ in _OPTIONAL])
def test_readonly_optional(args: list[str], keys: set[str], run_cli, parse_json, redact) -> None:
    _require_live()
    proc = run_cli(*args, timeout=180)
    if proc.returncode != 0:
        pytest.skip(redact((proc.stderr or proc.stdout)[:240]))
    data = parse_json(proc)
    assert keys <= set(data)


def test_config_path_and_text_show(run_cli) -> None:
    _require_live()
    path = run_cli("config", "path")
    assert path.returncode == 0
    assert path.stdout.strip().endswith(".yaml")
    shown = run_cli("-L", "zh", "config", "show")
    assert shown.returncode == 0
    text = shown.stdout
    assert "443" in text or "80" in text or "22" in text
    assert MASKED_SECRET in text or "密码" in text


def test_interfaces_use_role_device_labels(run_cli, redact) -> None:
    _require_live()
    proc = run_cli("-L", "zh", "network", "interfaces")
    assert proc.returncode == 0, redact(proc.stderr)
    out = proc.stdout
    assert "WAN(" in out or "LAN(" in out or "LOOPBACK(" in out
    assert "Static address" not in out


def test_passwall2_show_first_items(run_cli, parse_json, redact) -> None:
    _require_live()
    nodes = run_cli("-f", "json", "passwall2", "nodes", timeout=180)
    if nodes.returncode != 0:
        pytest.skip(redact((nodes.stderr or nodes.stdout)[:240]))
    node_data = parse_json(nodes)
    assert node_data.get("ok") is True
    first = (node_data.get("nodes") or [None])[0]
    if first and first.get("id"):
        shown = run_cli("-f", "json", "passwall2", "node", "show", str(first["id"]))
        assert shown.returncode == 0, redact(shown.stderr or shown.stdout)
        body = parse_json(shown)
        assert body.get("ok") is True
        assert (body.get("node") or {}).get("id") == first["id"]
        blob = json.dumps(body)
        assert "stok=" not in blob
        assert "sysauth" not in blob.lower()
    acls = run_cli("-f", "json", "passwall2", "acl")
    assert acls.returncode == 0, redact(acls.stderr or acls.stdout)
    acl_data = parse_json(acls)
    assert acl_data.get("ok") is True
    rule = (acl_data.get("acl") or [None])[0]
    if rule and rule.get("id"):
        shown = run_cli("-f", "json", "passwall2", "acl", "show", str(rule["id"]))
        assert shown.returncode == 0, redact(shown.stderr or shown.stdout)
        body = parse_json(shown)
        assert body.get("ok") is True
        assert (body.get("acl") or {}).get("id") == rule["id"]


def test_routes_and_rules_columns(run_cli, redact) -> None:
    _require_live()
    routes = run_cli("-L", "zh", "network", "routes")
    assert routes.returncode == 0, redact(routes.stderr)
    assert "设备" in routes.stdout
    assert "表" in routes.stdout
    rules = run_cli("-L", "zh", "network", "rules")
    assert rules.returncode == 0, redact(rules.stderr)
    assert "优先级" in rules.stdout or "规则" in rules.stdout
