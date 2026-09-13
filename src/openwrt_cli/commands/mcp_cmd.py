"""Print MCP client snippets and an Agent install prompt."""

from __future__ import annotations

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app
from openwrt_cli.i18n import _, t
from openwrt_cli.mcp.extra import mcp_extra_installed, resolve_mcp_launch
from openwrt_cli.mcp.guide import agent_prompt, json_snippet, recommended_paths
from openwrt_cli.mcp.targets import client_by_id, mcp_client_ids
from openwrt_cli.services.result import CommandResult
from openwrt_cli.ui.render import emit

mcp_app = typer.Typer(help=_("help.mcp"), no_args_is_help=False)


def _warnings() -> list[str]:
    return [] if mcp_extra_installed() else ["mcp_extra_missing"]


def _check_client(name: str) -> str:
    key = (name or "generic").strip().lower()
    if key == "generic" or client_by_id(key) is not None:
        resolved = client_by_id(key)
        return resolved.id if resolved else "generic"
    raise typer.BadParameter(t("err.mcp_client", ids=", ".join(mcp_client_ids())))


@mcp_app.callback(invoke_without_command=True)
def mcp_root(
    ctx: typer.Context,
    format: FormatOpt = None,
    yes: YesOpt = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    mcp_overview(ctx, format=format, yes=yes)


def mcp_overview(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    launch = resolve_mcp_launch(None)
    data = {
        "writes_config": False,
        "next": ["json", "prompt", "path"],
        "commands": {
            "json": "openwrt mcp json",
            "prompt": "openwrt mcp prompt",
            "path": "openwrt mcp path",
        },
        "launch": launch,
    }
    emit(
        app.console,
        CommandResult.ok_data(data, transport="", kind="mcp_guide", warnings=_warnings()),
        app.format,
    )


@mcp_app.command("json", help=_("help.mcp.json"))
def mcp_json(
    ctx: typer.Context,
    client: str = typer.Option("generic", "--client", help=_("help.mcp.client")),
    command: str | None = typer.Option(None, "--command", help=_("help.mcp.command")),
    format: FormatOpt = None,
    yes: YesOpt = False,
) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    key = _check_client(client)
    info = json_snippet(key, command)
    extra = client_by_id(key)
    add = extra.mcp_add.format(command=resolve_mcp_launch(command)["command"]) if extra and extra.mcp_add else None
    data = {**info, "add": add}
    if app.format in ("json", "compact"):
        emit(
            app.console,
            CommandResult.ok_data(data, transport="", kind="mcp_json", warnings=_warnings()),
            app.format,
        )
        return
    if info["format"] == "toml":
        app.console.print(info["snippet"], end="" if str(info["snippet"]).endswith("\n") else "\n")
    else:
        app.console.print_json(data=info["snippet"])
    if add:
        app.console.print(add)
    if _warnings():
        app.console.print(f"[warning]⚠ {t('mcp.prompt.extra_missing')}[/warning]")


@mcp_app.command("prompt", help=_("help.mcp.prompt"))
def mcp_prompt_cmd(
    ctx: typer.Context,
    client: str = typer.Option("generic", "--client", help=_("help.mcp.client")),
    command: str | None = typer.Option(None, "--command", help=_("help.mcp.command")),
    format: FormatOpt = None,
    yes: YesOpt = False,
) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    key = _check_client(client)
    text = agent_prompt(key, command)
    if app.format in ("json", "compact"):
        emit(
            app.console,
            CommandResult.ok_data({"client": key, "prompt": text}, transport="", kind="mcp_prompt", warnings=_warnings()),
            app.format,
        )
        return
    app.console.print(text)


@mcp_app.command("path", help=_("help.mcp.path"))
def mcp_path(
    ctx: typer.Context,
    client: str = typer.Option("generic", "--client", help=_("help.mcp.client")),
    format: FormatOpt = None,
    yes: YesOpt = False,
) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    key = _check_client(client)
    rows = recommended_paths(None if key == "generic" else key)
    emit(
        app.console,
        CommandResult.ok_data({"paths": rows}, transport="", kind="mcp_path", warnings=_warnings()),
        app.format,
    )
