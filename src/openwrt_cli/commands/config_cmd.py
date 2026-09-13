from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app
from openwrt_cli.core.config import MCP_MODES, ConfigManager, public_config
from openwrt_cli.i18n import _, normalize_language, set_language, t
from openwrt_cli.services.result import CommandResult
from openwrt_cli.ui.render import emit

config_app = typer.Typer(help=_("help.config"), no_args_is_help=True)


@config_app.command("show")
def config_show(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    app = apply_opts(get_app(ctx), format, yes)
    path = app.cfg.get("_config_path") or ConfigManager().config_path
    data = public_config(app.cfg, path=path)
    emit(
        app.console,
        CommandResult.ok_data(data, transport=str(data.get("transport") or ""), kind="config"),
        app.format,
    )


@config_app.command("path")
def config_path(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    app = apply_opts(get_app(ctx), format, yes)
    path = app.cfg.get("_config_path") or ConfigManager().config_path
    if app.format in ("json", "compact"):
        emit(app.console, CommandResult.ok_data({"path": path}, transport=""), app.format)
        return
    app.console.print(path)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "-H", "--host"),
    user: str | None = typer.Option(None, "-u", "--user"),
    port: int | None = typer.Option(None, "-p", "--port"),
    ssh: bool = typer.Option(False, "--ssh"),
    http: bool = typer.Option(False, "--http"),
    https: bool = typer.Option(False, "--https"),
    language: str | None = typer.Option(None, "-L", "--language", help=_("help.opt.language")),
    mcp_mode_opt: str | None = typer.Option(None, "--mcp-mode", help=_("help.opt.mcp_mode")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    from openwrt_cli.app import _apply_connect_flags

    app = apply_opts(get_app(ctx), format, yes)
    if host:
        app.cfg["host"] = host
    if user:
        app.cfg["user"] = user
    if language:
        resolved = normalize_language(language)
        if not resolved:
            raise typer.BadParameter(t("err.language"))
        app.cfg["language"] = resolved
        set_language(resolved)
    if mcp_mode_opt is not None:
        mode = mcp_mode_opt.strip().lower()
        if mode not in MCP_MODES:
            raise typer.BadParameter(t("err.mcp_mode", mode=mcp_mode_opt))
        block = dict(app.cfg.get("mcp") or {})
        block["mode"] = mode
        app.cfg["mcp"] = block
    try:
        _apply_connect_flags(app.cfg, ssh=ssh, http=http, https=https, port=port)
    except typer.BadParameter as e:
        raise typer.BadParameter(str(e)) from e
    path = app.cfg.get("_config_path")
    mgr = ConfigManager(path)
    mgr.save(app.cfg)
    if app.format in ("json", "compact"):
        emit(
            app.console,
            CommandResult.ok_data({"path": mgr.config_path, "saved": True}, transport=str(app.cfg.get("transport") or "")),
            app.format,
        )
        return
    app.console.print(f"[success]✓ {t('msg.written_ok', path=mgr.config_path)}[/success]")
