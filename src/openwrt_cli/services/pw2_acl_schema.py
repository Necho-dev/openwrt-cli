"""PassWall2 acl_rule UCI schema (luci-app-passwall2 26.7.16)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from openwrt_cli.i18n import t

SCHEMA_VERSION = "26.7.16"

LOGLEVELS = ("debug", "info", "warning", "error")
QUERY_STRATEGIES = ("UseIP", "UseIPv4", "UseIPv6")
DNS_PROTOCOLS = ("tcp", "udp", "doh")
DNS_DETOUR = ("remote", "direct")
DNS_REDIRECT = ("1", "0")
READONLY_KEYS = frozenset({"log_file"})
DISPLAY_PREFIXES = ("Use global config", "使用全局配置")
DISPLAY_EXACT = frozenset({"No patterns", "不使用"})
BOOL_KEYS = frozenset({"enabled", "log", "remote_fakedns"})
FieldKind = Literal["text", "bool", "choice", "list"]

_SPLIT_SOURCES = re.compile(r"[\s,]+")


@dataclass(frozen=True)
class Field:
    key: str
    kind: FieldKind = "text"
    choices: tuple[str, ...] = ()
    required: bool = False
    clear_empty: bool = True
    allow_custom: bool = False


FIELDS: tuple[Field, ...] = (
    Field("enabled", kind="bool"),
    Field("remarks", required=True),
    Field("log", kind="bool"),
    Field("loglevel", kind="choice", choices=LOGLEVELS),
    Field("interface"),
    Field("sources", kind="list"),
    Field("node"),
    Field("tcp_no_redir_ports", allow_custom=True),
    Field("udp_no_redir_ports", allow_custom=True),
    Field("tcp_redir_ports", allow_custom=True),
    Field("udp_redir_ports", allow_custom=True),
    Field("direct_dns_query_strategy", kind="choice", choices=QUERY_STRATEGIES),
    Field("remote_dns_protocol", kind="choice", choices=DNS_PROTOCOLS),
    Field("dns_mode", kind="choice", choices=DNS_PROTOCOLS, allow_custom=True),
    Field("remote_dns"),
    Field("remote_dns_detour", kind="choice", choices=DNS_DETOUR),
    Field("remote_fakedns", kind="bool"),
    Field("remote_dns_query_strategy", kind="choice", choices=QUERY_STRATEGIES),
    Field("dns_redirect", kind="choice", choices=DNS_REDIRECT),
)
KNOWN_KEYS = frozenset(item.key for item in FIELDS)
FIELD_BY_KEY = {item.key: item for item in FIELDS}


def parse_sources(value: Any) -> list[str]:
    """Keep `a-b` ranges as one item; split only on whitespace or comma."""
    if value in (None, ""):
        return []
    if isinstance(value, list):
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            for part in parse_sources(item):
                if part in seen:
                    continue
                seen.add(part)
                out.append(part)
        return out
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in _SPLIT_SOURCES.split(line):
            item = part.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
    return out


def _as_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return " ".join(str(x) for x in value if x not in (None, ""))
    return str(value)


def _truthy(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, list) or isinstance(right, list):
        return parse_sources(left) == parse_sources(right)
    return _as_text(left) == _as_text(right)


def changed_fields(original: dict[str, Any], collected: dict[str, Any]) -> dict[str, Any]:
    """Keep only collected keys that differ from the current UCI snapshot."""
    return {
        key: value
        for key, value in collected.items()
        if not values_equal(original.get(key), value)
    }


def looks_like_display(value: Any) -> bool:
    text = _as_text(value).strip()
    if not text:
        return False
    if text in DISPLAY_EXACT:
        return True
    return any(text.startswith(prefix) for prefix in DISPLAY_PREFIXES)


def normalize_value(item: Field, value: Any) -> Any:
    if item.kind == "list":
        return parse_sources(value)
    if item.kind == "bool":
        if value in (None, ""):
            return ""
        return "1" if _truthy(value) else "0"
    return _as_text(value).strip()


def validate_acl(
    draft: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    raw: bool = False,
    partial: bool = False,
) -> tuple[bool, list[str], dict[str, Any]]:
    merged = {**(existing or {}), **{k: v for k, v in draft.items() if v is not None}}
    errors: list[str] = []
    forbidden = [key for key in draft if key in READONLY_KEYS]
    if forbidden:
        errors.append(t("err.pw2_acl_readonly", keys=", ".join(sorted(forbidden))))
    if not raw:
        extra = [key for key in draft if key not in KNOWN_KEYS and not str(key).startswith(".")]
        if extra:
            errors.append(t("err.pw2_acl_unknown", keys=", ".join(sorted(extra))))
    normalized: dict[str, Any] = {}
    for key, value in merged.items():
        if key.startswith(".") or key in READONLY_KEYS:
            continue
        item = FIELD_BY_KEY.get(key)
        if item is None:
            if raw and key in draft:
                if looks_like_display(value):
                    errors.append(t("err.pw2_acl_display", field=key))
                    continue
                if isinstance(value, list):
                    normalized[key] = parse_sources(value)
                elif isinstance(value, bool):
                    normalized[key] = "1" if value else "0"
                else:
                    text = _as_text(value).strip()
                    if text:
                        normalized[key] = text
            continue
        if key not in draft and partial:
            continue
        if looks_like_display(value):
            errors.append(t("err.pw2_acl_display", field=key))
            continue
        norm = normalize_value(item, value)
        empty = norm in ("", [], None)
        if item.required and empty and not (partial and key not in draft):
            errors.append(t("err.pw2_acl_required", field=key))
            continue
        if item.kind == "choice" and item.choices and not empty:
            if norm not in item.choices and not item.allow_custom:
                errors.append(t("err.pw2_acl_choice", field=key, value=norm))
                continue
        if empty and item.clear_empty:
            continue
        normalized[key] = norm
    if not partial:
        for item in FIELDS:
            if item.required and item.key not in normalized:
                errors.append(t("err.pw2_acl_required", field=item.key))
    seen: set[str] = set()
    uniq: list[str] = []
    for msg in errors:
        if msg in seen:
            continue
        seen.add(msg)
        uniq.append(msg)
    return not uniq, uniq, normalized
