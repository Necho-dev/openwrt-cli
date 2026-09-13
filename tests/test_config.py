from __future__ import annotations

import pytest

from openwrt_cli.core.config import (
    MASKED_SECRET,
    McpModeError,
    canonical_config,
    mcp_mode,
    normalize_config,
    public_config,
    resolve_http_port,
)


def test_legacy_http_port_collapses_to_port():
    cfg = normalize_config({
        "host": "192.0.2.1",
        "http_port": 443,
        "port": 22,
        "scheme": "https",
        "transport": "http",
        "user": "root",
        "verify_ssl": False,
        "password": "secret",
    })
    assert cfg["port"] == 443
    assert "http_port" not in cfg
    assert cfg["scheme"] == "https"
    assert resolve_http_port({"port": 22, "http_port": 443, "scheme": "https"}) == 443


def test_canonical_order_and_mask():
    raw = {
        "password": "secret",
        "host": "192.0.2.1",
        "transport": "http",
        "scheme": "https",
        "port": 443,
        "user": "root",
        "_config_path": "/tmp/x.yaml",
        "http_port": 443,
    }
    saved = canonical_config(raw)
    assert list(saved)[:5] == ["host", "user", "transport", "scheme", "port"]
    assert "_config_path" not in saved
    assert "http_port" not in saved
    pub = public_config(raw, path="/tmp/x.yaml")
    assert pub["password"] == MASKED_SECRET
    assert pub["path"] == "/tmp/x.yaml"


def test_https_transport_alias_and_ssh_defaults():
    http = normalize_config({"transport": "https", "host": "192.0.2.1"})
    assert http["transport"] == "http"
    assert http["scheme"] == "https"
    assert http["port"] == 443
    ssh = normalize_config({})
    assert ssh["transport"] == "ssh"
    assert ssh["port"] == 22
    assert ssh["user"] == "root"
    assert "scheme" not in ssh


def test_old_yaml_defaults_mcp_readonly():
    cfg = normalize_config({"host": "192.0.2.1"})
    assert cfg["mcp"]["mode"] == "readonly"
    assert mcp_mode(cfg) == "readonly"
    saved = canonical_config(cfg)
    assert saved["mcp"] == {"mode": "readonly"}
    pub = public_config({"host": "192.0.2.1", "password": "secret"})
    assert pub["mcp"]["mode"] == "readonly"
    assert pub["password"] == MASKED_SECRET


def test_mcp_mode_readwrite_and_invalid():
    assert mcp_mode(normalize_config({"mcp": {"mode": "readwrite"}})) == "readwrite"
    bad = normalize_config({"mcp": {"mode": "full"}})
    assert bad["mcp"]["mode"] == "full"
    with pytest.raises(McpModeError) as exc:
        mcp_mode(bad)
    assert exc.value.mode == "full"
