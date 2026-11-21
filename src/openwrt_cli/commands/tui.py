from __future__ import annotations

import typer

from openwrt_cli.commands.common import fail, get_app, require_host
from openwrt_cli.core.config import ConfigManager
from openwrt_cli.core.errors import DeviceConnectionError
from openwrt_cli.i18n import t
from openwrt_cli.services.runtime_logs import RuntimeLog, retention_settings
from openwrt_cli.tui.app import OpenWrtTUI


def run_tui(ctx: typer.Context) -> None:
    app = get_app(ctx)
    manager = ConfigManager(app.cfg.get("_config_path"))
    journal = RuntimeLog("tui", policy=lambda: retention_settings(manager.load()))
    journal.emit("tui", "tui.starting", profile=app.cfg.get("active"))
    tui = None
    try:
        require_host(app)
        try:
            device = app.client
        except DeviceConnectionError as exc:
            journal.emit("tui", "tui.connection_failed", level="ERROR", profile=app.cfg.get("active"),
                         error=type(exc).__name__)
            fail(app, t("msg.connect_fail", error=exc), error="connect")
        tui = OpenWrtTUI(device, app.cfg, journal=journal)
        tui.run()
    except Exception as exc:
        journal.emit("tui", "tui.failed", level="ERROR", profile=app.cfg.get("active"), error=type(exc).__name__)
        raise
    finally:
        try:
            if tui is not None:
                tui.sampler.stop(wait=2)
        finally:
            journal.emit("tui", "tui.stopped", profile=(tui.cfg if tui is not None else app.cfg).get("active"))
            journal.close()
