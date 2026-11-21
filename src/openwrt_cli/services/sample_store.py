"""SQLite history and checkpoints for OpenWrt sampling."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sample_checkpoints (
    router TEXT NOT NULL, source TEXT NOT NULL, snapshot TEXT NOT NULL,
    PRIMARY KEY (router, source)
);
CREATE TABLE IF NOT EXISTS sample_batches (
    id INTEGER PRIMARY KEY, router TEXT NOT NULL, source TEXT NOT NULL,
    collected_ms INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sample_batches_history
    ON sample_batches (router, source, collected_ms);
CREATE TABLE IF NOT EXISTS sample_logs (
    batch_id INTEGER NOT NULL REFERENCES sample_batches(id), position INTEGER NOT NULL,
    event_ms INTEGER, entry TEXT NOT NULL,
    PRIMARY KEY (batch_id, position)
);
"""
METRIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS sample_metrics (
    router TEXT NOT NULL, source TEXT NOT NULL, subject TEXT NOT NULL,
    event_ms INTEGER NOT NULL, data TEXT NOT NULL, batch_id INTEGER NOT NULL,
    PRIMARY KEY (router, source, subject, event_ms, batch_id)
);
CREATE INDEX IF NOT EXISTS sample_metrics_history
    ON sample_metrics (router, source, event_ms);
"""

LOG_SOURCES = ("system", "kernel", "passwall2.runtime", "passwall2.acl")
METRIC_SOURCES = ("openwrt.metrics", "bandix.devices", "bandix.history")


class SampleWriterBusy(BlockingIOError):
    """Another sampler holds the database writer lock."""


@dataclass(frozen=True)
class SampleBatch:
    status: str
    added: int


def default_data_path() -> Path:
    return Path.home() / ".openwrt" / "data.db"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _overlap(previous: list[str], current: list[str]) -> tuple[int, bool]:
    """Shortest nonempty shared suffix/prefix and whether larger matches also fit."""
    if not previous or not current:
        return 0, False
    prefix = [0] * len(current)
    matched = 0
    for i in range(1, len(current)):
        while matched and current[matched] != current[i]:
            matched = prefix[matched - 1]
        if current[matched] == current[i]:
            matched += 1
        prefix[i] = matched
    matched = 0
    for item in previous:
        while matched and (matched == len(current) or current[matched] != item):
            matched = prefix[matched - 1]
        if current[matched] == item:
            matched += 1
    ambiguous = bool(matched and prefix[matched - 1])
    while matched and prefix[matched - 1]:
        matched = prefix[matched - 1]
    return matched, ambiguous


class SampleStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_data_path()

    @contextmanager
    def writer(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        flags = os.O_CREAT | os.O_RDWR
        with os.fdopen(os.open(lock_path, flags, 0o600), "r+b") as lock:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                raise SampleWriterBusy(lock_path) from exc
            os.close(os.open(self.path, flags, 0o600))
            with closing(sqlite3.connect(self.path, timeout=1)) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript(SCHEMA + METRIC_SCHEMA)
                if not self._versioned_metrics(db):
                    # Legacy metrics are visible to every snapshot, including one opened before migration.
                    db.executescript(
                        """BEGIN IMMEDIATE;
                        ALTER TABLE sample_metrics RENAME TO legacy_sample_metrics;
                        DROP INDEX sample_metrics_history;
                        """ + METRIC_SCHEMA + """
                        INSERT INTO sample_metrics SELECT *, 0 FROM legacy_sample_metrics;
                        DROP TABLE legacy_sample_metrics;
                        COMMIT;"""
                    )
                yield db

    @staticmethod
    def _versioned_metrics(db: sqlite3.Connection) -> bool:
        return any(row[1] == "batch_id" for row in db.execute("PRAGMA table_info(sample_metrics)"))

    @staticmethod
    def _metric_snapshot_filter(db: sqlite3.Connection, snapshot: int) -> tuple[str, tuple[int, ...]]:
        if not SampleStore._versioned_metrics(db):
            return "", ()
        return """AND m.batch_id <= ? AND NOT EXISTS (
            SELECT 1 FROM sample_metrics newer
            WHERE newer.router=m.router AND newer.source=m.source
              AND newer.subject=m.subject AND newer.event_ms=m.event_ms
              AND newer.batch_id > m.batch_id AND newer.batch_id <= ?
        )""", (snapshot, snapshot)

    def size(self) -> int:
        size = 0
        for path in (self.path, Path(f"{self.path}-wal")):
            try:
                size += path.stat().st_size
            except FileNotFoundError:
                pass
        return size

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
            yield db

    def record_logs(
        self, db: sqlite3.Connection, router: str, source: str, at: int, rows: list[dict], *, detail: str = "",
    ) -> SampleBatch:
        # Use source IDs/timestamps where available and retain position within a snapshot.
        encoded = [_json(row) for row in rows]
        snapshot = [hashlib.blake2b(item.encode(), digest_size=16).hexdigest() for item in encoded]
        with db:
            cursor = db.execute(
                "SELECT snapshot FROM sample_checkpoints WHERE router=? AND source=?", (router, source)
            ).fetchone()
            previous = json.loads(cursor[0]) if cursor else []
            if snapshot == previous and cursor:
                self._batch(db, router, source, at, "continuous", detail)
                return SampleBatch("continuous", 0)
            overlap, ambiguous = _overlap(previous, snapshot)
            if not cursor:
                status = "initial"
            elif not snapshot:
                status = "gap_empty"
            elif not previous:
                status = "resumed"
            elif ambiguous:
                status = "gap_ambiguous"
            elif not overlap:
                status = "gap_rollover"
            else:
                status = "continuous"
            batch = self._batch(db, router, source, at, status, detail)
            db.executemany(
                "INSERT INTO sample_logs VALUES (?, ?, ?, ?)",
                ((batch, i, rows[i].get("time"), encoded[i]) for i in range(overlap, len(rows))),
            )
            db.execute(
                "INSERT INTO sample_checkpoints VALUES (?, ?, ?) "
                "ON CONFLICT(router, source) DO UPDATE SET snapshot=excluded.snapshot",
                (router, source, _json(snapshot)),
            )
        return SampleBatch(status, len(rows) - overlap)

    def record_metrics(
        self, db: sqlite3.Connection, router: str, source: str, at: int,
        rows: list[tuple[str, int, dict]], *, interval: float | None = None,
    ) -> SampleBatch:
        with db:
            last = db.execute(
                "SELECT max(collected_ms) FROM sample_batches WHERE router=? AND source=? AND status!='error'",
                (router, source),
            ).fetchone()[0]
            status = "gap_interval" if last is not None and interval and at - last > interval * 1500 else "continuous"
            batch = self._batch(db, router, source, at, status)
            inserted = db.executemany(
                """INSERT OR REPLACE INTO sample_metrics
                   SELECT :router, :source, :subject, :event_ms, :data, :batch
                   WHERE :data IS NOT (
                       SELECT data FROM sample_metrics
                       WHERE router=:router AND source=:source AND subject=:subject AND event_ms=:event_ms
                       ORDER BY batch_id DESC LIMIT 1
                   )""",
                (dict(router=router, source=source, subject=subject, event_ms=event_ms, data=_json(data), batch=batch)
                 for subject, event_ms, data in rows),
            )
        return SampleBatch(status, inserted.rowcount)

    def record_error(self, db: sqlite3.Connection, router: str, source: str, at: int, error: str) -> None:
        with db:
            self._batch(db, router, source, at, "error", error)

    @staticmethod
    def _batch(db: sqlite3.Connection, router: str, source: str, at: int, status: str, detail: str = "") -> int:
        return db.execute(
            "INSERT INTO sample_batches (router, source, collected_ms, status, detail) VALUES (?, ?, ?, ?, ?)",
            (router, source, at, status, detail),
        ).lastrowid

    def history(self, router: str, since_ms: int) -> list[tuple[str, int, int, int, int]]:
        if not self.path.is_file():
            return []
        with self._reader() as db:
            return db.execute(
                """SELECT source, min(collected_ms), max(collected_ms), count(*),
                          sum(status LIKE 'gap_%' OR status='error')
                   FROM sample_batches WHERE router=? AND collected_ms>=?
                   GROUP BY source ORDER BY source""", (router, since_ms),
            ).fetchall()

    def metrics(self, router: str, source: str, subject: str, since_ms: int) -> list[tuple[int, dict]]:
        if not self.path.is_file():
            return []
        with self._reader() as db:
            db.execute("BEGIN")
            latest, params = self._metric_snapshot_filter(db, 2**63 - 1)
            rows = db.execute(
                f"""SELECT event_ms, data FROM sample_metrics m WHERE router=? AND source=? AND subject=?
                    AND event_ms>=? {latest} ORDER BY event_ms""", (router, source, subject, since_ms, *params)
            )
            return [(at, json.loads(data)) for at, data in rows]

    def logs(self, router: str, source: str, since_ms: int, limit: int = 200) -> list[tuple[int, dict, str]]:
        if not self.path.is_file():
            return []
        with self._reader() as db:
            rows = db.execute(
                """SELECT coalesce(l.event_ms, b.collected_ms) AS stamp, l.entry, b.detail
                   FROM sample_batches b JOIN sample_logs l ON l.batch_id=b.id
                   WHERE b.router=? AND b.source=? AND stamp>=?
                   ORDER BY stamp DESC, b.id DESC, l.position DESC LIMIT ?""",
                (router, source, since_ms, limit),
            ).fetchall()
            return [(at, json.loads(entry), detail) for at, entry, detail in reversed(rows)]

    def query(
        self, router: str, source: str, since_ms: int, until_ms: int, *,
        limit: int, offset: int, subject: str | None, snapshot: int | None = None,
    ) -> dict:
        data = {"database": "missing", "records": [], "coverage": [], "coverage_state": "unknown", "gaps": [],
                "gaps_truncated": False, "next_offset": None, "snapshot": None}
        if not self.path.is_file():
            return data

        acl_group = source == "passwall2.acl"
        match = "source GLOB ?" if acl_group else "source = ?"
        selector = "passwall2.acl.*" if acl_group else source
        window = (router, selector, since_ms, until_ms)
        with self._reader() as db:
            # Keep records and coverage on the same SQLite snapshot while the TUI writes.
            db.execute("BEGIN")
            if snapshot is None:
                snapshot = db.execute("SELECT coalesce(max(id), 0) FROM sample_batches").fetchone()[0]
            data["snapshot"] = snapshot
            coverage = db.execute(
                f"""SELECT source, min(collected_ms), max(collected_ms),
                           min(CASE WHEN status != 'error' THEN collected_ms END),
                           max(CASE WHEN status != 'error' THEN collected_ms END),
                           count(*), sum(status = 'error'), sum(status GLOB 'gap_*')
                    FROM sample_batches WHERE router=? AND {match} AND collected_ms BETWEEN ? AND ? AND id <= ?
                    GROUP BY source ORDER BY source LIMIT 201""", (*window, snapshot),
            ).fetchall()
            data["coverage"] = [
                {"source": name, "first_batch_ms": first, "last_batch_ms": last,
                 "first_success_ms": first_ok, "last_success_ms": last_ok,
                 "batch_count": count, "read_errors": errors, "recorded_gaps": gaps}
                for name, first, last, first_ok, last_ok, count, errors, gaps in coverage
            ]
            if any(row["first_success_ms"] is not None for row in data["coverage"]):
                data["coverage_state"] = "observed"
            data["gaps"], data["gaps_truncated"] = self._query_gaps(db, window, match, source, snapshot)
            if source in METRIC_SOURCES:
                subject_filter = "AND lower(replace(subject, '-', ':'))=?" if subject is not None else ""
                params = (*window, subject) if subject is not None else window
                latest, batch_params = self._metric_snapshot_filter(db, snapshot)
                rows = db.execute(
                    f"""SELECT source, subject, event_ms, data FROM sample_metrics m
                        WHERE router=? AND source=? AND event_ms BETWEEN ? AND ? {subject_filter} {latest}
                        ORDER BY event_ms DESC, subject LIMIT ? OFFSET ?""",
                    (*params, *batch_params, limit + 1, offset),
                ).fetchall()
                data["records"] = [
                    {"source": name, "subject": key, "event_ms": at, "data": json.loads(value)}
                    for name, key, at, value in rows[:limit]
                ]
            else:
                rows = db.execute(
                    f"""SELECT b.source, l.event_ms, b.collected_ms, l.entry, b.detail
                        FROM sample_batches b JOIN sample_logs l ON l.batch_id=b.id
                        WHERE b.router=? AND b.{match} AND b.id <= ?
                          AND coalesce(l.event_ms, b.collected_ms) BETWEEN ? AND ?
                        ORDER BY coalesce(l.event_ms, b.collected_ms) DESC, b.id DESC, l.position DESC
                        LIMIT ? OFFSET ?""",
                    (*window[:2], snapshot, *window[2:], limit + 1, offset),
                ).fetchall()
                data["records"] = [
                    {"source": name, "event_ms": at, "collected_ms": collected, "entry": json.loads(entry),
                     **({"acl_binding_at_collection": json.loads(detail)} if name.startswith("passwall2.acl.") and detail else {})}
                    for name, at, collected, entry, detail in rows[:limit]
                ]
        data["database"] = "available"
        if len(rows) > limit:
            data["next_offset"] = offset + limit
        return data

    @staticmethod
    def _query_gaps(db: sqlite3.Connection, window: tuple, match: str, source: str,
                    snapshot: int) -> tuple[list[dict], bool]:
        shared = ["connection"]
        if source not in METRIC_SOURCES:
            shared.append("logs")
        if source.startswith("passwall2."):
            shared.append("passwall2.discovery")
        placeholders = ",".join("?" for _ in shared)
        router, selector, since_ms, until_ms = window
        rows = db.execute(
            f"""SELECT b.source, b.collected_ms, b.status, b.detail,
                       (SELECT max(previous.collected_ms) FROM sample_batches previous
                        WHERE previous.router=b.router AND previous.source=b.source
                          AND previous.collected_ms < b.collected_ms AND previous.status != 'error'
                          AND previous.id <= ?)
                FROM sample_batches b
                WHERE b.router=? AND (b.{match} OR b.source IN ({placeholders}))
                  AND b.collected_ms BETWEEN ? AND ? AND b.id <= ? AND (b.status='error' OR b.status GLOB 'gap_*')
                ORDER BY b.collected_ms DESC, b.id DESC LIMIT 201""",
            (snapshot, router, selector, *shared, since_ms, until_ms, snapshot),
        ).fetchall()
        return [
            {"source": name, "detected_ms": at, "previous_success_ms": previous, "type": status,
             **({"detail": detail} if status == "error" and detail else {})}
            for name, at, status, detail, previous in rows[:200]
        ], len(rows) > 200
