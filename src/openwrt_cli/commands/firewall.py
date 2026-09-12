from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, run_service
from openwrt_cli.services.firewall import FirewallService

from openwrt_cli.i18n import _

firewall_app = typer.Typer(help=_("help.firewall"), no_args_is_help=True)


def _svc(ctx, format, yes):
    return apply_opts(get_app(ctx), format, yes)


@firewall_app.command("rules")
def fw_rules(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: FirewallService(d).rules())


@firewall_app.command("nat")
def fw_nat(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: FirewallService(d).nat())


@firewall_app.command("zones")
def fw_zones(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: FirewallService(d).zones())


@firewall_app.command("redirects")
def fw_redirects(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: FirewallService(d).redirects())


@firewall_app.command("status")
def fw_status(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: FirewallService(d).status())
