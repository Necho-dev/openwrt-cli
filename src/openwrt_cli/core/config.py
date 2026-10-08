"""YAML config file (~/.openwrt-cli.yaml)."""

from __future__ import annotations

import os
from typing import Any

import yaml

_GLOBAL_KEYS = ("language", "mcp", "active", "profiles", "users")
_ACCOUNT_KEYS = (
    "name",
    "host",
    "user",
    "transport",
    "scheme",
    "port",
    "identity_file",
    "verify_ssl",
    "password",
    "mcp",
)
_CONN_KEYS = (
    "host",
    "user",
    "transport",
    "scheme",
    "port",
    "identity_file",
    "verify_ssl",
    "password",
    "http_port",
)
_SKIP_KEYS = frozenset({"_config_path", "_connect_explicit", "http_port", "effective"})

MCP_MODES = frozenset({"readonly", "readwrite"})
MCP_MODE_INHERIT = "inherit"
MCP_MODE_DEFAULT = "readonly"
MASKED_SECRET = "********"
_LEGACY_PROFILE = "default"


class McpModeError(ValueError):
    """mcp.mode is missing a legal value (readonly / readwrite)."""

    def __init__(self, mode: str):
        self.mode = mode
        super().__init__(mode)


class ProfileError(ValueError):
    """A profile list change cannot be applied."""

    def __init__(self, code: str, name: str = ""):
        self.code = code
        self.name = name
        super().__init__(code)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_http_port(cfg: dict) -> int:
    """HTTP(S) port. New files use port; old files may keep port=22 plus http_port."""
    scheme = (cfg.get("scheme") or "https").lower()
    legacy = cfg.get("http_port")
    port = cfg.get("port")
    if legacy is not None and (port is None or _as_int(port, 22) == 22):
        return _as_int(legacy, 443 if scheme == "https" else 80)
    if port is not None:
        return _as_int(port, 443 if scheme == "https" else 80)
    if legacy is not None:
        return _as_int(legacy, 443 if scheme == "https" else 80)
    return 443 if scheme == "https" else 80


def validate_profile_name(name: str) -> str:
    text = str(name or "").strip()
    if not text or any(ch.isspace() for ch in text):
        raise ProfileError("invalid_name", text)
    return text


def normalize_config(raw: Any) -> dict:
    """Load a file into a runtime dict: global settings plus the active profile flattened on top."""
    original = raw if isinstance(raw, dict) else {}
    profiles = _normalize_profiles(original)
    active = _resolve_active(original, profiles)
    cfg: dict[str, Any] = {
        "mcp": _normalize_mcp(original.get("mcp")),
        "profiles": profiles,
    }
    if active:
        cfg["active"] = active
    language = str(original.get("language") or "").strip()
    if language:
        cfg["language"] = language
    for key, value in original.items():
        if key in _GLOBAL_KEYS or key in _CONN_KEYS or key in _SKIP_KEYS or str(key).startswith("_"):
            continue
        if value is None or value == "":
            continue
        cfg[key] = value
    _flatten(cfg)
    return cfg


def _normalize_profiles(original: dict) -> list[dict]:
    """Read ``profiles``. A file that still says ``users`` is accepted until the next save."""
    if "profiles" in original or "users" in original:
        raw_profiles = original.get("profiles") if "profiles" in original else original.get("users")
        profiles: list[dict] = []
        if isinstance(raw_profiles, list):
            for item in raw_profiles:
                profile = _account_from_raw(item)
                if profile is not None:
                    profiles.append(profile)
        return profiles
    if not any(key in original for key in _CONN_KEYS):
        return []
    fields = {key: original[key] for key in _CONN_KEYS if key in original}
    profile = _normalize_account(fields)
    profile["name"] = _LEGACY_PROFILE
    return [profile]


def _account_from_raw(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    account = _normalize_account(item)
    account["name"] = name
    mcp = _account_mcp(item.get("mcp"))
    if mcp is not None:
        account["mcp"] = mcp
    return account


def _account_mcp(block: Any) -> dict | None:
    if not isinstance(block, dict):
        return None
    mode = block.get("mode")
    if mode is None or str(mode).strip() == "":
        return None
    return {"mode": str(mode).strip().lower()}


def _resolve_active(original: dict, profiles: list[dict]) -> str | None:
    names = [str(profile.get("name") or "") for profile in profiles]
    if not names:
        return None
    active = str(original.get("active") or "").strip()
    if active in names:
        return active
    return names[0]


def _normalize_account(raw: dict) -> dict:
    """Connection fields for one profile. Does not invent mcp.mode."""
    original = dict(raw)
    cfg: dict[str, Any] = {}
    for key in _CONN_KEYS:
        if key in original:
            cfg[key] = original[key]
    transport = str(cfg.get("transport") or "ssh").lower()
    if transport == "https":
        cfg["transport"] = "http"
        cfg["scheme"] = cfg.get("scheme") or "https"
        transport = "http"
    if transport == "http":
        scheme = str(cfg.get("scheme") or "https").lower()
        if scheme not in ("http", "https"):
            scheme = "https"
        cfg["transport"] = "http"
        cfg["scheme"] = scheme
        if original.get("port") is None and original.get("http_port") is None:
            cfg["port"] = 443 if scheme == "https" else 80
        else:
            cfg["port"] = resolve_http_port(cfg)
        cfg.pop("http_port", None)
        if cfg.get("verify_ssl") is None:
            cfg["verify_ssl"] = False
        else:
            cfg["verify_ssl"] = bool(cfg.get("verify_ssl"))
        cfg.pop("identity_file", None)
    else:
        cfg["transport"] = "ssh"
        cfg["port"] = _as_int(cfg.get("port"), 22)
        cfg.pop("http_port", None)
        cfg.pop("scheme", None)
        cfg.pop("verify_ssl", None)
    if cfg.get("identity_file"):
        cfg["identity_file"] = os.path.expanduser(str(cfg["identity_file"]))
    else:
        cfg.pop("identity_file", None)
    if not cfg.get("user"):
        cfg["user"] = "root"
    if not cfg.get("password"):
        cfg.pop("password", None)
    host = cfg.get("host")
    if host is None or host == "":
        cfg.pop("host", None)
    else:
        cfg["host"] = str(host)
    return cfg


def _normalize_mcp(current: Any) -> dict:
    """Default missing global mcp.mode to readonly; keep illegal values for the caller to reject."""
    src = current if isinstance(current, dict) else {}
    out = {str(k): v for k, v in src.items() if str(k) not in {"effective"}}
    mode = out.get("mode")
    if mode is None or str(mode).strip() == "":
        out["mode"] = MCP_MODE_DEFAULT
        return out
    out["mode"] = str(mode).strip().lower()
    return out


def _global_mode_raw(cfg: dict) -> str:
    block = cfg.get("mcp") if isinstance(cfg.get("mcp"), dict) else {}
    mode = block.get("mode")
    if mode is None or str(mode).strip() == "":
        return MCP_MODE_DEFAULT
    return str(mode).strip().lower()


def _account_mode_raw(account: dict | None) -> str | None:
    if not isinstance(account, dict):
        return None
    return None if (mcp := _account_mcp(account.get("mcp"))) is None else str(mcp["mode"])


def find_profile(cfg: dict, name: str | None) -> dict | None:
    if not name:
        return None
    for user in cfg.get("profiles") or []:
        if isinstance(user, dict) and user.get("name") == name:
            return user
    return None


def mcp_mode(cfg: dict, account: dict | None = None) -> str:
    """Effective MCP permission. Account override, else global, else readonly.

    An illegal value on either layer raises McpModeError, even when the other layer is legal.
    """
    global_mode = _global_mode_raw(cfg)
    if global_mode not in MCP_MODES:
        raise McpModeError(global_mode)
    if account is None and cfg.get("active"):
        account = find_profile(cfg, str(cfg.get("active")))
    override = _account_mode_raw(account)
    if override is None:
        return global_mode
    if override not in MCP_MODES:
        raise McpModeError(override)
    return override


def _flatten(cfg: dict) -> None:
    for key in _CONN_KEYS:
        cfg.pop(key, None)
    account = find_profile(cfg, cfg.get("active"))
    if not account:
        return
    for key in _CONN_KEYS:
        if key not in account or account[key] is None or account[key] == "":
            continue
        cfg[key] = account[key]


def _canonical_account(account: dict) -> dict:
    normalized = _account_from_raw(account) or {}
    out: dict[str, Any] = {}
    transport = normalized.get("transport") or "ssh"
    for key in _ACCOUNT_KEYS:
        if key in ("scheme", "verify_ssl") and transport != "http":
            continue
        if key == "mcp":
            mcp = normalized.get("mcp")
            if isinstance(mcp, dict) and mcp.get("mode"):
                out["mcp"] = {"mode": mcp["mode"]}
            continue
        value = normalized.get(key)
        if value is None or value == "":
            continue
        out[key] = value
    return out


def canonical_config(cfg: dict) -> dict:
    """Stable key order for writing. Connection fields live under profiles, not at the top."""
    normalized = normalize_config(cfg)
    out: dict[str, Any] = {}
    if normalized.get("language"):
        out["language"] = normalized["language"]
    mcp = normalized.get("mcp") if isinstance(normalized.get("mcp"), dict) else {}
    block = {k: v for k, v in mcp.items() if k != "effective" and v not in (None, "")}
    if block:
        out["mcp"] = block
    if normalized.get("active"):
        out["active"] = normalized["active"]
    profiles = [_canonical_account(user) for user in normalized.get("profiles") or [] if isinstance(user, dict)]
    profiles = [user for user in profiles if user.get("name")]
    if profiles:
        out["profiles"] = profiles
    for key, value in normalized.items():
        if key in out or key in _SKIP_KEYS or key in _GLOBAL_KEYS or key in _CONN_KEYS:
            continue
        if value is None or value == "":
            continue
        out[key] = value
    return out


def public_config(cfg: dict, *, path: str | None = None) -> dict:
    """Global config for display / JSON. No connection fields. mcp.effective is not stored."""
    normalized = normalize_config(cfg)
    data: dict[str, Any] = {}
    if path:
        data["path"] = path
    if normalized.get("language"):
        data["language"] = normalized["language"]
    if normalized.get("active"):
        data["active"] = normalized["active"]
    block = dict(normalized.get("mcp") or {})
    block.pop("effective", None)
    try:
        block["effective"] = mcp_mode(normalized)
    except McpModeError as exc:
        block["effective"] = exc.mode
    data["mcp"] = block
    return data


def public_profiles(cfg: dict) -> dict:
    """Profiles for display. Passwords are masked. mcp_effective is computed."""
    normalized = normalize_config(cfg)
    profiles = []
    for account in normalized.get("profiles") or []:
        if not isinstance(account, dict):
            continue
        item = _canonical_account(account)
        if item.get("password"):
            item["password"] = MASKED_SECRET
        if not (isinstance(account.get("mcp"), dict) and account["mcp"].get("mode")):
            item.pop("mcp", None)
        try:
            item["mcp_effective"] = mcp_mode(normalized, account)
        except McpModeError as exc:
            item["mcp_effective"] = exc.mode
        profiles.append(item)
    return {"active": normalized.get("active"), "profiles": profiles}


def public_profile(cfg: dict, name: str | None = None) -> dict:
    """One profile for display. The password is masked. ``current`` is the active flag."""
    shown = public_profiles(cfg)
    active = shown.get("active")
    target = (name or "").strip() or active
    if not target:
        raise ProfileError("missing", name or "")
    for item in shown["profiles"]:
        if item.get("name") == target:
            return {"current": target == active, "profile": item}
    raise ProfileError("missing", str(target))


def profile_target(account: dict) -> str:
    """Short label: ``SSH  root@host:22``."""
    transport = str(account.get("transport") or "ssh").lower()
    scheme = str(account.get("scheme") or "").lower()
    if scheme in {"http", "https"}:
        proto = scheme.upper()
    elif transport == "ssh":
        proto = "SSH"
    elif transport == "http":
        proto = "HTTP"
    else:
        proto = (transport or "?").upper()
    user = account.get("user") or "root"
    host = account.get("host") or "?"
    port = account.get("port")
    text = f"{proto}  {user}@{host}"
    if port is not None:
        text += f":{port}"
    return text


def mcp_profile_card(cfg: dict, account: dict, active: str | None) -> dict:
    """One profile for MCP. Name, target, effective permission, and whether it is active."""
    name = str(account.get("name") or "")
    card = {
        "name": name,
        "target": profile_target(account),
        "current": bool(active) and name == active,
    }
    try:
        card["mcp_effective"] = mcp_mode(cfg, account)
    except McpModeError as exc:
        card["mcp_effective"] = exc.mode
    return card


def mcp_profiles(cfg: dict) -> dict:
    """Profiles for MCP. No passwords and no key paths."""
    normalized = normalize_config(cfg)
    active = normalized.get("active")
    profiles = []
    for account in normalized.get("profiles") or []:
        if isinstance(account, dict) and account.get("name"):
            profiles.append(mcp_profile_card(normalized, account, active))
    return {"active": active, "profiles": profiles}


def mcp_current_profile(cfg: dict) -> dict:
    """The active profile card. Raises ProfileError when none is active."""
    shown = mcp_profiles(cfg)
    active = shown.get("active")
    if not active:
        raise ProfileError("missing", "")
    for item in shown["profiles"]:
        if item.get("name") == active:
            return item
    raise ProfileError("missing", str(active))


def connection_for(cfg: dict, name: str) -> dict:
    """Runtime config flattened onto ``name`` without changing ``cfg``."""
    if find_profile(cfg, name) is None:
        raise ProfileError("missing", name)
    trial = dict(cfg)
    trial["profiles"] = [dict(user) for user in (cfg.get("profiles") or []) if isinstance(user, dict)]
    trial["active"] = name
    _flatten(trial)
    return trial


def _profiles(cfg: dict) -> list[dict]:
    return [dict(user) for user in (cfg.get("profiles") or []) if isinstance(user, dict)]


def _index(users: list[dict], name: str | None) -> int | None:
    if not name:
        return None
    for idx, user in enumerate(users):
        if user.get("name") == name:
            return idx
    return None


def add_profile(
    cfg: dict,
    name: str,
    fields: dict,
    *,
    activate: bool = False,
    mcp_mode_value: str | None = None,
) -> None:
    text = validate_profile_name(name)
    users = _profiles(cfg)
    if _index(users, text) is not None:
        raise ProfileError("exists", text)
    if not fields.get("host"):
        raise ProfileError("need_host", text)
    account = _normalize_account(fields)
    account["name"] = text
    _set_account_mcp(account, mcp_mode_value)
    first = not users
    users.append(account)
    cfg["profiles"] = users
    if first or activate or not cfg.get("active"):
        cfg["active"] = text
    _flatten(cfg)


def update_profile(
    cfg: dict,
    name: str,
    fields: dict,
    *,
    new_name: str | None = None,
    mcp_mode_value: str | None = None,
) -> None:
    users = _profiles(cfg)
    idx = _index(users, name)
    if idx is None:
        raise ProfileError("missing", name)
    merged = dict(users[idx])
    for key, value in fields.items():
        if value is None:
            continue
        merged[key] = value
    account = _normalize_account(merged)
    account["name"] = str(users[idx].get("name") or name)
    if mcp_mode_value is None:
        mcp = _account_mcp(users[idx].get("mcp"))
        if mcp is not None:
            account["mcp"] = mcp
    else:
        _set_account_mcp(account, mcp_mode_value)
    if new_name is not None:
        renamed = validate_profile_name(new_name)
        if renamed != account["name"] and _index(users, renamed) is not None:
            raise ProfileError("exists", renamed)
        if cfg.get("active") == account["name"]:
            cfg["active"] = renamed
        account["name"] = renamed
    users[idx] = account
    cfg["profiles"] = users
    _flatten(cfg)


def _set_account_mcp(account: dict, mcp_mode_value: str | None) -> None:
    if mcp_mode_value is None or mcp_mode_value == MCP_MODE_INHERIT:
        account.pop("mcp", None)
        return
    mode = str(mcp_mode_value).strip().lower()
    if mode not in MCP_MODES:
        raise McpModeError(mode)
    account["mcp"] = {"mode": mode}


def activate_profile(cfg: dict, name: str) -> None:
    if find_profile(cfg, name) is None:
        raise ProfileError("missing", name)
    cfg["active"] = name
    _flatten(cfg)


def delete_profile(cfg: dict, name: str) -> None:
    users = _profiles(cfg)
    idx = _index(users, name)
    if idx is None:
        raise ProfileError("missing", name)
    users.pop(idx)
    if not users:
        cfg["profiles"] = []
        cfg.pop("active", None)
        _flatten(cfg)
        return
    if cfg.get("active") == name:
        new_idx = idx if idx < len(users) else len(users) - 1
        cfg["active"] = users[new_idx]["name"]
    cfg["profiles"] = users
    _flatten(cfg)


def upsert_profile(cfg: dict, name: str, fields: dict, *, activate: bool = True) -> None:
    """Create or replace one profile's connection fields. Keeps an existing MCP override."""
    text = validate_profile_name(name)
    if not fields.get("host"):
        raise ProfileError("need_host", text)
    users = _profiles(cfg)
    idx = _index(users, text)
    account = _normalize_account(fields)
    account["name"] = text
    if idx is not None:
        mcp = _account_mcp(users[idx].get("mcp"))
        if mcp is not None:
            account["mcp"] = mcp
        users[idx] = account
    else:
        users.append(account)
    cfg["profiles"] = users
    if activate or not cfg.get("active"):
        cfg["active"] = text
    _flatten(cfg)


def is_last_profile(cfg: dict, name: str) -> bool:
    users = [user for user in (cfg.get("profiles") or []) if isinstance(user, dict)]
    return len(users) == 1 and users[0].get("name") == name


def persist_connection_overlay(cfg: dict) -> None:
    """Write this process's connection overrides into the active profile.

    Does not create a profile. Add one with a name first.
    """
    fields = {key: cfg[key] for key in _CONN_KEYS if key in cfg and key != "http_port"}
    users = _profiles(cfg)
    active = cfg.get("active")
    idx = _index(users, str(active)) if active else None
    if idx is None:
        raise ProfileError("need_name")
    update_profile(cfg, str(users[idx]["name"]), fields)


def parse_mcp_mode(value: str, *, allow_inherit: bool = False) -> str:
    mode = str(value or "").strip().lower()
    if allow_inherit and mode == MCP_MODE_INHERIT:
        return mode
    if mode not in MCP_MODES:
        raise McpModeError(mode)
    return mode


class ConfigManager:
    DEFAULT_CONFIG_PATH = os.path.expanduser("~/.openwrt-cli.yaml")

    def __init__(self, config_path: str | None = None):
        raw = config_path or self.DEFAULT_CONFIG_PATH
        self.config_path = os.path.expanduser(os.path.expandvars(str(raw)))

    def load(self) -> dict:
        """Load config; missing or empty file returns defaults. Does not rewrite the file."""
        if not os.path.exists(self.config_path):
            return normalize_config({})

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return normalize_config(raw)

    def save(self, config: dict) -> None:
        """Write a canonical config to disk."""
        parent = os.path.dirname(self.config_path) or "."
        os.makedirs(parent, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                canonical_config(config),
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
