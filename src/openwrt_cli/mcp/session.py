"""Shared DeviceClient + yaml reload. No FastMCP dependency."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from openwrt_cli.core.config import ConfigManager, McpModeError, ProfileError, connection_for, mcp_mode
from openwrt_cli.core.connection import open_connection
from openwrt_cli.core.device import DeviceClient
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError, DeviceConnectionError
from openwrt_cli.i18n import t
from openwrt_cli.mcp.guard import check_call
from openwrt_cli.mcp.payload import cap_payload, to_payload
from openwrt_cli.services.result import CommandResult


def _connection_key(cfg: dict) -> tuple:
    return (
        cfg.get("host"),
        cfg.get("port"),
        cfg.get("transport"),
        cfg.get("user"),
        cfg.get("identity_file"),
        cfg.get("scheme"),
    )


def _with_profile(payload: dict[str, Any], name: str) -> dict[str, Any]:
    if name:
        payload["profile"] = name
    return payload


class McpSession:
    def __init__(self, config_path: str | None = None):
        raw = config_path or os.environ.get("OPENWRT_CONFIG")
        self._mgr = ConfigManager(raw)
        self._clients: dict[tuple, DeviceClient] = {}
        self._locks: dict[tuple, threading.Lock] = {}
        self._pool_lock = threading.Lock()

    @property
    def config_path(self) -> str:
        return self._mgr.config_path

    def load_cfg(self) -> dict:
        return self._mgr.load()

    def current_mode(self) -> str:
        return mcp_mode(self.load_cfg())

    def close(self) -> None:
        with self._pool_lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._locks.clear()
        for client in clients:
            client.close()

    def invoke(
        self,
        logical: str,
        fn: Callable[[DeviceClient], CommandResult],
        *,
        profile: str | None = None,
    ) -> dict[str, Any]:
        cfg = self.load_cfg()
        requested = (profile or "").strip()
        try:
            if requested:
                trial = connection_for(cfg, requested)
                resolved = requested
            else:
                trial = cfg
                resolved = str(cfg.get("active") or "")
        except ProfileError as exc:
            fail = CommandResult.fail(
                t("err.profile_missing", name=exc.name or requested),
                data={"error": "profile_missing", "name": exc.name or requested},
            )
            return _with_profile(cap_payload(to_payload(fail)), requested)

        def finish(payload: dict[str, Any]) -> dict[str, Any]:
            return _with_profile(payload, resolved)

        denied = check_call(logical, trial)
        if denied is not None:
            return finish(cap_payload(to_payload(denied)))
        if not trial.get("host"):
            fail = CommandResult.fail(t("msg.need_host"), data={"error": "need_host"})
            return finish(cap_payload(to_payload(fail)))
        try:
            mcp_mode(trial)
        except McpModeError as e:
            fail = CommandResult.fail(
                t("err.mcp_mode", mode=e.mode),
                data={"error": "mcp_mode_invalid", "mode": e.mode},
            )
            return finish(cap_payload(to_payload(fail)))
        key = _connection_key(trial)
        lock = self._lock_for(key)
        client: DeviceClient | None = None
        try:
            with lock:
                client = self._client_for(key, trial)
                result = fn(client)
        except DeviceConnectionError as e:
            result = CommandResult.fail(t("msg.connect_fail", error=e), data={"error": "connect"})
        except CapabilityError as e:
            result = CommandResult.fail(str(e), data={"error": "capability"})
        except DeviceCommandError as e:
            result = CommandResult.fail(t("msg.exec_fail", error=e), data={"error": "exec"})
        except Exception as e:  # noqa: BLE001 — tool boundary
            result = CommandResult.fail(f"{type(e).__name__}: {e}", data={"error": "exec"})
        if not result.transport and client is not None:
            result.transport = client.transport
        return finish(cap_payload(to_payload(result)))

    def _lock_for(self, key: tuple) -> threading.Lock:
        with self._pool_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _client_for(self, key: tuple, cfg: dict) -> DeviceClient:
        with self._pool_lock:
            existing = self._clients.get(key)
        if existing is not None:
            return existing
        client = open_connection(cfg)
        with self._pool_lock:
            current = self._clients.get(key)
            if current is not None:
                client.close()
                return current
            self._clients[key] = client
        return client
