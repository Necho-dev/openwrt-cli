from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, run_service
from openwrt_cli.i18n import _, t
from openwrt_cli.services.monitor import MonitorService
from openwrt_cli.services.system import SystemService
from openwrt_cli.ui.confirm import require_confirm

system_app = typer.Typer(help=_("help.system"), no_args_is_help=True)


def _svc(ctx, format, yes):
    return apply_opts(get_app(ctx), format, yes)


@system_app.command("status", help=_("help.system.status"))
def system_status(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: MonitorService(d).status())


@system_app.command("info")
def system_info(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: SystemService(d).info())


@system_app.command("board")
def system_board(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: SystemService(d).board())


@system_app.command("cpu")
def system_cpu(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: MonitorService(d).cpu())


@system_app.command("memory")
def system_memory(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: MonitorService(d).memory())


@system_app.command("processes")
def system_processes(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: MonitorService(d).processes())


@system_app.command("disk")
def system_disk(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: MonitorService(d).disk())


@system_app.command("temperature")
def system_temperature(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: MonitorService(d).temperature())


@system_app.command("uptime")
def system_uptime(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: MonitorService(d).uptime())


@system_app.command("hostname")
def system_hostname(
    ctx: typer.Context,
    new_hostname: str | None = typer.Argument(None),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    if new_hostname:
        require_confirm(app.yes, t("confirm.hostname", name=new_hostname), fmt=app.format)
    run_service(app, lambda d: SystemService(d).hostname(new_hostname))


@system_app.command("reboot")
def system_reboot(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.reboot"), fmt=app.format)
    run_service(app, lambda d: SystemService(d).reboot())


@system_app.command("shutdown")
def system_shutdown(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.shutdown"), fmt=app.format)
    run_service(app, lambda d: SystemService(d).shutdown())
