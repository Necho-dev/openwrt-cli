"""Load UI strings from locales/*.json. Add a language by adding a file."""

from __future__ import annotations

import json
from importlib.resources import files

DEFAULT_LANG = "en"


def _load_catalogs() -> dict[str, dict[str, str]]:
    root = files("openwrt_cli.i18n") / "locales"
    catalogs: dict[str, dict[str, str]] = {}
    for item in root.iterdir():
        name = item.name
        if not name.endswith(".json") or name.startswith("_"):
            continue
        data = json.loads(item.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"locale {name} must be a JSON object")
        catalogs[name[:-5]] = {
            str(key): str(value) for key, value in data.items() if not str(key).startswith("_")
        }
    if DEFAULT_LANG not in catalogs:
        raise RuntimeError(f"missing default locale {DEFAULT_LANG}.json")
    return catalogs


CATALOGS = _load_catalogs()
SUPPORTED = tuple(sorted(CATALOGS, key=lambda code: (code != DEFAULT_LANG, code)))
EN = CATALOGS[DEFAULT_LANG]
ZH = CATALOGS.get("zh", {})
