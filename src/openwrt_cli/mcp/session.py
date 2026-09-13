"""Shared DeviceClient + yaml reload. No FastMCP dependency."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from openwrt_cli.core.config import ConfigManager, McpModeError, mcp_mode
from openwrt_cli.core.connection import open_connection
from openwrt_cli.core.device import DeviceClient
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError, DeviceConnectionError
from openwrt_cli.i18n import t
from openwrt_cli.mcp.guard import check_call
from openwrt_cli.mcp.payload import cap_payload, to_payload
from openwrt_cli.services.result import CommandResult


class McpSession:
    def __init__(self, config_path: str | None = None):
        raw = config_path or os.environ.get("OPENWRT_CONFIG")
        self._mgr = ConfigManager(raw)
        self._client: DeviceClient | None = None
        self._key: tuple | None = None
        self._lock = threading.Lock()

    @property
    def config_path(self) -> str:
        return self._mgr.config_path

    def load_cfg(self) -> dict:
        return self._mgr.load()

    def current_mode(self) -> str:
        return mcp_mode(self.load_cfg())

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
                self._key = None

    def invoke(self, logical: str, fn: Callable[[DeviceClient], CommandResult]) -> dict[str, Any]:
        cfg = self.load_cfg()
        denied = check_call(logical, cfg)
        if denied is not None:
            return cap_payload(to_payload(denied))
        if not cfg.get("host"):
            fail = CommandResult.fail(t("msg.need_host"), data={"error": "need_host"})
            return cap_payload(to_payload(fail))
        try:
            mcp_mode(cfg)
        except McpModeError as e:
            fail = CommandResult.fail(
                t("err.mcp_mode", mode=e.mode),
                data={"error": "mcp_mode_invalid", "mode": e.mode},
            )
            return cap_payload(to_payload(fail))
        try:
            with self._lock:
                client = self._client_locked(cfg)
                result = fn(client)
        except DeviceConnectionError as e:
            result = CommandResult.fail(t("msg.connect_fail", error=e), data={"error": "connect"})
        except CapabilityError as e:
            result = CommandResult.fail(str(e), data={"error": "capability"})
        except DeviceCommandError as e:
            result = CommandResult.fail(t("msg.exec_fail", error=e), data={"error": "exec"})
        except Exception as e:  # noqa: BLE001 — tool boundary
            result = CommandResult.fail(f"{type(e).__name__}: {e}", data={"error": "exec"})
        if not result.transport and self._client is not None:
            result.transport = self._client.transport
        return cap_payload(to_payload(result))

    def _client_locked(self, cfg: dict) -> DeviceClient:
        key = (
            cfg.get("host"),
            cfg.get("port"),
            cfg.get("transport"),
            cfg.get("user"),
            cfg.get("identity_file"),
            cfg.get("scheme"),
        )
        if self._client is None or self._key != key:
            if self._client is not None:
                self._client.close()
            self._client = open_connection(cfg)
            self._key = key
        return self._client
