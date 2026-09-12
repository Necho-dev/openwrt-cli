from __future__ import annotations

import sys

import questionary
import typer

from openwrt_cli.ui.wizard import WIZARD_STYLE


def require_confirm(yes: bool, message: str, *, fmt: str = "text") -> None:
    """Prompt on a TTY; non-TTY / JSON must pass --yes. Exit code 2 on refuse."""
    if yes:
        return
    if not sys.stdin.isatty() or fmt != "text":
        from openwrt_cli.i18n import t
        from openwrt_cli.ui.console import get_console
        from openwrt_cli.ui.render import emit_failure

        if fmt in ("json", "compact"):
            emit_failure(get_console(), fmt, t("confirm.need_yes"), error="need_yes")
        else:
            print(t("confirm.need_yes"), file=sys.stderr)
        raise typer.Exit(code=2)
    if not questionary.confirm(message, default=False, qmark="▶", style=WIZARD_STYLE).ask():
        raise typer.Abort()
