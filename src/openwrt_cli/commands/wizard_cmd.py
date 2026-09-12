from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, require_host
from openwrt_cli.i18n import _, t
from openwrt_cli.services.network import NetworkService
from openwrt_cli.services.service import ServiceService
from openwrt_cli.services.system import SystemService
from openwrt_cli.services.user import UserService
from openwrt_cli.ui.confirm import require_confirm
from openwrt_cli.ui.logo import render_logo
from openwrt_cli.ui.render import emit as emit_result
from openwrt_cli.ui.wizard import (
    ask_confirm,
    ask_password,
    ask_select,
    ask_text,
    is_valid_hostname,
    is_valid_ip,
    is_valid_password,
)

wizard_app = typer.Typer(help=_("help.wizard"))


def _emit(ctx: typer.Context, format: str | None, yes: bool, result) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    emit_result(app.console, result, app.format)


def wizard_user(ctx: typer.Context) -> None:
    app = get_app(ctx)
    require_host(app)
    svc = UserService(app.client)
    users = svc.login_users()
    if not users:
        typer.secho(t("wizard.no_users"), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    username = ask_select(t("wizard.pick_user"), users)
    if not username:
        raise typer.Abort()
    while True:
        password = ask_password(t("wizard.new_password")) or ""
        if not is_valid_password(password):
            app.console.print(f"[error]{t('wizard.password_short')}[/error]")
            continue
        confirm = ask_password(t("wizard.password_again")) or ""
        if confirm != password:
            app.console.print(f"[error]{t('wizard.password_mismatch')}[/error]")
            continue
        break
    require_confirm(app.yes, t("confirm.user_passwd", name=username), fmt=app.format)
    _emit(ctx, None, False, svc.passwd(username, password))


def wizard_hostname(ctx: typer.Context) -> None:
    app = get_app(ctx)
    require_host(app)
    svc = SystemService(app.client)
    current = (svc.hostname().data or {}).get("hostname") or ""
    app.console.print(t("wizard.current_hostname", name=current))
    new = ask_text(
        t("wizard.new_hostname"),
        default=current,
        validate=lambda x: is_valid_hostname(x) or t("wizard.hostname_invalid"),
    )
    if not new or new == current:
        app.console.print(f"[muted]{t('wizard.hostname_unchanged')}[/muted]")
        return
    require_confirm(app.yes, t("confirm.hostname", name=new), fmt=app.format)
    _emit(ctx, None, False, svc.hostname(new))


def wizard_wifi(ctx: typer.Context) -> None:
    app = get_app(ctx)
    require_host(app)
    svc = NetworkService(app.client)
    listing = svc.wifi_list()
    items = (listing.data or {}).get("wifi") or []
    if not items:
        typer.secho(t("wizard.no_wifi"), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    labels = [
        f"[{w['section']}] SSID: {w.get('ssid') or t('wizard.ssid_unset')}  ({w.get('network')})"
        for w in items
    ]
    choice = ask_select(t("wizard.pick_wifi"), labels)
    if not choice:
        raise typer.Abort()
    sec = items[labels.index(choice)]
    ssid = sec.get("ssid")
    key = None
    if ask_confirm(t("wizard.change_ssid"), default=True):
        ssid = ask_text(t("wizard.new_ssid"), default=sec.get("ssid") or "")
    if ask_confirm(t("wizard.change_wifi_key"), default=True):
        while True:
            key = ask_password(t("wizard.new_wifi_key")) or ""
            if len(key) < 8:
                app.console.print(f"[error]{t('wizard.wifi_key_short')}[/error]")
                continue
            break
    require_confirm(app.yes, t("confirm.wifi"), fmt=app.format)
    _emit(ctx, None, False, svc.wifi_set(sec["section"], ssid=ssid, key=key))


def wizard_lan(ctx: typer.Context) -> None:
    app = get_app(ctx)
    require_host(app)
    svc = NetworkService(app.client)
    current = (svc.lan_show().data or {}).get("ipaddr") or "192.168.1.1"
    app.console.print(t("wizard.current_lan", ip=current))
    new = ask_text(
        t("wizard.new_lan"),
        default=current,
        validate=lambda x: is_valid_ip(x) or t("wizard.lan_invalid"),
    )
    if not new or new == current:
        app.console.print(f"[muted]{t('wizard.lan_unchanged')}[/muted]")
        return
    require_confirm(app.yes, t("confirm.lan", ip=new), fmt=app.format)
    _emit(ctx, None, False, svc.lan_set(new))


def wizard_service(ctx: typer.Context) -> None:
    app = get_app(ctx)
    require_host(app)
    svc = ServiceService(app.client)
    listing = svc.list()
    names = [s["name"] for s in (listing.data or {}).get("services") or []]
    if not names:
        typer.secho(t("wizard.no_services"), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    actions = [
        ("start", t("action.start")),
        ("stop", t("action.stop")),
        ("restart", t("action.restart")),
        ("reload", t("wizard.reload")),
        ("enable", t("action.startup_enable")),
        ("disable", t("action.startup_disable")),
    ]
    choice = ask_select(t("wizard.pick_action"), [label for _, label in actions])
    if not choice:
        raise typer.Abort()
    name = ask_select(t("wizard.pick_service"), names)
    if not name:
        raise typer.Abort()
    act = next(key for key, label in actions if label == choice)
    require_confirm(app.yes, t("confirm.svc", name=name, action=act), fmt=app.format)
    _emit(ctx, None, False, svc.action(name, act))


def run_wizard_menu(ctx: typer.Context) -> None:
    app = get_app(ctx)
    app.console.print()
    app.console.print(render_logo())
    items = [
        ("user", t("wizard.user"), wizard_user),
        ("hostname", t("wizard.hostname"), wizard_hostname),
        ("wifi", t("wizard.wifi"), wizard_wifi),
        ("lan", t("wizard.lan"), wizard_lan),
        ("service", t("wizard.service"), wizard_service),
    ]
    choice = ask_select(t("wizard.pick"), [label for _, label, _ in items])
    for _, label, fn in items:
        if choice == label:
            fn(ctx)
            return


@wizard_app.callback(invoke_without_command=True)
def wizard_root(
    ctx: typer.Context,
    format: FormatOpt = None,
    yes: YesOpt = False,
    target: str = typer.Argument(None, help=_("help.wizard.target")),
) -> None:
    apply_opts(get_app(ctx), format, yes)
    require_host(get_app(ctx))
    if ctx.invoked_subcommand:
        return
    mapping = {
        "user": wizard_user,
        "hostname": wizard_hostname,
        "wifi": wizard_wifi,
        "lan": wizard_lan,
        "service": wizard_service,
    }
    if target:
        if target not in mapping:
            raise typer.BadParameter(t("err.wizard_target"))
        mapping[target](ctx)
        return
    run_wizard_menu(ctx)
