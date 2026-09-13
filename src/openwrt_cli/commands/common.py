from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import typer

from openwrt_cli.context import AppContext
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError, DeviceConnectionError
from openwrt_cli.i18n import _, t
from openwrt_cli.services.result import CommandResult
from openwrt_cli.services.system import parse_log_time
from openwrt_cli.ui.render import emit, emit_failure

FormatOpt = Annotated[
    str | None,
    typer.Option("-f", "--format", help=_("help.format_opt")),
]
YesOpt = Annotated[
    bool,
    typer.Option("--yes", "-y", help=_("help.opt.yes")),
]


def get_app(ctx: typer.Context) -> AppContext:
    obj = ctx.find_object(AppContext)
    if obj is None:
        raise typer.BadParameter(t("msg.internal_ctx"))
    return obj


def apply_opts(app: AppContext, format: str | None, yes: bool) -> AppContext:
    if format:
        if format not in ("text", "json", "compact"):
            raise typer.BadParameter(t("msg.bad_format"))
        app.format = format  # type: ignore[assignment]
    if yes:
        app.yes = True
    return app


def fail(app: AppContext, message: str, *, exit_code: int = 1, error: str | None = None) -> None:
    emit_failure(app.console, app.format, message, error=error)
    raise typer.Exit(exit_code)


def require_host(app: AppContext) -> None:
    if not app.cfg.get("host"):
        fail(app, t("msg.need_host"), error="need_host")


def refuse_interactive(app: AppContext, name: str) -> None:
    fail(app, t("msg.interactive", name=name), exit_code=2, error="interactive")


def parse_time_window(since: str | None, until: str | None) -> tuple[datetime | None, datetime | None]:
    since_dt = _parse_time_option("since", since)
    until_dt = _parse_time_option("until", until)
    if since_dt and until_dt and since_dt > until_dt:
        raise typer.BadParameter(t("err.since_until"))
    return since_dt, until_dt


def _parse_time_option(label: str, value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_log_time(value)
    except ValueError as e:
        raise typer.BadParameter(t("err.time_parse", label=label, error=e)) from e


def run_service(app: AppContext, fn) -> None:
    require_host(app)
    try:
        result: CommandResult = fn(app.client)
    except DeviceConnectionError as e:
        fail(app, t("msg.connect_fail", error=e), error="connect")
    except CapabilityError as e:
        fail(app, str(e), error="capability")
    except DeviceCommandError as e:
        fail(app, t("msg.exec_fail", error=e), error="exec")
    if not result.transport:
        result.transport = app.client.transport
    emit(app.console, result, app.format)
    if not result.ok:
        raise typer.Exit(1)
