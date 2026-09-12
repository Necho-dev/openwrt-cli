from __future__ import annotations

import ipaddress
import re
from datetime import datetime

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, run_service
from openwrt_cli.i18n import _, t
from openwrt_cli.services.network import NetworkService
from openwrt_cli.services.system import parse_log_time
from openwrt_cli.ui.confirm import require_confirm

network_app = typer.Typer(help=_("help.network"), no_args_is_help=True)
_MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$|^[0-9a-fA-F]{12}$")


def _svc(ctx, format, yes):
    return apply_opts(get_app(ctx), format, yes)


def _parse_bound(label: str, value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_log_time(value)
    except ValueError as e:
        raise typer.BadParameter(t("err.time_parse", label=label, error=e)) from e


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def _looks_like_mac(value: str) -> bool:
    return bool(_MAC_RE.match(value.strip()))


@network_app.command("interfaces", help=_("help.net.interfaces"))
def network_interfaces(
    ctx: typer.Context,
    rates: bool = typer.Option(False, "--rates", help=_("help.net.rates")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).stats(with_rates=rates))


@network_app.command("routes")
def network_routes(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).routes())


@network_app.command("rules", help=_("help.net.rules"))
def network_rules(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).rules())


@network_app.command("dns")
def network_dns(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).dns())


@network_app.command("dhcp")
def network_dhcp(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).dhcp())


@network_app.command("leases")
def network_leases(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).leases())


@network_app.command("neighbors", help=_("help.net.neighbors"))
@network_app.command("neighbours", hidden=True)
@network_app.command("arp", hidden=True)
def network_neighbors(
    ctx: typer.Context,
    live: bool = typer.Option(False, "--live", help=_("help.net.live")),
    ip: str | None = typer.Option(None, "--ip", help=_("help.net.filter_ip")),
    mac: str | None = typer.Option(None, "--mac", help=_("help.net.filter_mac")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    run_service(
        _svc(ctx, format, yes),
        lambda d: NetworkService(d).neighbors(live=live, ip=ip, mac=mac),
    )


@network_app.command(
    "metrics",
    help=_("help.net.metrics"),
)
def network_metrics(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help=_("help.net.metrics_target")),
    all_devices: bool = typer.Option(False, "--all", help=_("help.net.metrics_all")),
    ip: str | None = typer.Option(None, "--ip", help=_("help.net.metrics_ip")),
    mac: str | None = typer.Option(None, "--mac", help=_("help.net.metrics_mac")),
    since: str | None = typer.Option(None, "--since", help=_("help.since")),
    until: str | None = typer.Option(None, "--until", help=_("help.until")),
    raw: bool = typer.Option(False, "--raw", help=_("help.net.metrics_raw")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    selectors = [v for v in (target, ip, mac) if v]
    if all_devices and selectors:
        raise typer.BadParameter(t("err.metrics_all_mutex"))
    if len(selectors) > 1:
        raise typer.BadParameter(t("err.metrics_one"))

    resolved_ip = ip
    resolved_mac = mac
    if target:
        if _looks_like_ip(target):
            resolved_ip = target
        elif _looks_like_mac(target):
            resolved_mac = target
        else:
            raise typer.BadParameter(t("err.metrics_target"))

    since_dt = _parse_bound("since", since)
    until_dt = _parse_bound("until", until)
    if since_dt and until_dt and since_dt > until_dt:
        raise typer.BadParameter(t("err.since_until"))

    run_service(
        _svc(ctx, format, yes),
        lambda d: NetworkService(d).metrics(
            ip=resolved_ip,
            mac=resolved_mac,
            since=since_dt,
            until=until_dt,
            raw=raw,
        ),
    )


@network_app.command("stats", help=_("help.net.stats"))
def network_stats(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).stats(with_rates=True))


@network_app.command("traffic", help=_("help.net.traffic"))
def network_traffic(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).traffic())


@network_app.command("reload")
def network_reload(
    ctx: typer.Context,
    interface: str | None = typer.Argument(None),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.network_reload"), fmt=app.format)
    run_service(app, lambda d: NetworkService(d).reload(interface))


wifi_app = typer.Typer(help=_("help.net.wifi"), no_args_is_help=True)


@wifi_app.command("list")
def wifi_list(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).wifi_list())


@wifi_app.command("set")
def wifi_set(
    ctx: typer.Context,
    section: str = typer.Argument(..., help=_("help.net.wifi_section")),
    ssid: str | None = typer.Option(None, "--ssid"),
    key: str | None = typer.Option(None, "--key"),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.wifi"), fmt=app.format)
    run_service(app, lambda d: NetworkService(d).wifi_set(section, ssid=ssid, key=key))


network_app.add_typer(wifi_app, name="wifi")

lan_app = typer.Typer(help=_("help.net.lan"), no_args_is_help=True)


@lan_app.command("show")
def lan_show(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: NetworkService(d).lan_show())


@lan_app.command("set")
def lan_set(
    ctx: typer.Context,
    ipaddr: str = typer.Argument(...),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.lan", ip=ipaddr), fmt=app.format)
    run_service(app, lambda d: NetworkService(d).lan_set(ipaddr))


network_app.add_typer(lan_app, name="lan")
