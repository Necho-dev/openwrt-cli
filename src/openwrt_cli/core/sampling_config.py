"""Settings shared by the TUI and the sampler."""

from copy import deepcopy
from math import isfinite

DEFAULT_SAMPLING = {
    "enabled": False,
    "on_start": False,
    "logs": {
        "enabled": False,
        "interval": 10,
        "system": True,
        "kernel": False,
        "passwall2": {
            "enabled": False,
            "runtime": True,
            "acls": {"enabled": True, "sections": []},
        },
    },
    "metrics": {"enabled": False, "interval": 60, "bandix": {"enabled": False}},
}


def parse_interval(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("interval")
    try:
        interval = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("interval") from exc
    if not isfinite(interval) or interval <= 0:
        raise ValueError("interval")
    return interval


def _settings(defaults: dict, raw: object) -> dict:
    result = deepcopy(defaults)
    if not isinstance(raw, dict):
        return result
    for key, default in defaults.items():
        value = raw.get(key)
        if isinstance(default, dict):
            result[key] = _settings(default, value)
        elif key == "interval" and value is not None:
            try:
                result[key] = parse_interval(value)
            except ValueError:
                pass
        elif isinstance(value, type(default)):
            result[key] = value
    return result


def sampling_settings(config: dict, profile: str | None = None) -> dict:
    raw = config.get("sampling")
    result = _settings(DEFAULT_SAMPLING, raw)
    profiles = raw.get("profiles", {}) if isinstance(raw, dict) else {}
    override = profiles.get(profile, {}) if isinstance(profiles, dict) else {}
    sections = override.get("sections") if isinstance(override, dict) else None
    acl = result["logs"]["passwall2"]["acls"]
    selected = sections if isinstance(sections, list) else acl["sections"]
    # Retain invalid explicit entries so they cannot turn into "all sections".
    acl["sections"] = list(dict.fromkeys(str(item).strip() for item in selected))
    return result
