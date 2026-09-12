from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: read-only tests against a real OpenWrt")


def live_enabled() -> bool:
    return os.environ.get("OPENWRT_LIVE", "").strip().lower() in {"1", "true", "yes"}


def _redact(text: str) -> str:
    """Keep pytest output free of credential-shaped tokens."""
    out = text or ""
    secret = os.environ.get("OPENWRT_PASSWORD") or ""
    if secret:
        out = out.replace(secret, "***")
    return out


def _run_cli(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "openwrt_cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_json(proc: subprocess.CompletedProcess[str]) -> dict:
    blob = (proc.stdout or "").strip()
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as e:
        raise AssertionError(f"not JSON (exit={proc.returncode}): {_redact(blob)[:400]}") from e
    assert isinstance(data, dict), data
    return data


@pytest.fixture
def run_cli():
    return _run_cli


@pytest.fixture
def parse_json():
    return _parse_json


@pytest.fixture
def redact():
    return _redact
