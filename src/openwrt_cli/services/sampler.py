"""Cancellable sampling session with a single local SQLite writer."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from typing import Any

from openwrt_cli.core.connection import open_connection
from openwrt_cli.core.device import DeviceClient
from openwrt_cli.core.errors import DeviceCommandError, DeviceConnectionError
from openwrt_cli.core.sampling_config import sampling_settings
from openwrt_cli.services.bandix import BandixService, norm_mac
from openwrt_cli.services.monitor import MonitorService
from openwrt_cli.services.passwall2 import PassWall2Service, acl_enable, current_node, parse_acl
from openwrt_cli.services.sample_store import SampleStore
from openwrt_cli.services.runtime_logs import RuntimeLog
from openwrt_cli.services.system import SystemService, _entry_datetime


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _connection(config: dict) -> dict:
    return {
        key: config[key]
        for key in ("host", "port", "scheme", "transport", "user", "password", "identity_file", "verify_ssl", "http_port")
        if key in config
    }


def router_key(config: dict) -> str:
    target = [config.get(key) for key in ("host", "port", "scheme", "transport")]
    return hashlib.sha256(json.dumps(target).encode()).hexdigest()


def acl_catalog(values: dict) -> list[dict]:
    rows = parse_acl(values)
    for row in rows:
        row["sampling_available"] = acl_enable(values) is not False and row["enabled"] and (
            str(row["log"]).strip().lower() in {"1", "true", "on", "yes"}
        )
    return rows


def enabled_sources(settings: dict) -> bool:
    logs = settings["logs"]
    passwall = logs["passwall2"]
    return settings["enabled"] and (
        settings["metrics"]["enabled"] or logs["enabled"] and (
            logs["system"] or logs["kernel"]
            or passwall["enabled"] and (passwall["runtime"] or passwall["acls"]["enabled"])
        )
    )


def bandix_history(device: DeviceClient, mac: str | None = None) -> list[tuple[int, dict]]:
    points = (BandixService(device).metrics(mac) or {}).get("points", [])
    return [
        (point["ts"] if point["ts"] >= 10**11 else point["ts"] * 1000, point)
        for point in points if point["ts"] > 0
    ]


def _entries(read: Callable) -> list[dict]:
    result = read()
    if not result.ok:
        raise RuntimeError(result.message or "unavailable")
    rows = []
    for entry in result.data["entries"]:
        stamp = _entry_datetime(entry)
        rows.append(dict(entry, time=int(stamp.timestamp() * 1000)) if stamp is not None else entry)
    return rows


def _log_jobs(device: DeviceClient, log: dict) -> Iterator[tuple[str, Callable, str]]:
    system = SystemService(device)
    if log["system"]:
        yield "system", lambda: _entries(lambda: system.logs(tail=20_000)), ""
    if log["kernel"]:
        def kernel():
            rows = _entries(lambda: system.logs(kernel=True, tail=20_000))
            try:
                boot = device.fs.read("/proc/sys/kernel/random/boot_id").strip()
            except DeviceCommandError:
                return rows
            return [dict(row, boot_id=boot) for row in rows]
        yield "kernel", kernel, ""
    options = log["passwall2"]
    if not options["enabled"]:
        return
    passwall = PassWall2Service(device)
    try:
        values = device.uci.show("passwall2")
    except DeviceCommandError as exc:
        yield "passwall2.discovery", lambda error=exc: _raise(error), ""
        return
    if not values:
        return
    if options["runtime"]:
        yield "passwall2.runtime", lambda: _entries(lambda: passwall.logs(tail=20_000)), ""
    if not options["acls"]["enabled"]:
        return
    selected = options["acls"]["sections"]
    default_node, _ = current_node(values)
    eligible = acl_catalog(values)
    unavailable = set(selected) - {row["id"] for row in eligible if row["sampling_available"]}
    for section in sorted(unavailable):
        yield f"passwall2.acl.{section}", lambda: _raise(LookupError("ACL logging unavailable")), ""
    for row in eligible:
        section = row["id"]
        if not row["sampling_available"] or selected and section not in selected:
            continue
        node = row["node"] or default_node
        cfg = values.get(node, {})
        detail = json.dumps({"node": node, "address": cfg.get("address"), "port": cfg.get("port"),
                             "sources": row["sources"]}, ensure_ascii=False)
        yield f"passwall2.acl.{section}", lambda sid=section: _entries(
            lambda: passwall.acl_log(sid, tail=20_000, verify=False)
        ), detail


def _raise(error: Exception):
    raise error


@dataclass(frozen=True)
class SamplingStatus:
    state: str = "stopped"
    last_success: int | None = None
    gaps: int = 0
    error: str = ""


class Sampler:
    def __init__(self, store: SampleStore | None = None, connect: Callable = open_connection, *,
                 journal: RuntimeLog | None = None,
                 on_event: Callable[[str, str, dict], None] | None = None,
                 show_logs: bool = False):
        self.store = store or SampleStore()
        self._connect = connect
        self._journal = journal
        self._on_event = on_event
        self._show_logs = show_logs
        self._profile: str | None = None
        self._failures: dict[str, tuple[str, int, float]] = {}
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._requested = False
        self._target: tuple[dict, str, dict] | None = None
        self._status = SamplingStatus()

    @property
    def status(self) -> SamplingStatus:
        with self._condition:
            return self._status

    def configure(self, config: dict) -> None:
        profile = config.get("active")
        target = (_connection(config), router_key(config), sampling_settings(config, profile))
        with self._condition:
            if self._target == target and self._profile == profile:
                return
            previous = self._target
            if self._profile != profile or previous and previous[:2] != target[:2]:
                self._report_failures()
            self._profile = profile
            self._target = target
            if previous and previous[:2] != target[:2]:
                self._status = SamplingStatus(self._status.state)
            if not enabled_sources(target[2]):
                self._requested = False
            if previous is not None:
                self._log_configuration()
            self._condition.notify_all()

    def start(self) -> bool:
        with self._condition:
            if not self._target or not enabled_sources(self._target[2]):
                return False
            if self._requested:
                return True
            if self._thread and self._thread.is_alive():
                return False
            self._requested = True
            self._status = SamplingStatus("starting")
            self._log("sampler.starting")
            self._log_configuration()
            self._thread = threading.Thread(target=self._run, name="openwrt-sampler", daemon=True)
            self._thread.start()
            return True

    def stop(self, *, wait: float = 0) -> None:
        with self._condition:
            if self._requested:
                self._log("sampler.stop_requested")
            self._requested = False
            thread = self._thread
            if thread and thread.is_alive() and self._status.state != "error":
                self._status = replace(self._status, state="stopping")
            self._condition.notify_all()
        if thread and wait:
            thread.join(wait)

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _run(self) -> None:
        stage = "sqlite_open"
        try:
            with self.store.writer() as db:
                self._log("sampler.started")
                stage = "collect"
                self._collect(db)
        except Exception as exc:
            with self._condition:
                operation = "sqlite_write" if stage == "collect" and isinstance(exc, sqlite3.Error) else stage
                self._log("sampler.failed", level="ERROR", error=type(exc).__name__, operation=operation)
                self._status = replace(self._status, state="error", error=type(exc).__name__)
        finally:
            with self._condition:
                self._report_failures()
                self._requested = False
                if self._status.state != "error":
                    self._status = replace(self._status, state="stopped")
                self._log("sampler.stopped", level="ERROR" if self._status.state == "error" else "INFO",
                          outcome=self._status.state)

    def _log(self, event: str, *, level: str = "INFO", **fields) -> None:
        if self._journal is not None:
            self._journal.emit("sampler", event, profile=self._profile, level=level, **fields)
        if self._on_event is not None:
            self._on_event(event, level, fields)

    def _log_configuration(self) -> None:
        settings = self._target[2]
        logs, metrics = settings["logs"], settings["metrics"]
        passwall = logs["passwall2"]
        sources = []
        if settings["enabled"] and logs["enabled"]:
            sources.extend(source for source in ("system", "kernel") if logs[source])
            if passwall["enabled"]:
                if passwall["runtime"]:
                    sources.append("passwall2.runtime")
                if passwall["acls"]["enabled"]:
                    sources.append("passwall2.acl")
        if settings["enabled"] and metrics["enabled"]:
            sources.append("openwrt.metrics")
            if metrics["bandix"]["enabled"]:
                sources.append("bandix")
        self._log("sampler.configured", sample_source=", ".join(sources),
                  log_interval=logs["interval"], metric_interval=metrics["interval"])

    def _report_failures(self) -> None:
        for source, (error, repeats, _) in self._failures.items():
            if repeats:
                self._log("sampler.read_repeated", level="WARNING", error=error, sample_source=source, repeats=repeats)
        self._failures.clear()

    def _read_failed(self, source: str, error: str) -> None:
        previous, repeats, last = self._failures.get(source, ("", 0, 0))
        now = time.monotonic()
        if previous != error:
            if repeats:
                self._log("sampler.read_repeated", level="WARNING", error=previous, sample_source=source, repeats=repeats)
            self._log("sampler.read_failed", level="WARNING", error=error, sample_source=source)
            repeats, last = 0, now
        else:
            repeats += 1
            if now - last >= 60:
                self._log("sampler.read_repeated", level="WARNING", error=error, sample_source=source, repeats=repeats)
                repeats, last = 0, now
        self._failures[source] = (error, repeats, last)

    def _recovered(self, source: str) -> None:
        failure = self._failures.pop(source, None)
        if failure is not None:
            error, repeats, _ = failure
            self._log("sampler.recovered", sample_source=source, error=error, repeats=repeats)

    def _collect(self, db: sqlite3.Connection) -> None:
        device = None
        active = None
        due = {"logs": 0.0, "metrics": 0.0}
        backfilled = False
        try:
            while True:
                with self._condition:
                    if not self._requested:
                        return
                    target = self._target
                    connection, router, settings = target
                    if target != active:
                        if not active or target[:2] != active[:2]:
                            backfilled = False
                        due = {"logs": 0.0, "metrics": 0.0}
                    tick = time.monotonic()
                    ready = [key for key in due if settings[key]["enabled"] and due[key] <= tick]
                    if not ready:
                        next_due = min(due[key] for key in due if settings[key]["enabled"])
                        self._condition.wait(min(threading.TIMEOUT_MAX, max(0, next_due - tick)))
                        continue
                    for key in ready:
                        due[key] = tick + settings[key]["interval"]
                if active and active[:2] != target[:2] and device is not None:
                    device.close()
                    device = None
                active = target
                if device is None:
                    try:
                        device = self._connect(connection)
                    except Exception as exc:
                        self._write(db, target, "connection", error=exc)
                        continue
                with self._condition:
                    if self._requested and self._target == target:
                        self._recovered("connection")
                        self._status = replace(self._status, state="running", error="")
                try:
                    if "logs" in ready:
                        for source, read, detail in _log_jobs(device, settings["logs"]):
                            if not self._current(target):
                                break
                            self._sample(db, target, source, read, detail=detail)
                    if "metrics" in ready and self._current(target):
                        at = now_ms()
                        interval = settings["metrics"]["interval"]
                        self._sample(db, target, "openwrt.metrics",
                                     lambda: self._openwrt_metrics(device, at), interval=interval)
                        if settings["metrics"]["bandix"]["enabled"] and self._current(target):
                            self._sample(db, target, "bandix.devices", lambda: self._bandix_devices(device, at),
                                         interval=interval)
                            if not backfilled and self._current(target):
                                backfilled = self._sample(db, target, "bandix.history", lambda: [
                                    ("all", stamp, point) for stamp, point in bandix_history(device)
                                ], interval=interval, require_rows=True)
                except DeviceConnectionError as exc:
                    self._write(db, target, "connection", error=exc)
                    device.close()
                    device = None
        finally:
            if device is not None:
                device.close()

    def _current(self, target: tuple) -> bool:
        with self._condition:
            return self._requested and self._target == target

    def _sample(
        self, db: sqlite3.Connection, target: tuple, source: str, read: Callable, *,
        interval: float | None = None, detail: str = "", require_rows: bool = False,
    ) -> bool:
        try:
            rows = read()
            if require_rows and not rows:
                return False
        except Exception as exc:
            if isinstance(exc, DeviceConnectionError):
                raise
            self._write(db, target, source, error=exc)
            return False
        return self._write(db, target, source, rows, interval=interval, detail=detail)

    def _write(
        self, db: sqlite3.Connection, target: tuple, source: str, rows: list | None = None, *,
        interval: float | None = None, detail: str = "", error: Exception | None = None,
    ) -> bool:
        with self._condition:
            if not self._requested or self._target != target:
                return False
            _, router, _ = target
            at = now_ms()
            if error is not None:
                self._read_failed(source, type(error).__name__)
                self.store.record_error(db, router, source, at, type(error).__name__)
                self._status = replace(self._status, state="running", error=f"{source}: {type(error).__name__}")
                return False
            if interval is not None:
                batch = self.store.record_metrics(db, router, source, at, rows, interval=interval)
            else:
                batch = self.store.record_logs(db, router, source, at, rows, detail=detail)
            self._recovered(source)
            gap = batch.status.startswith("gap_")
            self._status = replace(self._status, state="running", last_success=at,
                                   gaps=self._status.gaps + int(gap))
            if gap:
                self._log("sampler.gap", level="WARNING", sample_source=source, outcome=batch.status)
            if self._on_event is not None:
                entries = ()
                if self._show_logs and interval is None and batch.added and batch.status != "initial":
                    entries = rows[-batch.added:]
                self._on_event("sampler.batch", "INFO", {
                    "sample_source": source, "outcome": batch.status, "added": batch.added, "entries": entries,
                })
            return True

    @staticmethod
    def _openwrt_metrics(device: DeviceClient, at: int) -> list[tuple[str, int, dict]]:
        result = MonitorService(device).status()
        if not result.ok:
            raise RuntimeError("Router metrics unavailable")
        return [("router", at, {**result.data, "warnings": result.warnings})]

    @staticmethod
    def _bandix_devices(device: DeviceClient, at: int) -> list[tuple[str, int, dict[str, Any]]]:
        data = BandixService(device).overlay()
        if data is None:
            raise RuntimeError("Bandix unavailable")
        devices = {norm_mac(row["mac"]): row for row in data["devices"] if row["mac"]}
        connections = data["connections"]
        rows = [(mac, at, {**devices.get(mac, {}), **connections.get(mac, {}), "mac": mac})
                for mac in sorted(devices.keys() | connections.keys())]
        totals = {}
        if devices:
            totals.update({key: sum(float(row.get(key) or 0) for row in devices.values())
                           for key in ("wide_rx_rate", "wide_tx_rate", "local_rx_rate", "local_tx_rate")})
        if connections:
            totals.update({key: sum(int(row.get(key) or 0) for row in connections.values())
                           for key in ("tcp_connections", "udp_connections", "total_connections")})
        if totals:
            rows.append(("all", at, totals))
        return rows
