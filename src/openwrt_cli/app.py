"""Typer root application."""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer

from openwrt_cli.version import package_version

from openwrt_cli.i18n import _, init_language, normalize_language, peek_argv_language, set_language, t

from openwrt_cli.commands.backup import backup_app
from openwrt_cli.commands.config_cmd import config_app
from openwrt_cli.commands.doctor import doctor_app
from openwrt_cli.commands.firewall import firewall_app
from openwrt_cli.commands.logs import register_logs
from openwrt_cli.commands.network import network_app
from openwrt_cli.commands.passwall2 import pw2_app
from openwrt_cli.commands.qos import qos_app
from openwrt_cli.commands.service import service_app
from openwrt_cli.commands.setup import run_setup
from openwrt_cli.commands.system import system_app
from openwrt_cli.commands.tui import run_tui
from openwrt_cli.commands.user import user_app
from openwrt_cli.commands.wizard_cmd import (
    run_wizard_menu,
    wizard_hostname,
    wizard_lan,
    wizard_service,
    wizard_user,
    wizard_wifi,
)
from openwrt_cli.commands.common import fail, get_app, refuse_interactive
from openwrt_cli.context import AppContext
from openwrt_cli.core.config import ConfigManager
from openwrt_cli.services.result import CommandResult
from openwrt_cli.ui.console import get_console
from openwrt_cli.ui.render import emit

_FLAG_OPTS = {
    "--https", "--http", "--ssh", "--yes", "-y", "--show-config", "--save-config",
    "--version", "-v", "--json",
}
_FORMAT_VALUES = {"text", "json", "compact"}
_VALUE_OPTS = {
    "-H", "--host", "-u", "--user", "-p", "--port", "-i", "--identity-file",
    "--password", "--config", "--format", "-L", "--language",
}


def hoist_global_options(argv: list[str]) -> list[str]:
    flags: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            rest.extend(argv[i:])
            break
        name = tok.split("=", 1)[0]
        if name in _FLAG_OPTS:
            flags.append(tok)
            i += 1
        elif name in _VALUE_OPTS:
            if "=" in tok:
                flags.append(tok)
                i += 1
            elif i + 1 < len(argv):
                flags.extend([tok, argv[i + 1]])
                i += 2
            else:
                flags.append(tok)
                i += 1
        elif _is_format_short(tok, argv[i + 1] if i + 1 < len(argv) else None):
            if "=" in tok or (tok != "-f" and tok.startswith("-f") and not tok.startswith("--")):
                flags.append(tok)
                i += 1
            else:
                flags.extend([tok, argv[i + 1]])
                i += 2
        else:
            rest.append(tok)
            i += 1
    # --json / -f json before --version so the eager version callback sees JSON mode
    head, mid, tail = [], [], []
    i = 0
    while i < len(flags):
        name = flags[i].split("=", 1)[0]
        if name == "--json":
            head.append(flags[i])
            i += 1
        elif name in ("-f", "--format") or (
            name.startswith("-f") and not name.startswith("--") and name != "-f"
        ):
            head.append(flags[i])
            if name in ("-f", "--format") and "=" not in flags[i] and i + 1 < len(flags):
                head.append(flags[i + 1])
                i += 2
            else:
                i += 1
        elif name in ("--version", "-v"):
            tail.append(flags[i])
            i += 1
        else:
            mid.append(flags[i])
            i += 1
    return head + mid + tail + rest


def _is_format_short(tok: str, nxt: str | None) -> bool:
    """Treat `-f json` as the global format; a bare `-f` stays logs --follow."""
    name, _, inline = tok.partition("=")
    if name == "-f":
        return (inline or nxt or "") in _FORMAT_VALUES
    if name.startswith("-f") and not name.startswith("--") and name != "-f":
        return name[2:] in _FORMAT_VALUES
    return False


def _mark_json(ctx: typer.Context, value: bool) -> bool:
    if value:
        ctx.meta["openwrt_json"] = True
    return value


def _show_version(ctx: typer.Context, value: bool) -> None:
    if not value:
        return
    fmt = "json" if ctx.meta.get("openwrt_json") else (ctx.params.get("format") or "text")
    ver = package_version()
    if fmt in ("json", "compact"):
        typer.echo(json.dumps({"ok": True, "name": "openwrt", "version": ver}, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"openwrt {ver}")
    raise typer.Exit()


def _apply_connect_flags(
    cfg: dict,
    *,
    ssh: bool,
    http: bool,
    https: bool,
    port: int | None,
) -> None:
    n = sum(bool(x) for x in (ssh, http, https))
    if n > 1:
        raise typer.BadParameter(t("msg.transport_one"))
    explicit_port = port
    prev = (cfg.get("transport") or "ssh").lower()
    prev_scheme = (cfg.get("scheme") or "https").lower()
    if ssh:
        cfg["transport"] = "ssh"
        if explicit_port is not None:
            cfg["port"] = explicit_port
        elif prev != "ssh":
            cfg["port"] = 22
        cfg.pop("http_port", None)
    elif https:
        cfg["transport"] = "http"
        cfg["scheme"] = "https"
        if explicit_port is not None:
            cfg["port"] = explicit_port
            cfg.pop("http_port", None)
        elif not (prev == "http" and prev_scheme == "https"):
            cfg["port"] = 443
            cfg.pop("http_port", None)
    elif http:
        cfg["transport"] = "http"
        cfg["scheme"] = "http"
        if explicit_port is not None:
            cfg["port"] = explicit_port
            cfg.pop("http_port", None)
        elif not (prev == "http" and prev_scheme == "http"):
            cfg["port"] = 80
            cfg.pop("http_port", None)
    elif explicit_port is not None:
        cfg["port"] = explicit_port
        cfg.pop("http_port", None)


app = typer.Typer(
    name="openwrt",
    help=_("help.app"),
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

app.add_typer(network_app, name="network", help=_("help.network"))
app.add_typer(firewall_app, name="firewall", help=_("help.firewall"))
app.add_typer(qos_app, name="qos", help=_("help.qos"))
app.add_typer(service_app, name="service", help=_("help.service"))
app.add_typer(pw2_app, name="passwall2", help=_("help.passwall2"))
app.add_typer(user_app, name="user", help=_("help.user"))
app.add_typer(backup_app, name="backup", help=_("help.backup"))
app.add_typer(system_app, name="system", help=_("help.system"))
app.add_typer(config_app, name="config", help=_("help.config"))
app.add_typer(doctor_app, name="doctor", help=_("help.doctor"))
register_logs(app)


@app.callback(help=_("help.app.cb"))
def root(
    ctx: typer.Context,
    host: Annotated[str | None, typer.Option("-H", "--host", help=_("help.opt.host"))] = None,
    user: Annotated[str | None, typer.Option("-u", "--user", help=_("help.opt.user"))] = None,
    port: Annotated[int | None, typer.Option("-p", "--port", help=_("help.opt.port"))] = None,
    identity_file: Annotated[
        str | None,
        typer.Option("-i", "--identity-file", help=_("help.opt.identity")),
    ] = None,
    password: Annotated[str | None, typer.Option("--password")] = None,
    ssh: Annotated[bool, typer.Option("--ssh", help=_("help.opt.ssh"))] = False,
    http: Annotated[bool, typer.Option("--http", help=_("help.opt.http"))] = False,
    https: Annotated[bool, typer.Option("--https", help=_("help.opt.https"))] = False,
    config: Annotated[str | None, typer.Option("--config", help=_("help.opt.config"))] = None,
    language: Annotated[str | None, typer.Option("-L", "--language", help=_("help.opt.language"))] = None,
    format: Annotated[str, typer.Option("-f", "--format", help=_("help.opt.format"))] = "text",
    json_out: Annotated[
        bool,
        typer.Option("--json", help=_("help.opt.json"), callback=_mark_json, is_eager=True),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help=_("help.opt.yes"))] = False,
    show_config: Annotated[bool, typer.Option("--show-config", hidden=True)] = False,
    save_config: Annotated[bool, typer.Option("--save-config", hidden=True)] = False,
    version: Annotated[
        bool,
        typer.Option("--version", "-v", help=_("help.opt.version"), callback=_show_version, is_eager=True),
    ] = False,
) -> None:
    if json_out:
        format = "json"
    if format not in ("text", "json", "compact"):
        raise typer.BadParameter(t("msg.bad_format"))

    mgr = ConfigManager(config)
    cfg = mgr.load()
    cfg["_config_path"] = mgr.config_path
    if language:
        resolved = normalize_language(language)
        if not resolved:
            raise typer.BadParameter(t("err.language"))
        set_language(resolved)
        cfg["language"] = resolved
    elif cfg.get("language"):
        set_language(cfg.get("language"))
    if host:
        cfg["host"] = host
    if user:
        cfg["user"] = user
    if identity_file:
        cfg["identity_file"] = identity_file
    if password:
        cfg["password"] = password
    _apply_connect_flags(cfg, ssh=ssh, http=http, https=https, port=port)

    obj = AppContext(cfg=cfg, format=format, yes=yes, console=get_console())  # type: ignore[arg-type]
    ctx.obj = obj
    ctx.call_on_close(obj.close)

    if show_config:
        import json
        safe = {k: v for k, v in cfg.items() if k != "_config_path"}
        if safe.get("password"):
            safe["password"] = "***"
        obj.console.print_json(data=safe)
        raise typer.Exit(0)
    if save_config:
        if not cfg.get("host"):
            fail(obj, t("msg.save_need_host"), error="need_host")
        mgr.save(cfg)
        if format in ("json", "compact"):
            emit(
                obj.console,
                CommandResult.ok_data(
                    {"path": mgr.config_path, "saved": True},
                    transport=str(cfg.get("transport") or ""),
                ),
                format,
            )
        else:
            obj.console.print(f"[success]✓ {t('msg.saved_ok', path=mgr.config_path)}[/success]")
        raise typer.Exit(0)


@app.command("setup", help=_("help.setup"))
def setup_cmd(ctx: typer.Context) -> None:
    app = get_app(ctx)
    if app.format != "text":
        refuse_interactive(app, "setup")
    run_setup(get_app_console(ctx), ctx.obj.cfg.get("_config_path"))


@app.command("tui", help=_("help.tui"))
def tui_cmd(ctx: typer.Context) -> None:
    app = get_app(ctx)
    if app.format != "text":
        refuse_interactive(app, "tui")
    run_tui(ctx)


@app.command("wizard", help=_("help.wizard"))
def wizard_cmd(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help=_("help.wizard.target")),
) -> None:
    app = get_app(ctx)
    if app.format != "text":
        refuse_interactive(app, "wizard")
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


def get_app_console(ctx: typer.Context):
    return ctx.obj.console


def main() -> None:
    lang, config_path = peek_argv_language(sys.argv[1:])
    init_language(explicit=lang, config_path=config_path)
    sys.argv = [sys.argv[0], *hoist_global_options(sys.argv[1:])]
    app(prog_name="openwrt")


if __name__ == "__main__":
    main()
