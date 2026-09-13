from __future__ import annotations

import sys
from typing import Any

import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, get_app, parse_time_window, run_service
from openwrt_cli.i18n import _, t
from openwrt_cli.services.passwall2 import PassWall2Service
from openwrt_cli.ui.confirm import require_confirm

pw2_app = typer.Typer(help=_("help.passwall2"), no_args_is_help=True)
node_app = typer.Typer(help=_("help.pw2.node"), no_args_is_help=True)
components_app = typer.Typer(help=_("help.pw2.components"), invoke_without_command=True)
acl_app = typer.Typer(help=_("help.pw2.acl"), invoke_without_command=True)
acl_source_app = typer.Typer(help=_("help.pw2.acl.source"), no_args_is_help=True)


def _svc(ctx, format, yes):
    return apply_opts(get_app(ctx), format, yes)


@pw2_app.command("status", help=_("help.pw2.status"))
def pw2_status(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).status())


@pw2_app.command("nodes", help=_("help.pw2.nodes"))
def pw2_nodes(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).nodes())


@node_app.command("show", help=_("help.pw2.node.show"))
def pw2_node_show(ctx: typer.Context, node_id: str, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).node_show(node_id))


@node_app.command("ping", help=_("help.pw2.node.ping"))
def pw2_node_ping(
    ctx: typer.Context,
    node_id: str,
    tcp: bool = typer.Option(False, "--tcp", help=_("help.pw2.node.tcp")),
    url: bool = typer.Option(False, "--url", help=_("help.pw2.node.url")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    if tcp and url:
        raise typer.BadParameter(t("err.pw2_ping_mutex"))
    mode = "url" if url else ("tcping" if tcp else None)
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).node_ping(node_id, mode=mode))


def _parse_set_pairs(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise typer.BadParameter(t("err.pw2_set_pair", value=item))
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(t("err.pw2_set_pair", value=item))
        out[key] = value
    return out


def _node_draft(
    *,
    type_name: str | None = None,
    protocol: str | None = None,
    remarks: str | None = None,
    group: str | None = None,
    address: str | None = None,
    port: str | None = None,
    username: str | None = None,
    uuid: str | None = None,
    password: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    draft = dict(extra or {})
    for key, value in (
        ("type", type_name),
        ("protocol", protocol),
        ("remarks", remarks),
        ("group", group),
        ("address", address),
        ("port", port),
        ("username", username),
        ("uuid", uuid),
        ("password", password),
    ):
        if value:
            draft[key] = value
    return draft


def _wizard_node(draft: dict[str, str]) -> dict[str, str]:
    from openwrt_cli.services.pw2_node_schema import NODE_TYPES, PROTOCOLS_BY_TYPE, visible_fields
    from openwrt_cli.ui.wizard import ask_password, ask_select, ask_text

    filled = dict(draft)
    type_name = filled.get("type") or ask_select(t("pw2.opt.type"), list(NODE_TYPES))
    if not type_name:
        raise typer.Abort()
    filled["type"] = type_name
    protocols = list(PROTOCOLS_BY_TYPE.get(type_name, ()))
    protocol = filled.get("protocol")
    if not protocol and protocols:
        protocol = ask_select(t("pw2.opt.protocol"), protocols)
        if not protocol:
            raise typer.Abort()
        filled["protocol"] = protocol
    for item in visible_fields(type_name, filled.get("protocol") or "", filled):
        if item.key in {"type", "protocol"} or filled.get(item.key):
            continue
        if not item.required and item.kind not in {"secret"}:
            continue
        if item.kind == "secret":
            value = ask_password(t(f"pw2.opt.{item.key}") if item.key in {"uuid", "password"} else item.key)
        elif item.kind == "choice" and item.choices:
            value = ask_select(item.key, list(item.choices))
        else:
            value = ask_text(item.key)
        if value:
            filled[item.key] = value
    return filled


@node_app.command("add", help=_("help.pw2.node.add"))
def pw2_node_add(
    ctx: typer.Context,
    from_url: str | None = typer.Option(None, "--from-url", help=_("help.pw2.node.from_url")),
    type_name: str | None = typer.Option(None, "--type", help=_("help.pw2.node.type")),
    protocol: str | None = typer.Option(None, "--protocol", help=_("help.pw2.node.protocol")),
    remarks: str | None = typer.Option(None, "--remarks", help=_("help.pw2.node.remarks")),
    group: str | None = typer.Option(None, "--group", help=_("help.pw2.node.group")),
    address: str | None = typer.Option(None, "--address", help=_("help.pw2.node.address")),
    port: str | None = typer.Option(None, "--port", help=_("help.pw2.node.port")),
    username: str | None = typer.Option(None, "--username", help=_("help.pw2.node.username")),
    uuid: str | None = typer.Option(None, "--uuid", help=_("help.pw2.node.uuid")),
    password: str | None = typer.Option(None, "--password", help=_("help.pw2.node.password")),
    extra: list[str] | None = typer.Option(None, "--set", help=_("help.pw2.node.option")),
    apply: bool = typer.Option(False, "--apply", help=_("help.pw2.node.apply")),
    raw: bool = typer.Option(False, "--raw", help=_("help.pw2.node.raw")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    draft = _node_draft(
        type_name=type_name,
        protocol=protocol,
        remarks=remarks,
        group=group,
        address=address,
        port=port,
        username=username,
        uuid=uuid,
        password=password,
        extra=_parse_set_pairs(extra),
    )
    if not from_url and not draft.get("type") and sys.stdin.isatty() and app.format != "json":
        draft = _wizard_node(draft)
    elif not from_url and not draft.get("type"):
        raise typer.BadParameter(t("err.pw2_node_need_fields"))
    require_confirm(app.yes, t("confirm.pw2_node_add"), fmt=app.format)
    if apply:
        require_confirm(app.yes, t("confirm.pw2_apply"), fmt=app.format)
    run_service(
        app,
        lambda d: PassWall2Service(d).node_add(draft, apply=apply, from_url=from_url, raw=raw),
    )


@node_app.command("set", help=_("help.pw2.node.set"))
def pw2_node_set(
    ctx: typer.Context,
    node_id: str,
    type_name: str | None = typer.Option(None, "--type", help=_("help.pw2.node.type")),
    protocol: str | None = typer.Option(None, "--protocol", help=_("help.pw2.node.protocol")),
    remarks: str | None = typer.Option(None, "--remarks", help=_("help.pw2.node.remarks")),
    group: str | None = typer.Option(None, "--group", help=_("help.pw2.node.group")),
    address: str | None = typer.Option(None, "--address", help=_("help.pw2.node.address")),
    port: str | None = typer.Option(None, "--port", help=_("help.pw2.node.port")),
    username: str | None = typer.Option(None, "--username", help=_("help.pw2.node.username")),
    password: str | None = typer.Option(None, "--password", help=_("help.pw2.node.password")),
    extra: list[str] | None = typer.Option(None, "--set", help=_("help.pw2.node.option")),
    unset: list[str] | None = typer.Option(None, "--unset", help=_("help.pw2.node.unset")),
    apply: bool = typer.Option(False, "--apply", help=_("help.pw2.node.apply")),
    raw: bool = typer.Option(False, "--raw", help=_("help.pw2.node.raw")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    patch = _node_draft(
        type_name=type_name,
        protocol=protocol,
        remarks=remarks,
        group=group,
        address=address,
        port=port,
        username=username,
        password=password,
        extra=_parse_set_pairs(extra),
    )
    if not patch and not unset:
        raise typer.BadParameter(t("err.pw2_node_need_patch"))
    require_confirm(app.yes, t("confirm.pw2_node_set", id=node_id), fmt=app.format)
    if apply:
        require_confirm(app.yes, t("confirm.pw2_apply"), fmt=app.format)
    run_service(
        app,
        lambda d: PassWall2Service(d).node_set(node_id, patch, apply=apply, unset=tuple(unset or ()), raw=raw),
    )


@node_app.command("delete", help=_("help.pw2.node.delete"))
def pw2_node_delete(
    ctx: typer.Context,
    node_id: str,
    force: bool = typer.Option(False, "--force", help=_("help.pw2.node.force")),
    apply: bool = typer.Option(False, "--apply", help=_("help.pw2.node.apply")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.pw2_node_delete", id=node_id), fmt=app.format)
    if apply:
        require_confirm(app.yes, t("confirm.pw2_apply"), fmt=app.format)
    run_service(app, lambda d: PassWall2Service(d).node_delete(node_id, apply=apply, force=force))


@pw2_app.command("subscribe", help=_("help.pw2.subscribe"))
def pw2_subscribe(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).subscribe())


@pw2_app.command("settings", help=_("help.pw2.settings"))
def pw2_settings(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).settings())


@pw2_app.command("rules", help=_("help.pw2.rules"))
def pw2_rules(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).rules())


@components_app.callback(invoke_without_command=True)
def pw2_components(
    ctx: typer.Context,
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    if ctx.invoked_subcommand is not None:
        return
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).components())


@components_app.command("check", help=_("help.pw2.components.check"))
def pw2_components_check(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help=_("help.pw2.components.name")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).components_check(name))


@acl_app.callback(invoke_without_command=True)
def pw2_acl(
    ctx: typer.Context,
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    if ctx.invoked_subcommand is not None:
        return
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).acl())


def _acl_draft(
    *,
    remarks: str | None = None,
    sources: list[str] | None = None,
    node: str | None = None,
    enabled: str | None = None,
    log: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    from openwrt_cli.services.pw2_acl_schema import parse_sources

    draft: dict[str, Any] = dict(extra or {})
    if remarks is not None:
        draft["remarks"] = remarks
    if sources:
        draft["sources"] = parse_sources(sources)
    if node is not None:
        draft["node"] = node
    if enabled is not None:
        draft["enabled"] = enabled
    if log is not None:
        draft["log"] = log
    return draft


def _wizard_acl(draft: dict[str, Any]) -> dict[str, Any]:
    from openwrt_cli.ui.wizard import ask_text

    filled = dict(draft)
    if not filled.get("remarks"):
        remarks = ask_text(t("pw2.opt.remarks"))
        if remarks:
            filled["remarks"] = remarks
    if not filled.get("sources"):
        sources = ask_text(t("pw2.opt.sources"))
        if sources:
            filled["sources"] = sources
    if not filled.get("node"):
        node = ask_text(t("pw2.opt.node"))
        if node:
            filled["node"] = node
    return filled


@acl_app.command("add", help=_("help.pw2.acl.add"))
def pw2_acl_add(
    ctx: typer.Context,
    remarks: str | None = typer.Option(None, "--remarks", help=_("help.pw2.acl.remarks")),
    sources: list[str] | None = typer.Option(None, "--sources", help=_("help.pw2.acl.sources")),
    node: str | None = typer.Option(None, "--node", help=_("help.pw2.acl.node")),
    enabled: str | None = typer.Option(None, "--enabled", help=_("help.pw2.acl.enabled")),
    log: str | None = typer.Option(None, "--log", help=_("help.pw2.acl.log_flag")),
    extra: list[str] | None = typer.Option(None, "--set", help=_("help.pw2.acl.option")),
    apply: bool = typer.Option(False, "--apply", help=_("help.pw2.acl.apply")),
    raw: bool = typer.Option(False, "--raw", help=_("help.pw2.acl.raw")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    draft = _acl_draft(
        remarks=remarks,
        sources=sources,
        node=node,
        enabled=enabled,
        log=log,
        extra=_parse_set_pairs(extra),
    )
    if not draft.get("remarks") and not draft.get("sources") and sys.stdin.isatty() and app.format != "json":
        draft = _wizard_acl(draft)
    elif not draft.get("remarks"):
        raise typer.BadParameter(t("err.pw2_acl_need_fields"))
    require_confirm(app.yes, t("confirm.pw2_acl_add"), fmt=app.format)
    if apply:
        require_confirm(app.yes, t("confirm.pw2_apply"), fmt=app.format)
    run_service(app, lambda d: PassWall2Service(d).acl_add(draft, apply=apply, raw=raw))


@acl_app.command("set", help=_("help.pw2.acl.set"))
def pw2_acl_set(
    ctx: typer.Context,
    acl_id: str,
    remarks: str | None = typer.Option(None, "--remarks", help=_("help.pw2.acl.remarks")),
    sources: list[str] | None = typer.Option(None, "--sources", help=_("help.pw2.acl.sources")),
    node: str | None = typer.Option(None, "--node", help=_("help.pw2.acl.node")),
    enabled: str | None = typer.Option(None, "--enabled", help=_("help.pw2.acl.enabled")),
    log: str | None = typer.Option(None, "--log", help=_("help.pw2.acl.log_flag")),
    extra: list[str] | None = typer.Option(None, "--set", help=_("help.pw2.acl.option")),
    unset: list[str] | None = typer.Option(None, "--unset", help=_("help.pw2.acl.unset")),
    apply: bool = typer.Option(False, "--apply", help=_("help.pw2.acl.apply")),
    raw: bool = typer.Option(False, "--raw", help=_("help.pw2.acl.raw")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    patch = _acl_draft(
        remarks=remarks,
        sources=sources,
        node=node,
        enabled=enabled,
        log=log,
        extra=_parse_set_pairs(extra),
    )
    if sources is not None and not sources:
        patch["sources"] = []
    if not patch and not unset:
        raise typer.BadParameter(t("err.pw2_acl_need_patch"))
    require_confirm(app.yes, t("confirm.pw2_acl_set", id=acl_id), fmt=app.format)
    if apply:
        require_confirm(app.yes, t("confirm.pw2_apply"), fmt=app.format)
    run_service(
        app,
        lambda d: PassWall2Service(d).acl_set(acl_id, patch, apply=apply, unset=tuple(unset or ()), raw=raw),
    )


@acl_app.command("delete", help=_("help.pw2.acl.delete"))
def pw2_acl_delete(
    ctx: typer.Context,
    acl_id: str,
    apply: bool = typer.Option(False, "--apply", help=_("help.pw2.acl.apply")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.pw2_acl_delete", id=acl_id), fmt=app.format)
    if apply:
        require_confirm(app.yes, t("confirm.pw2_apply"), fmt=app.format)
    run_service(app, lambda d: PassWall2Service(d).acl_delete(acl_id, apply=apply))


@acl_source_app.command("add", help=_("help.pw2.acl.source.add"))
def pw2_acl_source_add(
    ctx: typer.Context,
    acl_id: str,
    items: list[str] = typer.Argument(..., help=_("help.pw2.acl.sources")),
    apply: bool = typer.Option(False, "--apply", help=_("help.pw2.acl.apply")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.pw2_acl_source_add", id=acl_id), fmt=app.format)
    if apply:
        require_confirm(app.yes, t("confirm.pw2_apply"), fmt=app.format)
    run_service(app, lambda d: PassWall2Service(d).acl_source_add(acl_id, items, apply=apply))


@acl_source_app.command("remove", help=_("help.pw2.acl.source.remove"))
def pw2_acl_source_remove(
    ctx: typer.Context,
    acl_id: str,
    items: list[str] = typer.Argument(..., help=_("help.pw2.acl.sources")),
    apply: bool = typer.Option(False, "--apply", help=_("help.pw2.acl.apply")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = _svc(ctx, format, yes)
    require_confirm(app.yes, t("confirm.pw2_acl_source_remove", id=acl_id), fmt=app.format)
    if apply:
        require_confirm(app.yes, t("confirm.pw2_apply"), fmt=app.format)
    run_service(app, lambda d: PassWall2Service(d).acl_source_remove(acl_id, items, apply=apply))


@acl_app.command("show", help=_("help.pw2.acl.show"))
def pw2_acl_show(ctx: typer.Context, acl_id: str, format: FormatOpt = None, yes: YesOpt = False):
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).acl_show(acl_id))


@acl_app.command("log", help=_("help.pw2.acl.log"))
def pw2_acl_log(
    ctx: typer.Context,
    acl_id: str,
    tail: int = typer.Option(80, "--tail", help=_("help.pw2.logs.tail")),
    since: str | None = typer.Option(None, "--since", help=_("help.since")),
    until: str | None = typer.Option(None, "--until", help=_("help.until")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    if tail < 1:
        raise typer.BadParameter(t("err.tail"))
    since_dt, until_dt = parse_time_window(since, until)
    run_service(
        _svc(ctx, format, yes),
        lambda d: PassWall2Service(d).acl_log(acl_id, tail=tail, since=since_dt, until=until_dt),
    )


@pw2_app.command("logs", help=_("help.pw2.logs"))
def pw2_logs(
    ctx: typer.Context,
    tail: int = typer.Option(80, "--tail", help=_("help.pw2.logs.tail")),
    since: str | None = typer.Option(None, "--since", help=_("help.since")),
    until: str | None = typer.Option(None, "--until", help=_("help.until")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    if tail < 1:
        raise typer.BadParameter(t("err.tail"))
    since_dt, until_dt = parse_time_window(since, until)
    run_service(_svc(ctx, format, yes), lambda d: PassWall2Service(d).logs(tail=tail, since=since_dt, until=until_dt))


acl_app.add_typer(acl_source_app, name="source")
pw2_app.add_typer(node_app, name="node")
pw2_app.add_typer(components_app, name="components")
pw2_app.add_typer(acl_app, name="acl")
