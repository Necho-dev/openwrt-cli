from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, run_service
from openwrt_cli.services.user import UserService
from openwrt_cli.ui.confirm import require_confirm

from openwrt_cli.i18n import _, t

user_app = typer.Typer(help=_("help.user"), no_args_is_help=True)
key_app = typer.Typer(help=_("help.user.key"), no_args_is_help=True)


def _svc(ctx, format, yes):
    return apply_opts(get_app(ctx), format, yes)


@user_app.command("list")
def user_list(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: UserService(d).list())


@user_app.command("groups")
def user_groups(ctx: typer.Context, username: str | None = typer.Argument(None), format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: UserService(d).groups(username))


@user_app.command("add")
def user_add(
    ctx: typer.Context,
    username: str,
    password: str = typer.Option(..., "--password"),
    groups: str | None = typer.Option(None, "--groups"),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.user_add", name=username), fmt=app.format)
    run_service(app, lambda d: UserService(d).add(username, password, groups))


@user_app.command("passwd")
def user_passwd(
    ctx: typer.Context,
    username: str,
    password: str = typer.Option(..., "--password"),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.user_passwd", name=username), fmt=app.format)
    run_service(app, lambda d: UserService(d).passwd(username, password))


@user_app.command("delete")
def user_delete(ctx: typer.Context, username: str, format: FormatOpt = None, yes: YesOpt = False):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.user_delete", name=username), fmt=app.format)
    run_service(app, lambda d: UserService(d).delete(username))


@key_app.command("list")
def key_list(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: UserService(d).key_list())


@key_app.command("add")
def key_add(
    ctx: typer.Context,
    key_file: str | None = typer.Option(None, "--file", "-i"),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.key_add"), fmt=app.format)
    run_service(app, lambda d: UserService(d).key_add(key_file))


user_app.add_typer(key_app, name="key")
