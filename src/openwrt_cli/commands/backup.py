from __future__ import annotations

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, require_host, run_service
from openwrt_cli.services.backup import BackupService
from openwrt_cli.ui.confirm import require_confirm
from openwrt_cli.ui.render import emit

from openwrt_cli.i18n import _, t

backup_app = typer.Typer(help=_("help.backup"), no_args_is_help=True)


def _svc(ctx, format, yes):
    return apply_opts(get_app(ctx), format, yes)


@backup_app.command("create")
def backup_create(
    ctx: typer.Context,
    output: str = typer.Option("/tmp/openwrt-backup.tar.gz", "--output", "-o"),
    exclude: list[str] | None = typer.Option(None, "--exclude"),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_host(app)
    if app.format == "text":
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=app.console, transient=True) as progress:
            progress.add_task(t("backup.creating"), total=None)
            result = BackupService(app.client).create(output=output, exclude=exclude)
    else:
        result = BackupService(app.client).create(output=output, exclude=exclude)
    emit(app.console, result, app.format)
    if not result.ok:
        raise typer.Exit(1)


@backup_app.command("restore")
def backup_restore(
    ctx: typer.Context,
    backup_file: str,
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.backup_restore"), fmt=app.format)
    run_service(app, lambda d: BackupService(d).restore(backup_file))


@backup_app.command("list")
def backup_list(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: BackupService(d).list())
