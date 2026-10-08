from __future__ import annotations

import pytest

from openwrt_cli.core.config import (
    MASKED_SECRET,
    McpModeError,
    ProfileError,
    activate_profile,
    add_profile,
    canonical_config,
    delete_profile,
    mcp_mode,
    normalize_config,
    persist_connection_overlay,
    public_config,
    public_profiles,
    resolve_http_port,
    update_profile,
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
    assert cfg["active"] == "default"
    assert "mcp" not in cfg["profiles"][0]
    assert resolve_http_port({"port": 22, "http_port": 443, "scheme": "https"}) == 443


def test_canonical_writes_accounts_not_top_level_host():
    raw = {
        "password": "secret",
        "host": "192.0.2.1",
        "transport": "http",
        "scheme": "https",
        "port": 443,
        "user": "root",
        "_config_path": "/tmp/x.yaml",
        "http_port": 443,
        "note": "keep",
    }
    saved = canonical_config(raw)
    assert list(saved)[:3] == ["mcp", "active", "profiles"]
    assert "host" not in saved
    assert "_config_path" not in saved
    assert saved["note"] == "keep"
    account = saved["profiles"][0]
    assert account["name"] == "default"
    assert account["host"] == "192.0.2.1"
    assert account["password"] == "secret"
    assert "http_port" not in account
    assert "mcp" not in account
    pub = public_config(raw, path="/tmp/x.yaml")
    assert "password" not in pub
    assert "host" not in pub
    assert "profiles" not in pub
    assert pub["path"] == "/tmp/x.yaml"
    assert pub["active"] == "default"
    assert pub["mcp"]["effective"] == "readonly"


def test_legacy_users_key_rewrites_as_profiles():
    raw = {
        "language": "en",
        "active": "home",
        "users": [{"name": "home", "host": "192.0.2.1", "user": "root"}],
    }
    cfg = normalize_config(raw)
    assert cfg["active"] == "home"
    assert cfg["profiles"][0]["host"] == "192.0.2.1"
    assert "users" not in cfg
    saved = canonical_config(raw)
    assert saved["profiles"][0]["name"] == "home"
    assert "users" not in saved
    both = normalize_config({
        "profiles": [{"name": "lab", "host": "192.0.2.2"}],
        "users": [{"name": "home", "host": "192.0.2.1"}],
    })
    assert [item["name"] for item in both["profiles"]] == ["lab"]


def test_save_overlay_does_not_invent_a_profile_name():
    cfg = normalize_config({"mcp": {"mode": "readonly"}, "host": "192.0.2.1", "user": "root"})
    # Flattened legacy files already have a migrated name. A file with no profiles must not grow one.
    fresh = normalize_config({"mcp": {"mode": "readonly"}})
    fresh["host"] = "192.0.2.1"
    fresh["user"] = "root"
    with pytest.raises(ProfileError) as exc:
        persist_connection_overlay(fresh)
    assert exc.value.code == "need_name"
    assert fresh["profiles"] == []
    assert cfg["profiles"][0]["name"] == "default"


def test_https_transport_alias_and_empty_file():
    http = normalize_config({"transport": "https", "host": "192.0.2.1"})
    assert http["transport"] == "http"
    assert http["scheme"] == "https"
    assert http["port"] == 443
    empty = normalize_config({})
    assert empty["mcp"]["mode"] == "readonly"
    assert empty["profiles"] == []
    assert "host" not in empty
    assert "active" not in empty


def test_old_yaml_keeps_mcp_global_not_on_the_account():
    cfg = normalize_config({"host": "192.0.2.1", "mcp": {"mode": "readwrite"}})
    assert cfg["mcp"]["mode"] == "readwrite"
    assert "mcp" not in cfg["profiles"][0]
    assert mcp_mode(cfg) == "readwrite"
    saved = canonical_config(cfg)
    assert saved["mcp"] == {"mode": "readwrite"}
    assert "mcp" not in saved["profiles"][0]
    pub = public_config({"host": "192.0.2.1", "password": "secret"})
    assert pub["mcp"]["mode"] == "readonly"
    assert pub["mcp"]["effective"] == "readonly"
    assert "password" not in pub


def test_mcp_mode_readwrite_and_invalid():
    assert mcp_mode(normalize_config({"mcp": {"mode": "readwrite"}})) == "readwrite"
    bad = normalize_config({"mcp": {"mode": "full"}})
    assert bad["mcp"]["mode"] == "full"
    with pytest.raises(McpModeError) as exc:
        mcp_mode(bad)
    assert exc.value.mode == "full"


def test_account_mcp_overrides_global_and_inherit_clears_it():
    cfg = normalize_config({
        "mcp": {"mode": "readonly"},
        "active": "lab",
        "profiles": [
            {"name": "home", "host": "192.0.2.1"},
            {"name": "lab", "host": "192.0.2.2", "mcp": {"mode": "readwrite"}},
        ],
    })
    assert mcp_mode(cfg) == "readwrite"
    assert mcp_mode(cfg, cfg["profiles"][0]) == "readonly"
    assert public_config(cfg)["mcp"]["mode"] == "readonly"
    assert public_config(cfg)["mcp"]["effective"] == "readwrite"
    shown = public_profiles(cfg)
    assert shown["profiles"][0]["mcp_effective"] == "readonly"
    assert "mcp" not in shown["profiles"][0]
    assert shown["profiles"][1]["mcp"]["mode"] == "readwrite"
    assert shown["profiles"][1]["mcp_effective"] == "readwrite"
    update_profile(cfg, "lab", {}, mcp_mode_value="inherit")
    assert "mcp" not in find(cfg, "lab")
    assert mcp_mode(cfg) == "readonly"


def test_illegal_global_rejected_even_when_account_overrides():
    cfg = normalize_config({
        "mcp": {"mode": "full"},
        "active": "lab",
        "profiles": [{"name": "lab", "host": "192.0.2.2", "mcp": {"mode": "readwrite"}}],
    })
    with pytest.raises(McpModeError) as exc:
        mcp_mode(cfg)
    assert exc.value.mode == "full"


def test_delete_active_promotes_next_and_last_clears_host():
    cfg = normalize_config({
        "language": "en",
        "mcp": {"mode": "readwrite"},
        "active": "home",
        "profiles": [
            {"name": "home", "host": "192.0.2.1", "password": "secret"},
            {"name": "lab", "host": "192.0.2.2"},
        ],
    })
    delete_profile(cfg, "home")
    assert cfg["active"] == "lab"
    assert cfg["host"] == "192.0.2.2"
    delete_profile(cfg, "lab")
    assert "active" not in cfg
    assert "host" not in cfg
    saved = canonical_config(cfg)
    assert "profiles" not in saved
    assert "host" not in saved
    assert saved["language"] == "en"
    assert saved["mcp"]["mode"] == "readwrite"
    profiles = public_profiles(cfg)
    assert profiles["profiles"] == []
    assert profiles["active"] is None


def test_add_activate_rename_and_mask():
    cfg = normalize_config({"language": "zh", "mcp": {"mode": "readonly"}})
    add_profile(cfg, "home", {"host": "192.0.2.1", "password": "secret"}, mcp_mode_value="readwrite")
    assert cfg["active"] == "home"
    add_profile(cfg, "lab", {"host": "192.0.2.2", "transport": "ssh"})
    assert cfg["active"] == "home"
    assert cfg["host"] == "192.0.2.1"
    activate_profile(cfg, "lab")
    assert cfg["host"] == "192.0.2.2"
    update_profile(cfg, "home", {"host": "192.0.2.9"}, new_name="house")
    assert find(cfg, "house")["host"] == "192.0.2.9"
    assert find(cfg, "home") is None
    shown = public_profiles(cfg)
    house = next(item for item in shown["profiles"] if item["name"] == "house")
    assert house["password"] == MASKED_SECRET
    with pytest.raises(ProfileError) as exc:
        add_profile(cfg, "lab", {"host": "192.0.2.3"})
    assert exc.value.code == "exists"
    with pytest.raises(ProfileError):
        add_profile(cfg, "bad name", {"host": "192.0.2.3"})


def find(cfg, name):
    from openwrt_cli.core.config import find_profile

    return find_profile(cfg, name)
