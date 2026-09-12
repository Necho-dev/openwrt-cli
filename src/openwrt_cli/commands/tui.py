from __future__ import annotations

from openwrt_cli.commands.common import fail, get_app, require_host
from openwrt_cli.core.errors import DeviceConnectionError
from openwrt_cli.i18n import t
from openwrt_cli.tui.app import OpenWrtTUI


def run_tui(ctx: typer.Context) -> None:
    app = get_app(ctx)
    require_host(app)
    try:
        device = app.client
    except DeviceConnectionError as e:
        fail(app, t("msg.connect_fail", error=e), error="connect")
    OpenWrtTUI(device).run()
