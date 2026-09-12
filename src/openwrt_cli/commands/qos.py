from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, run_service
from openwrt_cli.i18n import _
from openwrt_cli.services.qos import QoSService

qos_app = typer.Typer(help=_("help.qos"), no_args_is_help=True)


def _svc(ctx, format, yes):
    return apply_opts(get_app(ctx), format, yes)


@qos_app.command("status")
def qos_status(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: QoSService(d).status())


@qos_app.command("rules")
def qos_rules(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: QoSService(d).rules())


@qos_app.command("classes")
def qos_classes(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: QoSService(d).classes())


@qos_app.command("stats")
def qos_stats(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: QoSService(d).stats())


@qos_app.command("interrupts")
def qos_interrupts(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: QoSService(d).interrupts())
