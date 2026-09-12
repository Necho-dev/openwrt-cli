from __future__ import annotations

import re

import questionary

WIZARD_STYLE = questionary.Style([
    ("qmark", "#58a6ff bold"),
    ("question", "bold"),
    ("answer", "#e6edf3 bold"),
    ("pointer", "#58a6ff bold"),
    ("highlighted", "#58a6ff bold"),
    ("selected", "#58a6ff bold"),
])


def _prompt_label(message: str) -> str:
    text = (message or "").rstrip()
    if not text or text.endswith((":", "：", "?", "？")):
        return text
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return f"{text}："
    return f"{text}:"


def is_valid_ip(ip: str) -> bool:
    return bool(re.match(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$",
        ip,
    ))


def is_valid_hostname(name: str) -> bool:
    return bool(re.match(
        r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$",
        name,
    ))


def is_valid_password(pwd: str) -> bool:
    return 6 <= len(pwd) <= 128


def ask_text(message: str, default: str | None = None, validate=None) -> str | None:
    return questionary.text(_prompt_label(message), default=default or "", qmark="▶", style=WIZARD_STYLE, validate=validate).ask()


def ask_password(message: str) -> str | None:
    return questionary.password(_prompt_label(message), qmark="▶", style=WIZARD_STYLE).ask()


def ask_select(message: str, choices: list[str], default: str | None = None) -> str | None:
    items = list(choices)
    if default in items:
        items = [default, *[item for item in items if item != default]]
    return questionary.select(
        _prompt_label(message),
        choices=items,
        qmark="▶",
        style=WIZARD_STYLE,
    ).ask()


def ask_confirm(message: str, default: bool = False) -> bool:
    return bool(questionary.confirm(_prompt_label(message), default=default, qmark="▶", style=WIZARD_STYLE).ask())
