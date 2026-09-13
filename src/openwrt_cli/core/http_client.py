"""OpenWrt ubus HTTP JSON-RPC 客户端（LuCI / uhttpd-mod-ubus）。"""

from __future__ import annotations

import http.client
import json
import secrets
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Literal
from urllib.parse import urlencode

from openwrt_cli.core.channels.uci import encode_uci_values, option_from_values, resolve_section, section_id_from_add
from openwrt_cli.core.config import resolve_http_port
from openwrt_cli.core.device import Capability, DeviceBase
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError, DeviceConnectionError
from openwrt_cli.i18n import t

UBUS_OK = 0
NULL_SESSION = "00000000000000000000000000000000"


class _SessionExpired(Exception):
    """明确的会话失效：session not found / expired / not logged in。"""


class _AccessDenied(Exception):
    """JSON-RPC Access denied：可能是会话死了，也可能是方法 ACL。"""


def _is_session_dead_message(message: str | None) -> bool:
    msg = (message or "").lower()
    return any(
        token in msg
        for token in ("session not found", "not logged in", "session expired")
    )

_HTTP_CAPS = frozenset({
    Capability.UBUS,
    Capability.UCI,
    Capability.FILE_READ,
    Capability.INITD,
})

_LUCI_EXACT = frozenset({
    "get_log",
    "get_acl_log",
    "get_redir_log",
    "ping_node",
    "urltest_node",
    "get_now_use_node",
    "get_node",
    "check_passwall2",
})
_LUCI_COM = frozenset({
    "geoview", "sing-box", "singbox", "xray", "hysteria", "hysteria2",
})
_LUCI_PREFIX = ("version_", "check_")


def luci_action_allowed(action: str) -> bool:
    """Read-only PassWall2 dispatcher actions. Writes such as clear_log are rejected."""
    name = (action or "").strip()
    if not name or "/" in name or ".." in name or "\\" in name:
        return False
    if name in _LUCI_EXACT:
        return True
    for prefix in _LUCI_PREFIX:
        if name.startswith(prefix):
            return name[len(prefix):] in _LUCI_COM
    return False


class _HTTPUbus:
    def __init__(self, device: HTTPDevice):
        self._device = device

    def call(self, obj: str, method: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> Any:
        return self._device.call(obj, method, params, timeout=timeout)


class _HTTPUci:
    def __init__(self, device: HTTPDevice):
        self._device = device

    def show(self, config: str) -> dict[str, dict[str, Any]]:
        data = self._device.call("uci", "get", {"config": config})
        return data.get("values") or {}

    def get(self, path: str) -> str:
        parts = path.split(".")
        values = self.show(parts[0])
        return option_from_values(values, path)

    def set(self, path: str, value: str) -> None:
        parts = path.split(".")
        if len(parts) < 3:
            raise DeviceCommandError(t("err.uci_set", path=path))
        config, section, option = parts[0], parts[1], parts[2]
        self.set_values(config, section, {option: value})

    def set_values(self, config: str, section: str, values: dict[str, Any]) -> None:
        payload = encode_uci_values(values)
        if not payload:
            return
        shown = self.show(config)
        section = resolve_section(shown, section)
        typ = (shown.get(section) or {}).get(".type")
        scalars = {key: value for key, value in payload.items() if not isinstance(value, list)}
        lists = {key: value for key, value in payload.items() if isinstance(value, list) and value}
        if scalars:
            self._set(config, section, scalars, typ)
        for key, value in lists.items():
            self._set(config, section, {key: value}, typ)

    def _set(self, config: str, section: str, values: dict[str, Any], typ: Any) -> None:
        params: dict[str, Any] = {"config": config, "section": section, "values": values}
        if typ:
            params["type"] = str(typ)
        self._device.call("uci", "set", params)

    def add(self, config: str, typ: str, values: dict[str, Any] | None = None, name: str | None = None) -> str:
        params: dict[str, Any] = {"config": config, "type": typ}
        if name:
            params["name"] = name
        encoded = encode_uci_values(values)
        if encoded:
            params["values"] = encoded
        data = self._device.call("uci", "add", params)
        sid = section_id_from_add(data)
        if not sid:
            raise DeviceCommandError(t("err.uci_add", config=config, typ=typ))
        return sid

    def delete(
        self,
        config: str,
        section: str,
        option: str | None = None,
        options: list[str] | None = None,
    ) -> None:
        shown = self.show(config)
        section = resolve_section(shown, section)
        params: dict[str, Any] = {"config": config, "section": section}
        typ = (shown.get(section) or {}).get(".type")
        if typ:
            params["type"] = str(typ)
        names = [item for item in (options or ([option] if option else [])) if item]
        if len(names) == 1:
            params["option"] = names[0]
        elif names:
            params["options"] = names
        self._device.call("uci", "delete", params)

    def commit(self, config: str | None = None) -> None:
        params: dict[str, Any] = {"config": config} if config else {}
        self._device.call("uci", "commit", params)


class _HTTPShell:
    def exec(self, cmd: str, timeout: int = 30, check: bool = False) -> str:
        raise CapabilityError(
            t("err.http_no_shell", cmd=cmd),
            missing=("shell",),
            hint=t("err.use_ssh"),
        )


class _HTTPFs:
    def __init__(self, device: HTTPDevice):
        self._device = device

    def exists(self, path: str) -> bool:
        try:
            self._device.call("file", "stat", {"path": path})
            return True
        except (DeviceCommandError, DeviceConnectionError):
            return False

    def read(self, path: str) -> str:
        try:
            data = self._device.call("file", "read", {"path": path})
        except DeviceCommandError as e:
            raise CapabilityError(
                t("err.http_no_read", path=path, error=e),
                missing=("file_read",),
            ) from e
        raw = data.get("data") or ""
        if data.get("encoding") == "base64":
            import base64
            return base64.b64decode(raw).decode("utf-8", errors="replace")
        return raw if isinstance(raw, str) else str(raw)

    def write(self, path: str, content: str) -> None:
        raise CapabilityError(
            t("err.http_no_write"),
            missing=("file_write",),
            hint=t("err.use_ssh"),
        )


class HTTPDevice(DeviceBase):
    """LuCI/ubus HTTP 设备。不具备 SHELL / IPTABLES / TC / FILE_WRITE。"""

    transport: Literal["ssh", "http"] = "http"
    capabilities = _HTTP_CAPS

    def __init__(
        self,
        host: str,
        user: str = "root",
        password: str | None = None,
        port: int | None = None,
        scheme: str = "https",
        verify_ssl: bool = False,
        timeout: int = 15,
        identity_file: str | None = None,
    ):
        self.host = host
        self.user = user
        self.password = password or ""
        self.scheme = scheme or "https"
        self.port = int(port or (443 if self.scheme == "https" else 80))
        self.verify_ssl = bool(verify_ssl)
        self.timeout = timeout
        self.session: str | None = None
        self._session_timeout = 300
        self._login_at = 0.0
        self._last_ok = 0.0
        self._auth_lock = threading.Lock()
        self._rpc_id = 0
        self._ssl_ctx = None if self.scheme != "https" else (
            ssl.create_default_context() if self.verify_ssl else ssl._create_unverified_context()
        )
        self.base = f"{self.scheme}://{self.host}:{self.port}".rstrip("/")
        self.ubus_url = f"{self.base}/ubus"
        self._http_lock = threading.RLock()
        self._http_conn: http.client.HTTPConnection | None = None
        self._ubus = _HTTPUbus(self)
        self._uci = _HTTPUci(self)
        self._shell = _HTTPShell()
        self._fs = _HTTPFs(self)
        self._luci_token = ""
        self._luci_sid = ""
        self._luci_form_authed = False
        self._hold_session = False
        self._login()

    @classmethod
    def from_config(cls, cfg: dict) -> HTTPDevice:
        scheme = cfg.get("scheme") or "https"
        return cls(
            host=cfg["host"],
            user=cfg.get("user", "root"),
            password=cfg.get("password"),
            port=resolve_http_port(cfg),
            scheme=scheme,
            verify_ssl=cfg.get("verify_ssl", False),
        )

    @property
    def ubus(self) -> _HTTPUbus:
        return self._ubus

    @property
    def uci(self) -> _HTTPUci:
        return self._uci

    @property
    def shell(self) -> _HTTPShell:
        return self._shell

    @property
    def fs(self) -> _HTTPFs:
        return self._fs

    def _login(self) -> None:
        try:
            status, data = self._call(NULL_SESSION, "session", "login", {
                "username": self.user,
                "password": self.password,
            })
        except DeviceConnectionError:
            raise
        except DeviceCommandError as e:
            raise DeviceConnectionError(self._login_hint(str(e))) from e
        except Exception as e:
            raise DeviceConnectionError(
                t(
                    "err.http_connect",
                    url=self.ubus_url,
                    error=f"{type(e).__name__}: {e}",
                    base=self.base,
                )
            ) from e
        if status != UBUS_OK or not isinstance(data, dict) or not data.get("ubus_rpc_session"):
            raise DeviceConnectionError(
                t("err.http_login", status=status, base=self.base)
            )
        self.session = data["ubus_rpc_session"]
        now = time.monotonic()
        self._login_at = now
        self._last_ok = now
        try:
            self._session_timeout = max(30, int(data.get("timeout") or 300))
        except (TypeError, ValueError):
            self._session_timeout = 300
        self._install_luci_token()

    def _install_luci_token(self) -> None:
        """Modern LuCI session_retrieve requires values.token. Never log it."""
        self._luci_form_authed = False
        if not self.session or self.session == NULL_SESSION:
            self._luci_token = ""
            self._luci_sid = ""
            return
        self._luci_sid = self.session
        token = secrets.token_hex(16)
        try:
            status, _ = self._call(self.session, "session", "set", {"values": {"token": token}})
        except (DeviceCommandError, DeviceConnectionError, _SessionExpired, _AccessDenied):
            self._luci_token = ""
            return
        self._luci_token = token if status == UBUS_OK else ""

    def _login_hint(self, detail: str) -> str:
        missing_session = "object not found" in detail.lower()
        if missing_session:
            return t("err.http_no_session")
        return t("err.http_login_detail", detail=detail)

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _ensure_conn(self, timeout: float) -> http.client.HTTPConnection:
        if self._http_conn is None:
            if self.scheme == "https":
                self._http_conn = http.client.HTTPSConnection(
                    self.host, self.port, timeout=timeout, context=self._ssl_ctx,
                )
            else:
                self._http_conn = http.client.HTTPConnection(
                    self.host, self.port, timeout=timeout,
                )
        else:
            self._http_conn.timeout = timeout
            if self._http_conn.sock is not None:
                self._http_conn.sock.settimeout(timeout)
        return self._http_conn

    def _drop_conn(self) -> None:
        with self._http_lock:
            conn, self._http_conn = self._http_conn, None
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            pass

    def _post(self, payload: dict | list, timeout: int | None = None):
        body = json.dumps(payload).encode("utf-8")
        wait = float(timeout or self.timeout)
        headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
        with self._http_lock:
            last_err: Exception | None = None
            for attempt in range(2):
                try:
                    conn = self._ensure_conn(wait)
                    conn.request("POST", "/ubus", body, headers)
                    resp = conn.getresponse()
                    raw = resp.read()
                    if resp.status >= 400:
                        self._drop_conn()
                        raise DeviceConnectionError(f"HTTP {resp.status}")
                    parsed = json.loads(raw.decode("utf-8", errors="replace"))
                    if isinstance(parsed, list):
                        if not parsed:
                            raise DeviceCommandError(t("err.ubus_empty"))
                        return parsed[0]
                    return parsed
                except DeviceConnectionError:
                    raise
                except DeviceCommandError:
                    raise
                except (TimeoutError, socket.timeout) as e:
                    self._drop_conn()
                    raise DeviceConnectionError(t("err.timeout")) from e
                except (
                    http.client.RemoteDisconnected,
                    http.client.CannotSendRequest,
                    http.client.ResponseNotReady,
                    BrokenPipeError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                ) as e:
                    self._drop_conn()
                    last_err = e
                    if attempt == 0:
                        continue
                    raise DeviceConnectionError(t("err.router_disconnected")) from e
                except OSError as e:
                    self._drop_conn()
                    reason = str(e)
                    if "timed out" in reason.lower() or getattr(e, "errno", None) == 60:
                        raise DeviceConnectionError(t("err.timeout")) from e
                    raise DeviceConnectionError(t("err.router_down_reason", reason=reason)) from e
            raise DeviceConnectionError(t("err.router_down_reason", reason=last_err))

    def _http_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: int | None = None,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        wait = float(timeout or self.timeout)
        hdrs = {"Connection": "keep-alive"}
        if headers:
            hdrs.update(headers)
        with self._http_lock:
            last_err: Exception | None = None
            for attempt in range(2):
                try:
                    conn = self._ensure_conn(wait)
                    conn.request(method, path, body, hdrs)
                    resp = conn.getresponse()
                    raw = resp.read()
                    status = resp.status
                    rh = list(resp.getheaders())
                    if status >= 500:
                        self._drop_conn()
                    return status, rh, raw
                except (TimeoutError, socket.timeout) as e:
                    self._drop_conn()
                    raise DeviceConnectionError(t("err.timeout")) from e
                except (
                    http.client.RemoteDisconnected,
                    http.client.CannotSendRequest,
                    http.client.ResponseNotReady,
                    BrokenPipeError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                ) as e:
                    self._drop_conn()
                    last_err = e
                    if attempt == 0:
                        continue
                    raise DeviceConnectionError(t("err.router_disconnected")) from e
                except OSError as e:
                    self._drop_conn()
                    reason = str(e)
                    if "timed out" in reason.lower() or getattr(e, "errno", None) == 60:
                        raise DeviceConnectionError(t("err.timeout")) from e
                    raise DeviceConnectionError(t("err.router_down_reason", reason=reason)) from e
            raise DeviceConnectionError(t("err.router_down_reason", reason=last_err))

    def _luci_cookie(self) -> str:
        sid = self._luci_sid or self.session or ""
        return f"sysauth={sid}; sysauth_http={sid}; sysauth_https={sid}"

    def _luci_headers(self) -> dict[str, str]:
        return {
            "Cookie": self._luci_cookie(),
            "Accept": "*/*",
        }

    def _luci_path(self, action: str, params: dict[str, Any] | None, *, stok: bool) -> str:
        base = "/cgi-bin/luci"
        if stok and self._luci_token:
            base = f"/cgi-bin/luci/;stok={self._luci_token}"
        path = f"{base}/admin/services/passwall2/{action}"
        if params:
            path = f"{path}?{urlencode(params, doseq=True)}"
        return path

    def _cookie_sid(self, headers: list[tuple[str, str]]) -> str | None:
        for name, value in headers:
            if name.lower() != "set-cookie":
                continue
            first = (value or "").split(";", 1)[0]
            if "=" not in first:
                continue
            key, val = first.split("=", 1)
            if key.strip() in {"sysauth", "sysauth_http", "sysauth_https"} and val.strip():
                return val.strip()
        return None

    def _luci_form_login(self) -> bool:
        fields = {
            "luci_username": self.user,
            "luci_password": self.password,
        }
        if self._luci_token:
            fields["token"] = self._luci_token
        body = urlencode(fields).encode("utf-8")
        status, headers, _raw = self._http_request(
            "POST",
            "/cgi-bin/luci",
            headers={
                **self._luci_headers(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=body,
            timeout=max(self.timeout, 20),
        )
        sid = self._cookie_sid(headers)
        if sid:
            self._luci_sid = sid
        if status in {200, 302, 303}:
            self._luci_form_authed = True
            return True
        return False

    def _parse_luci_body(self, raw: bytes) -> Any:
        text = raw.decode("utf-8", errors="replace")
        stripped = text.strip()
        if not stripped:
            return ""
        if stripped[:1] in "{[":
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        return text

    def luci_call(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        """GET a read-only LuCI dispatcher action. Other paths are rejected."""
        if not luci_action_allowed(action):
            raise CapabilityError(
                t("err.luci_denied", action=action),
                missing=("luci",),
            )
        self._maybe_refresh_session()
        if not self.session or self.session == NULL_SESSION:
            self._relogin()
        if not self._luci_token and self.session and self.session != NULL_SESSION:
            self._install_luci_token()
        wait = timeout if timeout is not None else max(self.timeout, 20)
        last_status = None
        tried_stok = False
        tried_form = False
        for _attempt in range(4):
            path = self._luci_path(action, params, stok=tried_stok)
            status, _headers, raw = self._http_request(
                "GET",
                path,
                headers=self._luci_headers(),
                timeout=wait,
            )
            last_status = status
            if status == 200:
                return self._parse_luci_body(raw)
            if status == 403:
                if not tried_stok and self._luci_token:
                    tried_stok = True
                    continue
                if not tried_form:
                    tried_form = True
                    if self._luci_form_login():
                        continue
                raise DeviceCommandError(t("err.luci_http", action=action, status=status), status=status)
            if status in {301, 302, 303, 307, 308}:
                raise DeviceCommandError(t("err.luci_http", action=action, status=status), status=status)
            raise DeviceCommandError(t("err.luci_http", action=action, status=status), status=status)
        raise DeviceCommandError(t("err.luci_http", action=action, status=last_status), status=last_status)

    def _call(self, sid, obj, method, params=None, timeout=None, batch: bool = False):
        envelope = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "call",
            "params": [sid, obj, method, params or {}],
        }
        raw = self._post([envelope] if batch else envelope, timeout=timeout)
        if not isinstance(raw, dict):
            raise DeviceCommandError(t("err.ubus_bad", raw=raw))
        if raw.get("error"):
            err = raw["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            text = str(msg)
            low = text.lower()
            if sid == NULL_SESSION:
                if _is_session_dead_message(text) or "object not found" in low or "access denied" in low:
                    raise DeviceCommandError(f"ubus {obj}.{method}: {text}")
                raise DeviceConnectionError(t("err.ubus_rpc", error=text))
            if _is_session_dead_message(text):
                raise _SessionExpired(text)
            if "access denied" in low:
                raise _AccessDenied(text)
            raise DeviceCommandError(f"ubus {obj}.{method}: {text}")
        result = raw.get("result")
        if not isinstance(result, list) or not result:
            raise DeviceCommandError(t("err.ubus_bad", raw=raw))
        status = result[0]
        data = result[1] if len(result) > 1 else {}
        return status, data

    @contextmanager
    def hold_session(self) -> Iterator[None]:
        """Keep one rpcd session for a set/delete/commit batch (per-session UCI overlay)."""
        self._maybe_refresh_session()
        prev = self._hold_session
        self._hold_session = True
        try:
            yield
        finally:
            self._hold_session = prev

    def call(self, obj: str, method: str, params: dict[str, Any] | None = None, timeout: int | None = None):
        if not self._hold_session:
            self._maybe_refresh_session()
        last_status = None
        for attempt in range(2):
            if not self.session or self.session == NULL_SESSION:
                if self._hold_session:
                    raise DeviceConnectionError(t("err.session_expired"))
                self._relogin()
            if not self.session or self.session == NULL_SESSION:
                raise DeviceConnectionError(t("err.not_logged_in"))
            stale = self.session
            try:
                status, data = self._call(stale, obj, method, params, timeout=timeout)
            except _SessionExpired:
                if self._hold_session or attempt:
                    raise DeviceConnectionError(t("err.session_expired"))
                self._relogin(stale)
                continue
            except _AccessDenied as e:
                if self._hold_session or attempt or not self._session_is_dead(stale):
                    raise DeviceCommandError(f"ubus {obj}.{method}: {e}") from e
                self._relogin(stale)
                continue
            last_status = status
            if status == UBUS_OK:
                self._last_ok = time.monotonic()
                return data
            raise DeviceCommandError(t("err.ubus_fail", obj=obj, method=method, status=status))
        raise DeviceCommandError(t("err.ubus_fail", obj=obj, method=method, status=last_status))

    def _session_is_dead(self, sid: str) -> bool:
        """用 system.board 区分「会话死了」和「这个方法没权限」。"""
        if not sid or sid == NULL_SESSION:
            return True
        try:
            status, _ = self._call(sid, "system", "board")
        except (_SessionExpired, _AccessDenied):
            return True
        except (DeviceCommandError, DeviceConnectionError):
            return False
        return status != UBUS_OK

    def _maybe_refresh_session(self) -> None:
        """会话快到期时提前重登。活跃 TUI 每 3s 已有请求，通常不会走到这里。"""
        if not self.session or not self._login_at:
            return
        age = time.monotonic() - self._login_at
        if age >= max(60.0, self._session_timeout * 0.75):
            try:
                self._relogin(self.session)
            except DeviceConnectionError:
                return

    def _relogin(self, stale: str | None = None) -> None:
        with self._auth_lock:
            if self.session and stale and self.session != stale:
                return
            if self.session and stale is None and self._login_at:
                age = time.monotonic() - self._login_at
                if age < max(60.0, self._session_timeout * 0.75):
                    return
            self._login()

    def keepalive(self) -> None:
        """本地判断是否需要续期；不额外打 system.board，避免缺对象时误伤建连。"""
        self._maybe_refresh_session()

    def list_services(self) -> dict[str, Any]:
        return self.call("rc", "list")

    def rc_init(self, name: str, action: str) -> Any:
        return self.call("rc", "init", {"name": name, "action": action})

    def close(self) -> None:
        self.session = None
        self._drop_conn()


# 旧名
HTTPClient = HTTPDevice
