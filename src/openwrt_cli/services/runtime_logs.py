"""Process-local event recording and retention for OpenWrt client diagnostics."""

from __future__ import annotations

import heapq
import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

DEFAULT_DAYS = 7
MAX_RETENTION_DAYS = 3650
DEFAULT_MIB = 50
SEGMENT_BYTES = 5 * 1024**2
KEEP_SEGMENTS = 4
_SESSION = re.compile(r"(?:tui|mcp|sampler)-[0-9a-f]{32}")
_SEGMENT = re.compile(r"\d{6,}\.(?:jsonl|active)")
_FIELDS = frozenset({
    "tool", "mode", "outcome", "error", "duration_ms", "request_id", "operation",
    "previous", "target", "sample_source", "log_interval", "metric_interval",
    "repeats", "removed_files", "removed_bytes", "days", "execution",
})
_MAX_RECORD_BYTES = 4096


def default_log_path() -> Path:
    return Path.home() / ".openwrt" / "logs"


def retention_settings(cfg: dict) -> tuple[int, int]:
    raw = cfg.get("runtime_logs")
    raw = raw if isinstance(raw, dict) else {}

    def positive(key: str, default: int, maximum: int) -> int:
        value = raw.get(key)
        return value if type(value) is int and 1 <= value <= maximum else default

    return positive("days", DEFAULT_DAYS, MAX_RETENTION_DAYS), positive("max_mib", DEFAULT_MIB, 10240)


@contextmanager
def _lock_file(path: Path, *, create: bool = False) -> Iterator[BinaryIO]:
    flags = os.O_RDWR | (os.O_CREAT | os.O_EXCL if create else 0)
    with os.fdopen(os.open(path, flags, 0o600), "r+b") as handle:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield handle


def _segments(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for directory in root.iterdir():
        if not _SESSION.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
            continue
        try:
            for path in directory.iterdir():
                if _SEGMENT.fullmatch(path.name) and not path.is_symlink() and path.is_file():
                    yield path
        except FileNotFoundError:
            continue


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except (FileNotFoundError, PermissionError):
        return False


def prune_runtime_logs(root: Path, policy: tuple[int, int], *, all_closed: bool = False) -> tuple[int, int]:
    """Delete expired/excess archived segments and recover segments from dead writers."""
    days, max_mib = policy
    cutoff = time.time() - days * 86_400
    entries = []
    for path in _segments(root):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))

    size = sum(length for _, length, _ in entries)
    removed_files = removed_bytes = 0
    touched: set[Path] = set()
    for stamp, length, path in sorted(entries):
        if not all_closed and stamp >= cutoff and size <= max_mib * 1024**2:
            continue
        if path.suffix == ".active":
            try:
                with _lock_file(path.parent / "writer.lock"):
                    removed = _unlink(path)
            except OSError:
                continue
        else:
            removed = _unlink(path)
        if removed:
            size -= length
            removed_files += 1
            removed_bytes += length
            touched.add(path.parent)
    for directory in touched:
        _remove_empty_session(directory)
    return removed_files, removed_bytes


def _remove_empty_session(directory: Path) -> None:
    lock = directory / "writer.lock"
    try:
        with _lock_file(lock):
            if any(path != lock for path in directory.iterdir()):
                return
        lock.unlink()
        directory.rmdir()
    except OSError:
        return


def runtime_log_snapshot(root: Path) -> tuple[tuple[Path, int, int], ...]:
    files = []
    for path in _segments(root):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        files.append((path, stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(files))


def query_runtime_logs(
    root: Path, *, profile: str | None = None, source: str | None = None,
    level: str | None = None, since_ms: int | None = None, until_ms: int | None = None,
    limit: int = 200, cancelled: Callable[[], bool] | None = None,
    snapshot: tuple[tuple[Path, int, int], ...] | None = None,
) -> tuple[list[dict], bool]:
    """Read the newest matching events, tolerating concurrent rotation or pruning."""
    files = runtime_log_snapshot(root) if snapshot is None else snapshot
    results: list[tuple[int, int, dict]] = []
    order = 0
    for path, _, _ in files:
        try:
            with path.open("rb") as handle:
                for line in handle:
                    if cancelled is not None and cancelled():
                        return [], False
                    if len(line) > _MAX_RECORD_BYTES or not line.endswith(b"\n"):
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    stamp = row.get("time_ms")
                    if not isinstance(stamp, int) or (since_ms is not None and stamp < since_ms) or (
                        until_ms is not None and stamp > until_ms
                    ):
                        continue
                    if any(value is not None and row.get(key) != value for key, value in (
                        ("profile", profile), ("source", source), ("level", level),
                    )):
                        continue
                    order += 1
                    entry = (stamp, order, row)
                    if len(results) <= limit:
                        heapq.heappush(results, entry)
                    else:
                        heapq.heappushpop(results, entry)
        except (FileNotFoundError, PermissionError):
            continue
    return [row for _, _, row in sorted(results, reverse=True)[:limit]], len(results) > limit


class RuntimeLog:
    def __init__(
        self, process: str, *, root: Path | None = None,
        policy: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.root = root if root is not None else default_log_path()
        self.process = process
        self.session = f"{process}-{uuid.uuid4().hex}"
        self._policy = policy or (lambda: (DEFAULT_DAYS, DEFAULT_MIB))
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=1024)
        self._closing = threading.Event()
        self._state_lock = threading.Lock()
        self._dropped = 0
        self.problem = ""
        self._thread = threading.Thread(target=self._run, name=f"openwrt-{process}-log", daemon=True)
        self._thread.start()

    def emit(self, source: str, event: str, *, level: str = "INFO", profile: str | None = None, **fields) -> None:
        entry = {
            "time_ms": time.time_ns() // 1_000_000, "source": source, "level": level,
            "event": event, "session": self.session, "pid": os.getpid(),
        }
        if profile:
            entry["profile"] = profile[:160]
        entry.update({
            key: (value[:160] if isinstance(value, str) else value)
            for key, value in fields.items()
            if key in _FIELDS and type(value) in (str, int, float, bool)
        })
        with self._state_lock:
            if self._closing.is_set():
                return
            try:
                self._queue.put_nowait(entry)
            except queue.Full:
                self._dropped += 1

    @property
    def dropped(self) -> int:
        with self._state_lock:
            return self._dropped

    def close(self, timeout: float = 2) -> None:
        with self._state_lock:
            self._closing.set()
        self._thread.join(timeout)

    def _problem(self, exc: Exception) -> None:
        issue = type(exc).__name__
        if issue != self.problem and self.process == "mcp":
            print(f"openwrt-mcp runtime log: {issue}", file=sys.stderr)
        self.problem = issue

    def _run(self) -> None:
        folder = self.root / self.session
        try:
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            folder.mkdir(mode=0o700)
            with _lock_file(folder / "writer.lock", create=True):
                self._write(folder)
        except OSError as exc:
            self._problem(exc)
        finally:
            _remove_empty_session(folder)

    def _write(self, folder: Path) -> None:
        index = size = 0
        handle: BinaryIO | None = None
        path: Path | None = None
        prune_at = 0.0
        reported_drops = 0

        def archive() -> None:
            nonlocal handle
            if handle is not None:
                handle.close()
                handle = None
                path.replace(path.with_suffix(".jsonl"))

        try:
            while not self._closing.is_set() or not self._queue.empty():
                if time.monotonic() >= prune_at:
                    try:
                        prune_runtime_logs(self.root, self._policy())
                    except Exception as exc:
                        self._problem(exc)
                    else:
                        self.problem = ""
                    prune_at = time.monotonic() + 60
                try:
                    entry = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                pending = [entry]
                dropped = self.dropped
                if dropped != reported_drops:
                    if self.process == "mcp":
                        print(f"openwrt-mcp runtime log: {dropped - reported_drops} events dropped", file=sys.stderr)
                    pending.insert(0, {key: entry[key] for key in ("time_ms", "session", "pid")} | {
                        "source": self.process, "level": "WARNING", "event": "runtime.queue_dropped",
                        "repeats": dropped - reported_drops,
                    })
                    reported_drops = dropped
                for row in pending:
                    encoded = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8", "replace")
                    if handle is None or size + len(encoded) > SEGMENT_BYTES:
                        archive()
                        index += 1
                        path = folder / f"{index:06}.active"
                        handle = os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb", buffering=0)
                        size = 0
                        if index > KEEP_SEGMENTS:
                            _unlink(folder / f"{index - KEEP_SEGMENTS:06}.jsonl")
                    if handle.write(encoded) != len(encoded):
                        raise OSError("incomplete runtime log write")
                    size += len(encoded)
        finally:
            archive()
