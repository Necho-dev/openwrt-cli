"""PassWall2 node UCI schema (unprefixed keys, luci-app-passwall2 26.7.16)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from openwrt_cli.i18n import t

SCHEMA_VERSION = "26.7.16"

NODE_TYPES = ("Xray", "sing-box", "Hysteria2")
SPECIAL_PROTOCOLS = frozenset({"_shunt", "_balancing", "_urltest", "_iface"})
PROTOCOLS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "Xray": (
        "vmess", "vless", "trojan", "shadowsocks", "socks", "http", "wireguard",
        "_shunt", "_balancing", "_urltest", "_iface",
    ),
    "sing-box": (
        "vmess", "vless", "trojan", "shadowsocks", "socks", "http", "wireguard",
        "hysteria", "hysteria2", "tuic", "ssh", "anytls",
        "_shunt", "_balancing", "_urltest", "_iface",
    ),
    "Hysteria2": ("hysteria2",),
}
TRANSPORTS = ("tcp", "http", "ws", "grpc", "h2", "httpupgrade", "xhttp", "quic", "mkcp")
DOMAIN_STRATEGIES = ("prefer_ipv4", "prefer_ipv6", "ipv4_only", "ipv6_only")
CHAIN_PROXY = ("1", "2")
BOOL_KEYS = frozenset({
    "tls", "reality", "allowInsecure", "utls", "mux", "uot",
})
FieldKind = Literal["text", "port", "secret", "choice", "bool", "list"]


@dataclass(frozen=True)
class Field:
    key: str
    kind: FieldKind = "text"
    choices: tuple[str, ...] = ()
    required: bool = False
    depends: dict[str, tuple[str, ...]] = field(default_factory=dict)
    clear_empty: bool = True


_COMMON = (
    Field("remarks", required=True),
    Field("group"),
    Field("type", kind="choice", choices=NODE_TYPES, required=True),
    Field("protocol", kind="choice", required=True),
    Field("address", required=True, depends={"protocol": tuple(
        p for p in {item for items in PROTOCOLS_BY_TYPE.values() for item in items} - SPECIAL_PROTOCOLS
    )}),
    Field("port", kind="port", required=True, depends={"protocol": tuple(
        p for p in {item for items in PROTOCOLS_BY_TYPE.values() for item in items} - SPECIAL_PROTOCOLS
    )}),
)
_AUTH = (
    Field("uuid", kind="secret", depends={"protocol": ("vmess", "vless")}),
    Field("password", kind="secret", depends={"protocol": (
        "trojan", "shadowsocks", "socks", "http", "hysteria", "hysteria2", "tuic", "anytls",
    )}),
    Field("username", depends={"protocol": ("socks", "http", "ssh")}),
    Field("method", depends={"protocol": ("shadowsocks",)}),
    Field("encryption", depends={"protocol": ("vmess", "vless")}),
    Field("flow", depends={"protocol": ("vless",)}),
)
_TRANSPORT = (
    Field("uot", kind="bool"),
    Field("transport", kind="choice", choices=TRANSPORTS),
    Field("tcp_guise", kind="choice", choices=("none", "http")),
    Field("domain_resolver"),
    Field("domain_strategy", kind="choice", choices=DOMAIN_STRATEGIES),
    Field("chain_proxy", kind="choice", choices=CHAIN_PROXY),
    Field("path", depends={"transport": ("ws", "h2", "httpupgrade", "xhttp")}),
    Field("host", depends={"transport": ("ws", "h2", "httpupgrade", "xhttp")}),
    Field("ws_host", depends={"transport": ("ws",)}),
    Field("ws_path", depends={"transport": ("ws",)}),
    Field("serviceName", depends={"transport": ("grpc",)}),
    Field("grpc_serviceName", depends={"transport": ("grpc",)}),
)
_TLS = (
    Field("tls", kind="bool", depends={"protocol": (
        "vmess", "vless", "trojan", "shadowsocks", "hysteria", "hysteria2", "tuic", "anytls",
    )}),
    Field("tls_serverName"),
    Field("fingerprint", depends={"tls": ("1", "true", "yes")}),
    Field("allowInsecure", kind="bool"),
    Field("alpn"),
    Field("utls", kind="bool"),
    Field("reality", kind="bool"),
    Field("reality_publicKey", kind="secret", depends={"reality": ("1", "true", "yes")}),
    Field("reality_shortId", depends={"reality": ("1", "true", "yes")}),
    Field("reality_spiderX", depends={"reality": ("1", "true", "yes")}),
)
_HY2 = (
    Field("hop", depends={"type": ("Hysteria2",), "protocol": ("hysteria2", "hysteria")}),
    Field("obfs", depends={"type": ("Hysteria2", "sing-box"), "protocol": ("hysteria2", "hysteria")}),
    Field("obfsPassword", kind="secret", depends={"protocol": ("hysteria2", "hysteria")}),
    Field("up_mbps", depends={"protocol": ("hysteria2", "hysteria")}),
    Field("down_mbps", depends={"protocol": ("hysteria2", "hysteria")}),
)
_SPECIAL = (
    Field("default_node", depends={"protocol": ("_shunt", "_balancing", "_urltest")}),
    Field("fallback_node", depends={"protocol": ("_balancing",)}),
    Field("urltest_node", kind="list", depends={"protocol": ("_urltest",)}),
    Field("iface", depends={"protocol": ("_iface",)}),
)

FIELDS: tuple[Field, ...] = _COMMON + _AUTH + _TRANSPORT + _TLS + _HY2 + _SPECIAL
KNOWN_KEYS = frozenset(item.key for item in FIELDS)


def _as_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return " ".join(str(x) for x in value if x not in (None, ""))
    return str(value)


def _truthy(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def _depends_ok(field: Field, draft: dict[str, Any]) -> bool:
    for key, allowed in field.depends.items():
        current = _as_text(draft.get(key))
        if key in BOOL_KEYS:
            current = "1" if _truthy(current) else "0"
        if current not in allowed:
            return False
    return True


def visible_fields(type_name: str, protocol: str, draft: dict[str, Any] | None = None) -> list[Field]:
    state = {**(draft or {}), "type": type_name, "protocol": protocol}
    if type_name == "Hysteria2" and not protocol:
        state["protocol"] = "hysteria2"
    out: list[Field] = []
    for item in FIELDS:
        if item.key == "protocol":
            item = Field(
                "protocol",
                kind="choice",
                choices=PROTOCOLS_BY_TYPE.get(type_name, ()),
                required=type_name != "Hysteria2",
            )
        if item.key == "type":
            out.append(item)
            continue
        if not _depends_ok(item, state) and item.key not in {"remarks", "group", "protocol"}:
            continue
        out.append(item)
    return out


def normalize_value(field: Field, value: Any) -> Any:
    if field.kind == "list":
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return [part for part in str(value).replace(",", " ").split() if part]
    if field.kind == "bool":
        if value in (None, ""):
            return ""
        return "1" if _truthy(value) else "0"
    text = _as_text(value).strip()
    return text


def validate_node(
    draft: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    raw: bool = False,
    partial: bool = False,
) -> tuple[bool, list[str], dict[str, Any]]:
    merged = {**(existing or {}), **{k: v for k, v in draft.items() if v is not None}}
    type_name = _as_text(merged.get("type"))
    protocol = _as_text(merged.get("protocol"))
    if type_name == "Hysteria2" and not protocol:
        protocol = "hysteria2"
        merged["protocol"] = protocol
    errors: list[str] = []
    if type_name and type_name not in NODE_TYPES:
        errors.append(t("err.pw2_node_type", value=type_name))
    allowed = PROTOCOLS_BY_TYPE.get(type_name, ())
    if protocol and allowed and protocol not in allowed:
        errors.append(t("err.pw2_node_protocol", value=protocol))
    if not raw:
        extra = [key for key in draft if key not in KNOWN_KEYS and not str(key).startswith(".")]
        if extra:
            errors.append(t("err.pw2_node_unknown", keys=", ".join(sorted(extra))))
    visible = {item.key: item for item in visible_fields(type_name, protocol, merged)}
    normalized: dict[str, Any] = {}
    for key, value in merged.items():
        if key.startswith("."):
            continue
        item = visible.get(key)
        if item is None:
            if raw and key in draft:
                normalized[key] = value if not isinstance(value, bool) else ("1" if value else "0")
            continue
        if key not in draft and partial:
            continue
        norm = normalize_value(item, value)
        empty = norm in ("", [], None)
        if item.required and empty and not (partial and key not in draft):
            errors.append(t("err.pw2_node_required", field=key))
            continue
        if item.kind == "port" and norm:
            try:
                port = int(norm)
            except ValueError:
                errors.append(t("err.pw2_node_port", value=norm))
                continue
            if port < 1 or port > 65535:
                errors.append(t("err.pw2_node_port", value=norm))
                continue
        if item.kind == "choice" and item.choices and norm and norm not in item.choices:
            errors.append(t("err.pw2_node_choice", field=key, value=norm))
            continue
        if empty and item.clear_empty:
            continue
        normalized[key] = norm
    if not partial:
        for item in visible.values():
            if item.required and item.key not in normalized:
                if item.key in {"address", "port"} and protocol in SPECIAL_PROTOCOLS:
                    continue
                errors.append(t("err.pw2_node_required", field=item.key))
    # unique required errors
    seen: set[str] = set()
    uniq: list[str] = []
    for msg in errors:
        if msg in seen:
            continue
        seen.add(msg)
        uniq.append(msg)
    return not uniq, uniq, normalized
