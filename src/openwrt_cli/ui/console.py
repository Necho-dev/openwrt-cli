from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

THEME = Theme({
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "bold red",
    "accent": "bold cyan",
    "muted": "dim",
})

_console: Console | None = None


def get_console(*, quiet: bool = False) -> Console:
    global _console
    if _console is None:
        _console = Console(theme=THEME, highlight=False, quiet=quiet)
    return _console
