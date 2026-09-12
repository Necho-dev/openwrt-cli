"""Runtime i18n: labels, help, and messages. Command names stay English."""

from __future__ import annotations

import os
from typing import Any

from openwrt_cli.i18n.catalog import CATALOGS, DEFAULT_LANG, SUPPORTED

_ENV_VAR = "OPENWRT_LANG"
_lang = DEFAULT_LANG


def normalize_language(value: str | None) -> str | None:
    raw = (value or "").strip().replace("-", "_").lower()
    if not raw:
        return None
    primary = raw.split(".", 1)[0].split("_", 1)[0]
    if raw.startswith("zh") or primary in {"zh", "cn"} or raw == "chinese":
        return "zh" if "zh" in SUPPORTED else None
    if raw in SUPPORTED:
        return raw
    if primary in SUPPORTED:
        return primary
    return None


def detect_system_language() -> str:
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        found = normalize_language(os.environ.get(key))
        if found:
            return found
    try:
        import locale

        for candidate in (locale.getlocale()[0], locale.getdefaultlocale()[0]):
            found = normalize_language(candidate)
            if found:
                return found
    except Exception:
        pass
    return DEFAULT_LANG


def peek_config_language(config_path: str | None = None) -> str | None:
    try:
        from openwrt_cli.core.config import ConfigManager

        cfg = ConfigManager(config_path).load() or {}
        return normalize_language(cfg.get("language"))
    except Exception:
        return None


def resolve_language(
    *,
    explicit: str | None = None,
    configured: str | None = None,
) -> str:
    env = normalize_language(os.environ.get(_ENV_VAR))
    return (
        normalize_language(explicit)
        or env
        or normalize_language(configured)
        or detect_system_language()
    )


def current_language() -> str:
    return _lang


def set_language(lang: str | None) -> str:
    global _lang
    _lang = normalize_language(lang) or DEFAULT_LANG
    return _lang


def init_language(*, explicit: str | None = None, config_path: str | None = None) -> str:
    return set_language(resolve_language(explicit=explicit, configured=peek_config_language(config_path)))


def t(key: str, **kwargs: Any) -> str:
    table = CATALOGS.get(_lang) or CATALOGS[DEFAULT_LANG]
    text = table.get(key)
    if text is None:
        text = CATALOGS[DEFAULT_LANG].get(key, key)
    if "{langs}" in text and "langs" not in kwargs:
        kwargs = {**kwargs, "langs": " / ".join(SUPPORTED)}
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            return text
    return text


class LazyStr:
    """Click/Typer help is rendered later; resolve against the current language."""

    def __init__(self, key: str, **kwargs: Any):
        self.key = key
        self.kwargs = kwargs

    def _s(self) -> str:
        return t(self.key, **self.kwargs)

    def __str__(self) -> str:
        return self._s()

    def __repr__(self) -> str:
        return self._s()

    def __html__(self) -> str:
        return self._s()

    def __len__(self) -> int:
        return len(self._s())

    def __iter__(self):
        return iter(self._s())

    def __mod__(self, other):
        return self._s() % other

    def __add__(self, other):
        return self._s() + str(other)

    def __radd__(self, other):
        return str(other) + self._s()

    def __eq__(self, other):
        return self._s() == other

    def __hash__(self) -> int:
        return hash(self.key)

    def __format__(self, spec: str) -> str:
        return format(self._s(), spec)

    def __getattr__(self, name: str):
        return getattr(self._s(), name)


def _(key: str, **kwargs: Any) -> LazyStr:
    return LazyStr(key, **kwargs)


def peek_argv_language(argv: list[str]) -> tuple[str | None, str | None]:
    """Read -L/--language and --config without running Typer."""
    lang = None
    config = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-L", "--language") and i + 1 < len(argv):
            lang = argv[i + 1]
            i += 2
            continue
        if tok.startswith("--language="):
            lang = tok.split("=", 1)[1]
            i += 1
            continue
        if tok.startswith("-L") and len(tok) > 2 and not tok.startswith("--"):
            lang = tok[2:]
            i += 1
            continue
        if tok == "--config" and i + 1 < len(argv):
            config = argv[i + 1]
            i += 2
            continue
        if tok.startswith("--config="):
            config = tok.split("=", 1)[1]
            i += 1
            continue
        i += 1
    return lang, config


init_language()
