"""Parse common proxy share URLs into PassWall2 UCI fields."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from openwrt_cli.i18n import t

_SCHEMES = ("vless", "vmess", "trojan", "ss", "hysteria2", "hy2")
_TRANSPORT = {
    "tcp": "tcp",
    "raw": "tcp",
    "ws": "ws",
    "websocket": "ws",
    "grpc": "grpc",
    "h2": "h2",
    "http": "h2",
    "httpupgrade": "httpupgrade",
    "xhttp": "xhttp",
    "splithttp": "xhttp",
    "quic": "quic",
    "mkcp": "mkcp",
    "kcp": "mkcp",
}


class ShareURLError(ValueError):
    pass


def parse_share_url(url: str) -> dict[str, Any]:
    text = (url or "").strip().splitlines()[0].strip()
    if not text:
        raise ShareURLError(t("err.pw2_share_url"))
    scheme = text.split(":", 1)[0].lower()
    if scheme not in _SCHEMES:
        raise ShareURLError(t("err.pw2_share_url"))
    if scheme == "vmess":
        return _parse_vmess(text)
    if scheme == "ss":
        return _parse_ss(text)
    if scheme in {"hysteria2", "hy2"}:
        return _parse_hy2(text)
    return _parse_userinfo(text, scheme)


def parse_share_urls(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(parse_share_url(line))
    if not rows:
        raise ShareURLError(t("err.pw2_share_url"))
    return rows


def _b64(text: str) -> str:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii")).decode("utf-8", errors="replace")


def _q(query: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = query.get(name)
        if values and values[0]:
            return unquote(values[0])
    return ""


def _remarks(parsed, fallback: str) -> str:
    frag = unquote((parsed.fragment or "").strip())
    return frag or fallback


def _host_port(parsed) -> tuple[str, str]:
    host = (parsed.hostname or "").strip("[]")
    port = str(parsed.port or "")
    return host, port


def _transport(value: str) -> str:
    return _TRANSPORT.get((value or "tcp").lower(), value or "tcp")


def _security(query: dict[str, list[str]], out: dict[str, Any]) -> None:
    security = _q(query, "security").lower()
    sni = _q(query, "sni", "peer")
    if sni:
        out["tls_serverName"] = sni
    if security in {"tls", "xtls", "reality"}:
        out["tls"] = "1"
    if security == "reality":
        out["reality"] = "1"
        pbk = _q(query, "pbk", "publickey")
        sid = _q(query, "sid", "shortid")
        spx = _q(query, "spx", "spiderx")
        if pbk:
            out["reality_publicKey"] = pbk
        if sid:
            out["reality_shortId"] = sid
        if spx:
            out["reality_spiderX"] = spx
    fp = _q(query, "fp", "fingerprint")
    if fp:
        out["fingerprint"] = fp
    alpn = _q(query, "alpn")
    if alpn:
        out["alpn"] = alpn.replace("%2C", ",")
    if _q(query, "insecure", "allowInsecure") in {"1", "true"}:
        out["allowInsecure"] = "1"


def _stream(query: dict[str, list[str]], out: dict[str, Any]) -> None:
    transport = _transport(_q(query, "type", "transport"))
    if transport:
        out["transport"] = transport
    path = _q(query, "path", "serviceName")
    host = _q(query, "host", "authority")
    if transport == "grpc":
        if path:
            out["serviceName"] = path
            out["grpc_serviceName"] = path
    else:
        if path:
            out["path"] = path
            if transport == "ws":
                out["ws_path"] = path
        if host:
            out["host"] = host
            if transport == "ws":
                out["ws_host"] = host
    flow = _q(query, "flow")
    if flow:
        out["flow"] = flow
    encryption = _q(query, "encryption")
    if encryption:
        out["encryption"] = encryption


def _parse_userinfo(url: str, scheme: str) -> dict[str, Any]:
    parsed = urlparse(url)
    host, port = _host_port(parsed)
    if not host or not port:
        raise ShareURLError(t("err.pw2_share_url"))
    query = parse_qs(parsed.query, keep_blank_values=True)
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    out: dict[str, Any] = {
        "type": "Xray",
        "protocol": "trojan" if scheme == "trojan" else scheme,
        "address": host,
        "port": port,
        "remarks": _remarks(parsed, host),
    }
    if scheme == "vless":
        out["uuid"] = user
        out["encryption"] = out.get("encryption") or "none"
    elif scheme == "trojan":
        out["password"] = user or password
    _security(query, out)
    _stream(query, out)
    return out


def _parse_hy2(url: str) -> dict[str, Any]:
    parsed = urlparse(url.replace("hy2://", "hysteria2://", 1))
    host, port = _host_port(parsed)
    if not host or not port:
        raise ShareURLError(t("err.pw2_share_url"))
    query = parse_qs(parsed.query, keep_blank_values=True)
    out: dict[str, Any] = {
        "type": "Hysteria2",
        "protocol": "hysteria2",
        "address": host,
        "port": port,
        "password": unquote(parsed.username or parsed.password or ""),
        "remarks": _remarks(parsed, host),
        "tls": "1",
    }
    sni = _q(query, "sni", "peer")
    if sni:
        out["tls_serverName"] = sni
    obfs = _q(query, "obfs")
    if obfs:
        out["obfs"] = obfs
    obfs_pw = _q(query, "obfs-password", "obfsPassword")
    if obfs_pw:
        out["obfsPassword"] = obfs_pw
    if _q(query, "insecure") in {"1", "true"}:
        out["allowInsecure"] = "1"
    return out


def _parse_ss(url: str) -> dict[str, Any]:
    body = url[5:]
    remarks = ""
    if "#" in body:
        body, frag = body.split("#", 1)
        remarks = unquote(frag)
    query = ""
    if "?" in body:
        body, query = body.split("?", 1)
    userinfo = ""
    hostport = body
    if "@" in body:
        userinfo, hostport = body.rsplit("@", 1)
        if ":" not in userinfo:
            try:
                userinfo = _b64(userinfo)
            except Exception as e:
                raise ShareURLError(t("err.pw2_share_url")) from e
    else:
        try:
            decoded = _b64(body)
        except Exception as e:
            raise ShareURLError(t("err.pw2_share_url")) from e
        if "@" in decoded:
            userinfo, hostport = decoded.rsplit("@", 1)
        else:
            raise ShareURLError(t("err.pw2_share_url"))
    if ":" not in userinfo or ":" not in hostport:
        raise ShareURLError(t("err.pw2_share_url"))
    method, password = userinfo.split(":", 1)
    host, port = hostport.rsplit(":", 1)
    host = host.strip("[]")
    out: dict[str, Any] = {
        "type": "Xray",
        "protocol": "shadowsocks",
        "address": host,
        "port": port,
        "method": unquote(method),
        "password": unquote(password),
        "remarks": remarks or host,
    }
    if query:
        _stream(parse_qs(query, keep_blank_values=True), out)
        _security(parse_qs(query, keep_blank_values=True), out)
    return out


def _parse_vmess(url: str) -> dict[str, Any]:
    raw = url[8:]
    if "?" in raw:
        raw = raw.split("?", 1)[0]
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    try:
        data = json.loads(_b64(raw))
    except Exception as e:
        raise ShareURLError(t("err.pw2_share_url")) from e
    if not isinstance(data, dict):
        raise ShareURLError(t("err.pw2_share_url"))
    host = str(data.get("add") or data.get("address") or "")
    port = str(data.get("port") or "")
    if not host or not port:
        raise ShareURLError(t("err.pw2_share_url"))
    out: dict[str, Any] = {
        "type": "Xray",
        "protocol": "vmess",
        "address": host,
        "port": port,
        "uuid": str(data.get("id") or ""),
        "remarks": str(data.get("ps") or data.get("remarks") or host),
    }
    aid = str(data.get("aid") or "")
    if aid:
        out["encryption"] = "auto"
    net = _transport(str(data.get("net") or "tcp"))
    if net:
        out["transport"] = net
    path = str(data.get("path") or "")
    host_h = str(data.get("host") or "")
    if net == "grpc":
        if path:
            out["serviceName"] = path
            out["grpc_serviceName"] = path
    else:
        if path:
            out["path"] = path
            if net == "ws":
                out["ws_path"] = path
        if host_h:
            out["host"] = host_h
            if net == "ws":
                out["ws_host"] = host_h
    tls = str(data.get("tls") or "").lower()
    if tls in {"tls", "1", "true"}:
        out["tls"] = "1"
    sni = str(data.get("sni") or "")
    if sni:
        out["tls_serverName"] = sni
    fp = str(data.get("fp") or "")
    if fp:
        out["fingerprint"] = fp
    return out
