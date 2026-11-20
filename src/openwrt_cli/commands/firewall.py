from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, run_service
from openwrt_cli.services.firewall import FirewallService

from openwrt_cli.i18n import _, t
from openwrt_cli.ui.confirm import require_confirm

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


@firewall_app.command("set-redirect", help=_("help.fw.set_redirect"))
def fw_set_redirect(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name"),
    src_port: int = typer.Option(..., "--src-port"),
    dest_ip: str = typer.Option(..., "--dest-ip"),
    dest_port: int = typer.Option(..., "--dest-port"),
    src: str = typer.Option("wan", "--src"),
    dest: str = typer.Option("lan", "--dest"),
    proto: str = typer.Option("tcp", "--proto"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.redirect", name=name, src=src, port=src_port, proto=proto,
                              ip=dest_ip, dest_port=dest_port), fmt=app.format)
    run_service(app, lambda d: FirewallService(d).set_redirect(
        name, src_port, dest_ip, dest_port, src=src, dest=dest, proto=proto, enabled=enabled,
    ))


@firewall_app.command("status")
def fw_status(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: FirewallService(d).status())
