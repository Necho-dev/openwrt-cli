from __future__ import annotations

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, fail, get_app, require_host
from openwrt_cli.core.errors import CapabilityError, DeviceCommandError, DeviceConnectionError
from openwrt_cli.i18n import _, t
from openwrt_cli.services.doctor import DoctorService
from openwrt_cli.ui.render import emit

doctor_app = typer.Typer(help=_("help.doctor"), invoke_without_command=True)


@doctor_app.callback(invoke_without_command=True)
def doctor_run(
    ctx: typer.Context,
    quick: bool = typer.Option(False, "--quick", help=_("help.doctor.quick")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    if ctx.invoked_subcommand:
        return
    app = apply_opts(get_app(ctx), format, yes)
    require_host(app)
    try:
        if app.format == "text":
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=app.console, transient=True) as progress:
                progress.add_task(t("doctor.running"), total=None)
                result = DoctorService(app.client).run(quick=quick)
        else:
            result = DoctorService(app.client).run(quick=quick)
    except DeviceConnectionError as e:
        fail(app, t("msg.connect_fail", error=e), error="connect")
    except (CapabilityError, DeviceCommandError) as e:
        fail(app, str(e), error="exec")
    emit(app.console, result, app.format)
    if not result.ok:
        raise typer.Exit(1)
