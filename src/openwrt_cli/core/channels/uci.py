from __future__ import annotations

import re
from typing import Any, Protocol

from openwrt_cli.i18n import t


class UciChannel(Protocol):
    def show(self, config: str) -> dict[str, dict[str, Any]]:
        """返回 {section_name: {'.type': ..., option: value}}。"""

    def get(self, path: str) -> str:
        """uci get 风格路径，如 system.@system[0].hostname。"""

    def set(self, path: str, value: str) -> None:
        """path 形如 config.section.option。"""

    def commit(self, config: str | None = None) -> None:
        ...


def resolve_section(values: dict[str, dict[str, Any]], section: str) -> str:
    m = re.match(r"^@(\w+)\[(\d+)\]$", section)
    if not m:
        return section
    typ, idx = m.group(1), int(m.group(2))
    matches = [name for name, sec in values.items() if sec.get(".type") == typ]
    if idx >= len(matches):
        raise KeyError(t("err.uci_section", typ=typ, idx=idx))
    return matches[idx]


def option_from_values(values: dict[str, dict[str, Any]], path: str) -> str:
    parts = path.split(".")
    if len(parts) < 2:
        raise KeyError(t("err.uci_path", path=path))
    section = resolve_section(values, parts[1])
    sec = values.get(section) or {}
    if len(parts) == 2:
        return str(sec)
    val = sec.get(parts[2], "")
    if isinstance(val, list):
        return " ".join(str(x) for x in val)
    return "" if val is None else str(val)


def sections_of_type(values: dict[str, dict[str, Any]], typ: str) -> list[tuple[str, dict[str, Any]]]:
    return [(name, sec) for name, sec in values.items() if sec.get(".type") == typ]
