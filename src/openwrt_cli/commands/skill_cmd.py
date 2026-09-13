"""Install the packaged openwrt-ops skill into Agent directories."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import questionary
import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, fail, get_app
from openwrt_cli.i18n import _, t
from openwrt_cli.mcp.packaging import (
    copy_skill,
    remove_skill,
    skill_installed,
    skill_manifest,
    skill_source_dir,
    skill_text,
)
from openwrt_cli.mcp.targets import (
    CLIENTS,
    SKILL_NAME,
    client_by_id,
    client_public,
    custom_skill_dest,
    detected_clients,
    forbidden_skill_path,
    parse_agent_ids,
    skill_dest,
)
from openwrt_cli.services.result import CommandResult
from openwrt_cli.ui.confirm import require_confirm
from openwrt_cli.ui.render import emit
from openwrt_cli.ui.wizard import WIZARD_STYLE, ask_confirm, ask_text

skill_app = typer.Typer(help=_("help.skill"), no_args_is_help=False)
CUSTOM_ID = "custom"


def _emit(app, data: dict, *, kind: str = "skill") -> None:
    emit(app.console, CommandResult.ok_data(data, transport="", kind=kind), app.format)


def _parse_agents(raw: list[str] | None) -> list[str]:
    ids = parse_agent_ids(raw)
    for name in ids:
        if client_by_id(name) is None:
            raise typer.BadParameter(t("err.skill_agent", name=name))
    return ids


def _copy_rows() -> list[dict]:
    """One row per client: global path + whether the current project has a copy."""
    rows = []
    for client in CLIENTS:
        global_dest = skill_dest(client, project=False)
        project_dest = skill_dest(client, project=True)
        rows.append({
            "client": client.id,
            "label": client.label,
            "path": str(global_dest),
            "status": "present" if skill_installed(global_dest) else "missing",
            "project": "present" if skill_installed(project_dest) else "missing",
        })
    return rows


def _detect_rows() -> list[dict]:
    copies = {row["client"]: row for row in _copy_rows()}
    rows = []
    for client in CLIENTS:
        pub = client_public(client)
        copy = copies.get(client.id) or {}
        pub["installed"] = copy.get("status") or "missing"
        rows.append(pub)
    return rows


def _is_wizard(app) -> bool:
    return app.format == "text" and sys.stdin.isatty() and not app.yes


def _install_intro() -> str:
    found = detected_clients()
    copies = {row["client"]: row for row in _copy_rows()}
    installed = sum(1 for client in found if copies.get(client.id, {}).get("status") == "present")
    return t("skill.install.intro", name=SKILL_NAME, detected=len(found), installed=installed)


def _ask_scope() -> bool:
    picked = questionary.select(
        t("skill.scope.ask"),
        choices=[
            questionary.Choice(t("skill.scope.global"), value=False),
            questionary.Choice(t("skill.scope.project"), value=True),
        ],
        qmark="▶",
        style=WIZARD_STYLE,
    ).ask()
    if picked is None:
        raise typer.Abort()
    return bool(picked)


def _ask_custom_dir() -> str:
    raw = ask_text(t("skill.install.custom_dir"))
    if raw is None:
        raise typer.Abort()
    path = raw.strip()
    if not path:
        raise typer.BadParameter(t("err.skill_custom_dir"))
    return path


def _client_choices() -> list:
    choices = [
        questionary.Choice(
            t("skill.choice.client", label=client.label, id=client.id),
            value=client.id,
        )
        for client in detected_clients()
    ]
    choices.append(questionary.Choice(t("skill.choice.custom"), value=CUSTOM_ID))
    return choices


def _ask_clients() -> tuple[list[str], str | None]:
    picked = questionary.select(
        t("skill.install.clients"),
        choices=_client_choices(),
        qmark="▶",
        style=WIZARD_STYLE,
    ).ask()
    if picked is None:
        raise typer.Abort()
    if picked == CUSTOM_ID:
        return [], _ask_custom_dir()
    return [picked], None


def _resolve_scope(app, *, project: bool, global_opt: bool) -> bool:
    if project and global_opt:
        raise typer.BadParameter(t("err.skill_scope"))
    if project:
        return True
    if global_opt:
        return False
    if _is_wizard(app):
        return _ask_scope()
    return False


def _plan_rows(targets: list[tuple[str, Path]]) -> list[dict]:
    rows = []
    for name, dest in targets:
        client = client_by_id(name)
        blocked = forbidden_skill_path(dest)
        parent = dest.parent
        cursor = parent
        while not cursor.exists() and cursor != cursor.parent:
            cursor = cursor.parent
        writable = cursor.exists() and os.access(cursor, os.W_OK)
        if blocked:
            check = "forbidden"
        elif not writable:
            check = "not_writable"
        elif skill_installed(dest):
            check = "exists"
        else:
            check = "ready"
        rows.append({
            "client": name,
            "label": client.label if client else t("skill.label.custom"),
            "path": str(dest),
            "status": check,
        })
    return rows


def _targets(*, agents: list[str] | None, project: bool, directory: str | None) -> list[tuple[str, Path]]:
    if directory:
        return [(CUSTOM_ID, custom_skill_dest(directory))]
    ids = agents or [c.id for c in detected_clients()]
    if not ids:
        raise typer.BadParameter(t("err.skill_no_target"))
    out: list[tuple[str, Path]] = []
    for name in ids:
        client = client_by_id(name)
        if client is None:
            raise typer.BadParameter(t("err.skill_agent", name=name))
        out.append((name, skill_dest(client, project=project)))
    return out


def _collect_targets(*, agents: list[str] | None, project: bool, directory: str | None) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if directory:
        out.append((CUSTOM_ID, custom_skill_dest(directory)))
    if agents:
        out.extend(_targets(agents=agents, project=project, directory=None))
    elif not directory:
        out.extend(_targets(agents=None, project=project, directory=None))
    if not out:
        raise typer.BadParameter(t("err.skill_no_target"))
    return out


@skill_app.callback(invoke_without_command=True)
def skill_root(
    ctx: typer.Context,
    format: FormatOpt = None,
    yes: YesOpt = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    skill_detect(ctx, format=format, yes=yes)


@skill_app.command("detect", help=_("help.skill.detect"))
def skill_detect(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    _emit(
        app,
        {"skill": SKILL_NAME, "clients": _detect_rows()},
        kind="skill_detect",
    )


@skill_app.command("list", help=_("help.skill.list"))
def skill_list(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    copies = _copy_rows()
    _emit(
        app,
        {
            "bundled": [skill_manifest()],
            "copies": copies,
            "installed": [row for row in copies if row["status"] == "present" or row["project"] == "present"],
        },
        kind="skill_list",
    )


@skill_app.command("show", help=_("help.skill.show"))
def skill_show(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    src = skill_source_dir()
    text = skill_text()
    if app.format in ("json", "compact"):
        _emit(app, {"name": SKILL_NAME, "path": str(src / "SKILL.md"), "text": text})
        return
    app.console.print(text)


@skill_app.command("install", help=_("help.skill.install"))
def skill_install(
    ctx: typer.Context,
    agent: list[str] | None = typer.Option(None, "--agent", "-a", help=_("help.skill.agent")),
    project: bool = typer.Option(False, "--project", help=_("help.skill.project")),
    global_opt: bool = typer.Option(False, "--global", help=_("help.skill.global")),
    directory: str | None = typer.Option(None, "--dir", help=_("help.skill.dir")),
    format: FormatOpt = None,
    yes: YesOpt = False,
) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    wizard = _is_wizard(app) and directory is None
    agents = _parse_agents(agent)
    if wizard:
        app.console.print(f"[muted]{_install_intro()}[/muted]")
        if not agents:
            agents, directory = _ask_clients()
        if directory is None:
            project = _resolve_scope(app, project=project, global_opt=global_opt)
    elif directory is None:
        project = _resolve_scope(app, project=project, global_opt=global_opt)
    targets = _collect_targets(agents=agents, project=project, directory=directory)
    for _, dest in targets:
        if forbidden_skill_path(dest):
            fail(app, t("err.skill_forbidden_dir"), error="skill_forbidden")
    plan = _plan_rows(targets)
    if wizard:
        _emit(app, {"planned": plan}, kind="skill_plan")
        blocked = [row for row in plan if row["status"] in {"forbidden", "not_writable"}]
        if blocked:
            fail(app, t("err.skill_forbidden_dir"), error="skill_forbidden")
        if not ask_confirm(t("confirm.skill_install_plan"), default=True):
            raise typer.Abort()
    else:
        require_confirm(app.yes, t("confirm.skill_install"), fmt=app.format)
    installed = []
    for name, dest in targets:
        if forbidden_skill_path(dest):
            fail(app, t("err.skill_forbidden_dir"), error="skill_forbidden")
        status = copy_skill(dest)
        installed.append({"client": name, "scope": "custom" if name == "custom" else ("project" if project else "global"), "path": str(dest), "status": status})
    _emit(app, {"skill": SKILL_NAME, "installed": installed})


@skill_app.command("uninstall", help=_("help.skill.uninstall"))
def skill_uninstall(
    ctx: typer.Context,
    agent: list[str] | None = typer.Option(None, "--agent", "-a", help=_("help.skill.agent")),
    project: bool = typer.Option(False, "--project", help=_("help.skill.project")),
    global_opt: bool = typer.Option(False, "--global", help=_("help.skill.global")),
    directory: str | None = typer.Option(None, "--dir", help=_("help.skill.dir")),
    format: FormatOpt = None,
    yes: YesOpt = False,
) -> None:
    app = apply_opts(get_app(ctx), format, yes)
    if directory is None:
        project = _resolve_scope(app, project=project, global_opt=global_opt)
    targets = _targets(agents=_parse_agents(agent), project=project, directory=directory)
    require_confirm(app.yes, t("confirm.skill_uninstall"), fmt=app.format)
    removed = []
    for name, dest in targets:
        if forbidden_skill_path(dest):
            fail(app, t("err.skill_forbidden_dir"), error="skill_forbidden")
        ok = remove_skill(dest)
        removed.append({"client": name, "path": str(dest), "status": "removed" if ok else "missing"})
    _emit(app, {"skill": SKILL_NAME, "removed": removed})
