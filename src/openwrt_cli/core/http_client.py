"""OpenWrt ubus HTTP JSON-RPC 客户端（LuCI / uhttpd-mod-ubus）。"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
from typing import Any, Literal

from openwrt_cli.core.channels.uci import option_from_values, resolve_section
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
        values = self.show(config)
        section = resolve_section(values, section)
        self._device.call("uci", "set", {
            "config": config,
            "section": section,
            "values": {option: value},
        })

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

    def call(self, obj: str, method: str, params: dict[str, Any] | None = None, timeout: int | None = None):
        self._maybe_refresh_session()
        last_status = None
        for attempt in range(2):
            if not self.session or self.session == NULL_SESSION:
                self._relogin()
            if not self.session or self.session == NULL_SESSION:
                raise DeviceConnectionError(t("err.not_logged_in"))
            stale = self.session
            try:
                status, data = self._call(stale, obj, method, params, timeout=timeout)
            except _SessionExpired:
                if attempt == 0:
                    self._relogin(stale)
                    continue
                raise DeviceConnectionError(t("err.session_expired"))
            except _AccessDenied as e:
                if attempt == 0 and self._session_is_dead(stale):
                    self._relogin(stale)
                    continue
                raise DeviceCommandError(f"ubus {obj}.{method}: {e}") from e
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
