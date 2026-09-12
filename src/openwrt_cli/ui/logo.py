from __future__ import annotations

from rich.text import Text

# small 字体会带空行；TUI 顶栏只留可辨认的 4 行
_COMPACT_FALLBACK = (
    "  ___              __      _____ _____\n"
    " / _ \\ _ __  ___ _ \\ \\    / / _ \\_   _|\n"
    "| (_) | '_ \\/ -_) ' \\ \\/\\/ /|   / | |\n"
    " \\___/| .__/\\___|_||_\\_/\\_/ |_|_\\ |_|"
)


def _figlet(text: str, font: str, width: int = 80) -> str:
    from pyfiglet import Figlet
    return Figlet(font=font, width=width).renderText(text)


def render_logo() -> Text:
    try:
        art = _figlet("OpenWRT", "slant", 80)
    except Exception:
        art = "OpenWRT\n"
    return Text(art.rstrip("\n"), style="bold cyan")


def render_logo_compact() -> Text:
    try:
        art = _figlet("OpenWRT", "small", 56)
        lines = [ln.rstrip() for ln in art.splitlines() if ln.strip()]
        body = "\n".join(lines[:4])
    except Exception:
        body = _COMPACT_FALLBACK
    return Text(body, style="bold cyan")
