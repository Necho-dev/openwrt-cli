from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, run_service
from openwrt_cli.i18n import _, t
from openwrt_cli.services.service import ServiceService
from openwrt_cli.ui.confirm import require_confirm

service_app = typer.Typer(help=_("help.service"), no_args_is_help=True)


def _svc(ctx, format, yes):
    return apply_opts(get_app(ctx), format, yes)


@service_app.command("list")
def svc_list(ctx: typer.Context, running: bool = typer.Option(False, "--running"), format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: ServiceService(d).list(running_only=running))


@service_app.command("status")
def svc_status(ctx: typer.Context, name: str, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: ServiceService(d).status(name))


@service_app.command("show", help=_("help.svc.show"))
def svc_show(ctx: typer.Context, name: str, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: ServiceService(d).show(name))


def _action(ctx, name, action, format, yes):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.svc", name=name, action=action), fmt=app.format)
    run_service(app, lambda d: ServiceService(d).action(name, action))


@service_app.command("start")
def svc_start(ctx: typer.Context, name: str, format: FormatOpt = None, yes: YesOpt = False):
    _action(ctx, name, "start", format, yes)


@service_app.command("stop")
def svc_stop(ctx: typer.Context, name: str, format: FormatOpt = None, yes: YesOpt = False):
    _action(ctx, name, "stop", format, yes)


@service_app.command("restart")
def svc_restart(ctx: typer.Context, name: str, format: FormatOpt = None, yes: YesOpt = False):
    _action(ctx, name, "restart", format, yes)


@service_app.command("reload")
def svc_reload(ctx: typer.Context, name: str, format: FormatOpt = None, yes: YesOpt = False):
    _action(ctx, name, "reload", format, yes)


@service_app.command("enable")
def svc_enable(ctx: typer.Context, name: str, format: FormatOpt = None, yes: YesOpt = False):
    _action(ctx, name, "enable", format, yes)


@service_app.command("disable")
def svc_disable(ctx: typer.Context, name: str, format: FormatOpt = None, yes: YesOpt = False):
    _action(ctx, name, "disable", format, yes)
