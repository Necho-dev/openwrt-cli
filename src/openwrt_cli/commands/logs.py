from __future__ import annotations

import json
from datetime import datetime

import typer

from openwrt_cli.commands.common import YesOpt, apply_opts, fail, get_app
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError, DeviceConnectionError
from openwrt_cli.i18n import _, t
from openwrt_cli.services.system import SystemService, format_log_line, parse_log_time
from openwrt_cli.ui.render import emit

_SOURCES = {"system": "system", "kernel": "kernel", "kernal": "kernel"}


def register_logs(app: typer.Typer) -> None:
    app.command("logs", help=_("help.logs"))(logs_cmd)


def logs_cmd(
    ctx: typer.Context,
    source: str = typer.Argument(..., metavar="system|kernel", help=_("help.logs.source")),
    follow: bool = typer.Option(False, "--follow", "-f", help=_("help.logs.follow")),
    tail: int = typer.Option(200, "--tail", help=_("help.logs.tail")),
    since: str | None = typer.Option(None, "--since", help=_("help.since")),
    until: str | None = typer.Option(None, "--until", help=_("help.until")),
    yes: YesOpt = False,
) -> None:
    src = _SOURCES.get(source.lower())
    if src is None:
        raise typer.BadParameter(t("err.logs_source"))
    _emit_logs(ctx, kernel=src == "kernel", follow=follow, tail=tail, since=since, until=until, yes=yes)


def _parse_bound(label: str, value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_log_time(value)
    except ValueError as e:
        raise typer.BadParameter(t("err.time_parse", label=label, error=e)) from e


def _emit_logs(
    ctx: typer.Context,
    *,
    kernel: bool,
    follow: bool,
    tail: int,
    since: str | None,
    until: str | None,
    yes: bool,
) -> None:
    from openwrt_cli.commands.common import require_host

    if tail < 1:
        raise typer.BadParameter(t("err.tail"))
    since_dt = _parse_bound("since", since)
    until_dt = _parse_bound("until", until)
    if since_dt and until_dt and since_dt > until_dt:
        raise typer.BadParameter(t("err.since_until"))

    app = apply_opts(get_app(ctx), None, yes)
    require_host(app)
    svc = SystemService(app.client)
    try:
        if not follow:
            emit(
                app.console,
                svc.logs(kernel=kernel, tail=tail, since=since_dt, until=until_dt),
                app.format,
            )
            return
        for entry in svc.follow_logs(kernel=kernel, tail=tail, since=since_dt, until=until_dt):
            if app.format in ("json", "compact"):
                app.console.print(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
            else:
                app.console.print(format_log_line(entry))
    except KeyboardInterrupt:
        raise typer.Exit(0) from None
    except DeviceConnectionError as e:
        fail(app, t("msg.connect_fail", error=e), error="connect")
    except CapabilityError as e:
        fail(app, str(e), error="capability")
    except DeviceCommandError as e:
        fail(app, t("msg.exec_fail", error=e), error="exec")
