"""YAML config file (~/.openwrt-cli.yaml)."""

from __future__ import annotations

import os
from typing import Any

import yaml

_CANONICAL_KEYS = (
    "host",
    "user",
    "transport",
    "scheme",
    "port",
    "language",
    "identity_file",
    "verify_ssl",
    "password",
)
_SKIP_KEYS = frozenset({"_config_path", "http_port"})


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


def normalize_config(raw: Any) -> dict:
    """Merge defaults and collapse legacy http_port into the effective port."""
    cfg = dict(_default_config())
    original = raw if isinstance(raw, dict) else {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key == "_config_path":
                continue
            cfg[key] = value
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
        if "verify_ssl" not in cfg or cfg.get("verify_ssl") is None:
            cfg["verify_ssl"] = False
        else:
            cfg["verify_ssl"] = bool(cfg.get("verify_ssl"))
    else:
        cfg["transport"] = "ssh"
        cfg["port"] = _as_int(cfg.get("port"), 22)
        cfg.pop("http_port", None)
        cfg.pop("scheme", None)
        cfg.pop("verify_ssl", None)
    if cfg.get("identity_file"):
        cfg["identity_file"] = os.path.expanduser(str(cfg["identity_file"]))
    language = str(cfg.get("language") or "").strip()
    if language:
        cfg["language"] = language
    else:
        cfg.pop("language", None)
    if not cfg.get("user"):
        cfg["user"] = "root"
    return cfg


def canonical_config(cfg: dict) -> dict:
    """Stable key order for writing; drop internals and empty values."""
    normalized = normalize_config(cfg)
    out: dict[str, Any] = {}
    transport = normalized.get("transport") or "ssh"
    for key in _CANONICAL_KEYS:
        if key in ("scheme", "verify_ssl") and transport != "http":
            continue
        value = normalized.get(key)
        if value is None or value == "":
            continue
        out[key] = value
    for key, value in normalized.items():
        if key in out or key in _SKIP_KEYS or key in _CANONICAL_KEYS:
            continue
        if value is None or value == "":
            continue
        out[key] = value
    return out


def public_config(cfg: dict, *, path: str | None = None) -> dict:
    """Effective config for display / JSON. Password is masked."""
    data = canonical_config(cfg)
    if data.get("password"):
        data["password"] = "***"
    if path:
        return {"path": path, **data}
    return data


def _default_config() -> dict:
    return {
        "host": None,
        "user": "root",
        "port": 22,
        "transport": "ssh",
    }


class ConfigManager:
    DEFAULT_CONFIG_PATH = os.path.expanduser("~/.openwrt-cli.yaml")

    def __init__(self, config_path: str | None = None):
        raw = config_path or self.DEFAULT_CONFIG_PATH
        self.config_path = os.path.expanduser(os.path.expandvars(str(raw)))

    def load(self) -> dict:
        """Load config; missing or empty file returns defaults."""
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
