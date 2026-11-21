from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import datetime

import pytest

from openwrt_cli.core.config import ConfigManager, connection_for
from openwrt_cli.mcp.session import McpSession
from openwrt_cli.services.sample_store import SampleStore
from openwrt_cli.services.sampler import router_key


@pytest.fixture
def sampling_fixture(monkeypatch, tmp_path):
    pytest.importorskip("mcp")
    from openwrt_cli.mcp import server

    config = ConfigManager(str(tmp_path / "config.yaml"))
    config.save({
        "active": "home",
        "mcp": {"mode": "readonly"},
        "profiles": [
            {"name": "home", "host": "192.0.2.1"},
            {"name": "shop", "host": "192.0.2.2", "mcp": {"mode": "readwrite"}},
        ],
    })
    path = tmp_path / "data.db"
    monkeypatch.setattr(server, "_session", McpSession(config.config_path))
    monkeypatch.setattr("openwrt_cli.services.sample_store.default_data_path", lambda: path)

    def forbidden(*_args, **_kwargs):
        pytest.fail("sampling queries must not connect to the router")

    monkeypatch.setattr("openwrt_cli.mcp.session.open_connection", forbidden)
    keys = {name: router_key(connection_for(config.load(), name)) for name in ("home", "shop")}
    return server, config, SampleStore(path), keys


def test_samples_query_local_log_range_profile_and_coverage(sampling_fixture):
    server, config, store, keys = sampling_fixture
    at = int(datetime.fromisoformat("2026-11-21T15:00:00+08:00").timestamp() * 1000)
    source = "passwall2.acl.cfg01"
    old = {"time": at - 1, "msg": "before the window"}
    a = {"time": at, "msg": "first line"}
    b = {"time": at + 1_000, "msg": "second line"}
    binding = {"node": "node1", "address": "192.0.2.8"}
    with store.writer() as db:
        store.record_logs(db, keys["home"], source, at, [{"msg": "different router"}])
        store.record_logs(db, keys["shop"], source, at + 500, [old, a, b], detail=json.dumps(binding))
        store.record_logs(db, keys["shop"], "passwall2.acl.cfg02", at + 1_500, [{"msg": "untimed line"}])
        store.record_error(db, keys["shop"], "connection", at + 2_000, "DeviceConnectionError")
        store.record_error(db, keys["shop"], source, at + 2_500, "TimeoutError")
        store.record_logs(db, keys["shop"], source, at + 3_000, [a, b])
        store.record_logs(db, keys["shop"], source, at + 4_000, [{"time": at + 3_500, "msg": "after window"}])

        snapshot = config.load()
        result = asyncio.run(server.samples_query(
            "passwall2.acl", "2026-11-21T15:00:00+08:00", str(at + 3_000), profile="shop", limit=2,
        ))
        assert result["ok"] and result["profile"] == "shop"
        assert result["database"] == "available"
        assert result["coverage_state"] == "observed"
        assert result["since_ms"] == at and result["until_ms"] == at + 3_000
        assert [row["entry"]["msg"] for row in result["records"]] == ["untimed line", "second line"]
        assert result["records"][0]["event_ms"] is None
        assert result["records"][0]["collected_ms"] == at + 1_500
        assert result["records"][1]["acl_binding_at_collection"] == binding
        assert result["next_offset"] == 2
        assert result["gaps"] == [
            {"source": source, "detected_ms": at + 2_500, "previous_success_ms": at + 500,
             "type": "error", "detail": "TimeoutError"},
            {"source": "connection", "detected_ms": at + 2_000, "previous_success_ms": None,
             "type": "error", "detail": "DeviceConnectionError"},
        ]
        assert result["coverage"][0]["first_success_ms"] == at + 500
        assert result["coverage"][0]["last_success_ms"] == at + 3_000
        assert result["coverage"][0]["read_errors"] == 1
        next_page = asyncio.run(server.samples_query(
            "passwall2.acl", str(at), str(at + 3_000), profile="shop", limit=2, offset=result["next_offset"],
        ))
        assert [row["entry"]["msg"] for row in next_page["records"]] == ["first line"]
        assert next_page["next_offset"] is None
        assert next_page["coverage"] == result["coverage"]
        exact = asyncio.run(server.samples_query(source, str(at), str(at + 4_000), profile="shop"))
        assert exact["gaps"][0]["type"] == "gap_rollover"
        assert exact["coverage"][0]["recorded_gaps"] == 1
        active = asyncio.run(server.samples_query(source, str(at), str(at + 3_000)))
        assert active["profile"] == "home"
        assert [row["entry"]["msg"] for row in active["records"]] == ["different router"]
        assert config.load() == snapshot


def test_samples_query_metrics_and_empty_windows(sampling_fixture):
    server, _, store, keys = sampling_fixture
    start = 1_800_000_000_000
    end = start + 10_000
    with store.writer() as db:
        for source, subject in [("openwrt.metrics", "router"), ("bandix.devices", "AA-BB-CC-DD-EE-FF"),
                                ("bandix.history", "all")]:
            store.record_metrics(db, keys["home"], source, start, [(subject, start, {"rx_bps": 100})], interval=1)
            store.record_metrics(db, keys["home"], source, end, [(subject, end, {"rx_bps": 200})], interval=1)
        store.record_logs(db, keys["home"], "system", end + 20_000, [{"time": start, "msg": "backfilled"}])
        for source in ("kernel", "passwall2.runtime"):
            store.record_logs(db, keys["home"], source, start, [{"msg": source}])

    for source, subject in [("openwrt.metrics", "router"), ("bandix.devices", "aa:bb:cc:dd:ee:ff"),
                            ("bandix.history", "all")]:
        result = asyncio.run(server.samples_query(source, str(start), str(end), subject=subject))
        assert [row["data"]["rx_bps"] for row in result["records"]] == [200, 100]
        assert result["coverage"][0]["recorded_gaps"] == 1
        assert result["gaps"][0]["type"] == "gap_interval"
        assert result["gaps"][0]["previous_success_ms"] == start
    for source in ("kernel", "passwall2.runtime"):
        result = asyncio.run(server.samples_query(source, str(start), str(end)))
        assert result["records"][0]["entry"]["msg"] == source
    backfilled = asyncio.run(server.samples_query("system", str(start), str(end)))
    assert backfilled["records"][0]["entry"]["msg"] == "backfilled"
    assert backfilled["coverage_state"] == "unknown"
    assert backfilled["coverage"] == []
    empty = asyncio.run(server.samples_query("bandix.devices", str(start), str(end), profile="shop"))
    assert empty["database"] == "available" and empty["coverage_state"] == "unknown"
    assert empty["coverage"] == empty["records"] == []


def test_samples_query_validates_and_does_not_create_database(sampling_fixture):
    server, _, store, _ = sampling_fixture
    plain = asyncio.run(server.samples_query("system"))
    assert plain["ok"] and plain["database"] == "missing"
    assert plain["until_ms"] - plain["since_ms"] == 3_600_000
    assert plain["coverage_state"] == "unknown"
    assert not store.path.exists()
    assert not store.path.with_suffix(".db.lock").exists()
    for kwargs, error in [
        ({"source": "connection"}, "samples_source"),
        ({"source": "system", "subject": "router"}, "samples_subject"),
        ({"source": "system", "limit": 201}, "samples_page"),
        ({"source": "system", "offset": -1}, "samples_page"),
        ({"source": "system", "cursor": "invalid"}, "samples_cursor"),
        ({"source": "system", "since": "invalid"}, "time_parse"),
        ({"source": "system", "since": "2026-11-21", "until": "2026-11-20"}, "samples_window"),
        ({"source": "system", "profile": "missing"}, "profile_missing"),
    ]:
        assert asyncio.run(server.samples_query(**kwargs))["error"] == error

    annotations = server.registered_tools()["samples_query"].annotations
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False
    assert annotations.openWorldHint is False
    assert "samples.query" not in server.write_tool_names()

    wire = asyncio.run(server.mcp.call_tool("samples_query", {"source": "system", "profile": "shop"}))
    blocks = wire[0] if isinstance(wire, tuple) else wire
    payload = blocks if isinstance(blocks, dict) else json.loads(blocks[0].text)
    assert payload["profile"] == "shop"
    assert payload["database"] == "missing"


def test_samples_cursor_keeps_log_and_metric_pages_stable(sampling_fixture):
    server, _, store, keys = sampling_fixture
    router = keys["home"]
    start = 1_800_000_000_000
    end = start + 1000
    logs = "passwall2.acl.cfg01"
    metrics = "bandix.history"
    old = {"time": start + 100, "msg": "A"}
    new = {"time": start + 200, "msg": "B"}
    late = {"time": start + 300, "msg": "late"}
    with store.writer() as db:
        store.record_logs(db, router, logs, start + 500, [old, new])
        store.record_metrics(db, router, metrics, start + 500, [
            ("all", start + 100, {"rx": 1}), ("all", start + 200, {"rx": 2}),
        ])
        first_log = asyncio.run(server.samples_query(logs, str(start), str(end), limit=1))
        first_metric = asyncio.run(server.samples_query(metrics, str(start), str(end), subject="all", limit=1))
        assert first_log["records"][0]["entry"]["msg"] == "B"
        assert first_metric["records"][0]["data"]["rx"] == 2

        store.record_logs(db, router, logs, start + 600, [new, late])
        store.record_error(db, router, logs, start + 700, "TimeoutError")
        store.record_metrics(db, router, metrics, start + 800, [
            ("all", start + 100, {"rx": 10}), ("all", start + 300, {"rx": 3}),
        ])
        second_log = asyncio.run(server.samples_query(logs, cursor=first_log["next_cursor"]))
        second_metric = asyncio.run(server.samples_query(metrics, cursor=first_metric["next_cursor"]))
        assert [row["entry"]["msg"] for row in second_log["records"]] == ["A"]
        assert second_log["coverage"] == first_log["coverage"]
        assert second_log["gaps"] == first_log["gaps"] == []
        assert second_log["next_cursor"] is None
        assert second_metric["subject"] == "all"
        assert [row["data"]["rx"] for row in second_metric["records"]] == [1]
        assert second_metric["coverage"] == first_metric["coverage"]
        assert second_metric["next_cursor"] is None
        assert store.metrics(router, metrics, "all", start) == [
            (start + 100, {"rx": 10}), (start + 200, {"rx": 2}), (start + 300, {"rx": 3}),
        ]
        fresh = asyncio.run(server.samples_query(logs, str(start), str(end)))
        assert [row["entry"]["msg"] for row in fresh["records"]] == ["late", "B", "A"]
        assert fresh["gaps"][0]["type"] == "error"
        assert asyncio.run(server.samples_query(logs, profile="shop", cursor=first_log["next_cursor"]))[
            "error"
        ] == "samples_cursor"
        assert asyncio.run(server.samples_query(metrics, cursor=first_log["next_cursor"]))["error"] == "samples_cursor"


def test_legacy_sample_metrics_read_and_resume(sampling_fixture):
    server, _, store, keys = sampling_fixture
    router = keys["home"]
    start = 1_800_000_000_000
    source = "bandix.history"
    with store.writer() as db:
        db.executescript("""
            DROP TABLE sample_metrics;
            CREATE TABLE sample_metrics (
                router TEXT, source TEXT, subject TEXT, event_ms INTEGER, data TEXT,
                PRIMARY KEY (router, source, subject, event_ms)
            );
        """)
        db.executemany("INSERT INTO sample_metrics VALUES (?, ?, ?, ?, ?)", [
            (router, source, "all", start, '{"rx":1}'),
            (router, source, "all", start + 1, '{"rx":2}'),
        ])
        db.commit()
    first = asyncio.run(server.samples_query(source, str(start), str(start + 1), limit=1))
    assert first["records"][0]["data"]["rx"] == 2
    with closing(sqlite3.connect(store.path)) as db:
        assert "batch_id" not in {row[1] for row in db.execute("PRAGMA table_info(sample_metrics)")}
    with store.writer() as db:
        store.record_metrics(db, router, source, start + 2, [("all", start, {"rx": 10})])
    second = asyncio.run(server.samples_query(source, cursor=first["next_cursor"]))
    assert [row["data"]["rx"] for row in second["records"]] == [1]
    assert store.metrics(router, source, "all", start) == [(start, {"rx": 10}), (start + 1, {"rx": 2})]
