"""Saved router profiles in ~/.openwrt-cli.yaml. Not router system users."""

from __future__ import annotations

import os
import sys

import questionary
import typer

from openwrt_cli.commands.common import FormatOpt, YesOpt, apply_opts, fail, get_app
from openwrt_cli.core.config import (
    MCP_MODE_INHERIT,
    ConfigManager,
    McpModeError,
    ProfileError,
    activate_profile,
    add_profile,
    delete_profile,
    find_profile,
    is_last_profile,
    parse_mcp_mode,
    profile_target,
    public_profile,
    public_profiles,
    update_profile,
    validate_profile_name,
)
from openwrt_cli.i18n import _, t
from openwrt_cli.services.result import CommandResult
from openwrt_cli.ui.confirm import require_confirm
from openwrt_cli.ui.render import emit
from openwrt_cli.ui.wizard import WIZARD_STYLE, _prompt_label, ask_confirm, ask_password, ask_select, ask_text

profiles_app = typer.Typer(help=_("help.profiles"), no_args_is_help=True)

_CONN_KEYS = ("host", "user", "transport", "scheme", "port", "identity_file", "verify_ssl", "password")


def _explicit(cfg: dict) -> dict:
    raw = cfg.get("_connect_explicit")
    return dict(raw) if isinstance(raw, dict) else {}


def _fail_profile(app, exc: ProfileError) -> None:
    key = {
        "invalid_name": "err.profile_name",
        "exists": "err.profile_exists",
        "missing": "err.profile_missing",
        "need_host": "err.profile_need_host",
    }.get(exc.code, "err.profile_missing")
    fail(app, t(key, name=exc.name), error=f"profile_{exc.code}")


def _parse_mode(app, value: str | None, *, allow_inherit: bool) -> str | None:
    if value is None:
        return None
    try:
        return parse_mcp_mode(value, allow_inherit=allow_inherit)
    except McpModeError as exc:
        fail(app, t("err.mcp_mode", mode=exc.mode), error="mcp_mode")


def _draft(
    base: dict,
    cfg: dict,
    *,
    host: str | None,
    user: str | None,
    port: int | None,
    identity_file: str | None,
    password: str | None,
    ssh: bool,
    http: bool,
    https: bool,
) -> dict:
    from openwrt_cli.app import _apply_connect_flags

    explicit = _explicit(cfg)
    draft = {key: base[key] for key in _CONN_KEYS if key in base}
    chosen_host = host or explicit.get("host")
    if chosen_host:
        draft["host"] = chosen_host
    chosen_user = user or explicit.get("user")
    if chosen_user:
        draft["user"] = chosen_user
    chosen_id = identity_file or explicit.get("identity_file")
    if chosen_id:
        draft["identity_file"] = chosen_id
    chosen_pw = password if password is not None else explicit.get("password")
    if chosen_pw:
        draft["password"] = chosen_pw
    chosen_port = port if port is not None else explicit.get("port")
    try:
        _apply_connect_flags(
            draft,
            ssh=bool(ssh or explicit.get("ssh")),
            http=bool(http or explicit.get("http")),
            https=bool(https or explicit.get("https")),
            port=chosen_port,
        )
    except typer.BadParameter as exc:
        raise typer.BadParameter(str(exc)) from exc
    return draft


def _save(app) -> None:
    path = app.cfg.get("_config_path")
    mgr = ConfigManager(path)
    mgr.save(app.cfg)
    if app.format in ("json", "compact"):
        emit(
            app.console,
            CommandResult.ok_data({"path": mgr.config_path, "saved": True}, transport=""),
            app.format,
        )
        return
    app.console.print(f"[success]✓ {t('msg.written_ok', path=mgr.config_path)}[/success]")


@profiles_app.command("list")
def profiles_list(ctx: typer.Context, format: FormatOpt = None, yes: YesOpt = False):
    app = apply_opts(get_app(ctx), format, yes)
    data = public_profiles(app.cfg)
    emit(app.console, CommandResult.ok_data(data, transport="", kind="profiles"), app.format)


@profiles_app.command("show")
def profiles_show(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help=_("help.profiles.show.name")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = apply_opts(get_app(ctx), format, yes)
    if not (name or "").strip() and not app.cfg.get("active"):
        fail(app, t("msg.no_profiles"), error="no_profiles")
    try:
        data = public_profile(app.cfg, name)
    except ProfileError as exc:
        _fail_profile(app, exc)
    emit(app.console, CommandResult.ok_data(data, transport="", kind="profile"), app.format)


def _guided_add(app, name: str | None) -> tuple[str, dict, str]:
    """Ask for a new profile. Enter keeps the suggested host, user, and port."""
    app.console.print(f"[accent]{t('profiles.add.title')}[/accent]")
    chosen = _require(ask_text(t("setup.profile"), default=name or "", validate=_valid_name))
    host = _require(ask_text(t("setup.host"), default="192.168.1.1"))
    ssh_label = t("setup.transport.ssh")
    http_label = t("setup.transport.http")
    transport_choice = _require(ask_select(t("setup.transport"), [ssh_label, http_label], default=ssh_label))
    transport = "http" if "HTTP" in transport_choice else "ssh"
    user = _require(ask_text(t("setup.user"), default="root"))
    fields: dict = {"host": host, "user": user, "transport": transport}
    if transport == "http":
        https_label, http_only = "HTTPS(443)", "HTTP(80)"
        scheme_choice = _require(ask_select(t("setup.http_scheme"), [https_label, http_only], default=https_label))
        scheme = "http" if scheme_choice == http_only else "https"
        port_default = "80" if scheme == "http" else "443"
        fields["scheme"] = scheme
        fields["verify_ssl"] = False
        fields["port"] = int(_require(ask_text(t("setup.port"), default=port_default, validate=_valid_port)))
        fields["password"] = _require(ask_password(t("setup.http_password")))
    else:
        password_label = t("setup.auth.password")
        key_label = t("setup.auth.key")
        skip_label = t("setup.auth.skip")
        auth = _require(ask_select(t("setup.auth"), [password_label, key_label, skip_label], default=password_label))
        if auth == password_label:
            fields["password"] = _require(ask_password(t("setup.ssh_password")))
        elif auth == key_label:
            default_key = os.path.expanduser("~/.ssh/id_ed25519_openwrt")
            identity_file = _require(ask_text(t("setup.ssh_key"), default=default_key))
            if not os.path.exists(os.path.expanduser(identity_file)):
                app.console.print(f"[warning]{t('setup.key_missing', path=identity_file)}[/warning]")
                if not ask_confirm(t("setup.keep_path"), default=False):
                    raise typer.Exit(1)
            fields["identity_file"] = os.path.expanduser(identity_file)
        fields["port"] = int(_require(ask_text(t("setup.ssh_port"), default="22", validate=_valid_port)))
    mcp_choices = {
        MCP_MODE_INHERIT: t("profiles.update.mcp.inherit"),
        "readonly": t("profiles.update.mcp.readonly"),
        "readwrite": t("profiles.update.mcp.readwrite"),
    }
    picked = _require(ask_select(
        t("profiles.update.mcp"),
        list(mcp_choices.values()),
        default=mcp_choices[MCP_MODE_INHERIT],
    ))
    mode = next(key for key, label in mcp_choices.items() if label == picked)
    return chosen, fields, mode


@profiles_app.command("add")
def profiles_add(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help=_("help.profiles.add.name")),
    host: str | None = typer.Option(None, "-H", "--host", help=_("help.opt.host")),
    user: str | None = typer.Option(None, "-u", "--user", help=_("help.opt.user")),
    port: int | None = typer.Option(None, "-p", "--port", help=_("help.opt.port")),
    identity_file: str | None = typer.Option(None, "-i", "--identity-file", help=_("help.opt.identity")),
    password: str | None = typer.Option(None, "--password", help=_("help.opt.password")),
    ssh: bool = typer.Option(False, "--ssh", help=_("help.opt.ssh")),
    http: bool = typer.Option(False, "--http", help=_("help.opt.http")),
    https: bool = typer.Option(False, "--https", help=_("help.opt.https")),
    mcp_mode_opt: str | None = typer.Option(None, "--mcp-mode", help=_("help.profiles.mcp_mode")),
    activate: bool = typer.Option(False, "--activate", help=_("help.profiles.activate_flag")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = apply_opts(get_app(ctx), format, yes)
    explicit = _explicit(app.cfg)
    if not _has_update_flags(None, host, user, port, identity_file, password, ssh, http, https, mcp_mode_opt, explicit):
        if not _can_pick_profile(app):
            if not name:
                fail(app, t("err.profile_add_name"), exit_code=2, error="need_name")
            fail(app, t("err.profile_need_host"), exit_code=1, error="profile_need_host")
        if name and find_profile(app.cfg, name) is not None:
            _fail_profile(app, ProfileError("exists", name))
        name, fields, mode = _guided_add(app, name)
    else:
        if not name:
            fail(app, t("err.profile_add_name"), exit_code=2, error="need_name")
        mode = _parse_mode(app, mcp_mode_opt, allow_inherit=True)
        fields = _draft(
            {},
            app.cfg,
            host=host,
            user=user,
            port=port,
            identity_file=identity_file,
            password=password,
            ssh=ssh,
            http=http,
            https=https,
        )
    if mode == MCP_MODE_INHERIT:
        mode = None
    try:
        add_profile(app.cfg, name, fields, activate=activate, mcp_mode_value=mode)
    except ProfileError as exc:
        _fail_profile(app, exc)
    except McpModeError as exc:
        fail(app, t("err.mcp_mode", mode=exc.mode), error="mcp_mode")
    _save(app)


def _has_update_flags(
    new_name, host, user, port, identity_file, password, ssh, http, https, mcp_mode_opt, explicit: dict,
) -> bool:
    return any([
        new_name, host, user, port is not None, identity_file, password,
        ssh, http, https, mcp_mode_opt,
        explicit.get("host"), explicit.get("user"), explicit.get("port") is not None and "port" in explicit,
        explicit.get("identity_file"), explicit.get("password"),
        explicit.get("ssh"), explicit.get("http"), explicit.get("https"),
    ])


def _require(value: str | None) -> str:
    if not value:
        raise typer.Exit(1)
    return value


def _valid_name(value: str):
    try:
        validate_profile_name(value)
    except ProfileError:
        return t("err.profile_name")
    return True


def _valid_port(value: str):
    return (value.isdigit() and 1 <= int(value) <= 65535) or t("setup.port_range")


def _guided_update(app, profile: dict) -> tuple[dict, str | None, str]:
    """Step through the saved fields. Enter keeps the shown value."""
    current_name = str(profile.get("name") or "")
    app.console.print(f"[accent]{t('profiles.update.title', name=current_name)}[/accent]")
    renamed = _require(ask_text(t("setup.profile"), default=current_name, validate=_valid_name))
    host = _require(ask_text(t("setup.host"), default=str(profile.get("host") or "192.168.1.1")))
    ssh_label = t("setup.transport.ssh")
    http_label = t("setup.transport.http")
    transport_now = "http" if str(profile.get("transport") or "ssh").lower() == "http" else "ssh"
    transport_choice = _require(ask_select(
        t("setup.transport"),
        [http_label, ssh_label],
        default=http_label if transport_now == "http" else ssh_label,
    ))
    transport = "http" if "HTTP" in transport_choice else "ssh"
    user = _require(ask_text(t("setup.user"), default=str(profile.get("user") or "root")))

    fields: dict = {key: profile[key] for key in _CONN_KEYS if key in profile and key != "http_port"}
    fields["host"] = host
    fields["user"] = user
    fields["transport"] = transport
    password = None
    identity_file = None

    if transport == "http":
        https_label, http_only = "HTTPS(443)", "HTTP(80)"
        scheme_now = str(profile.get("scheme") or "https").lower()
        scheme_choice = _require(ask_select(
            t("setup.http_scheme"),
            [https_label, http_only],
            default=https_label if scheme_now != "http" else http_only,
        ))
        scheme = "http" if scheme_choice == http_only else "https"
        if transport_now == "http" and profile.get("port") not in (None, 22):
            port_default = str(profile.get("port"))
        else:
            port_default = "80" if scheme == "http" else "443"
        port = int(_require(ask_text(t("setup.port"), default=port_default, validate=_valid_port)))
        keep = t("profiles.update.keep_auth")
        if profile.get("password"):
            auth = _require(ask_select(t("setup.auth"), [keep, t("setup.auth.password")], default=keep))
            if auth != keep:
                password = _require(ask_password(t("setup.http_password")))
        else:
            password = _require(ask_password(t("setup.http_password")))
        fields["scheme"] = scheme
        fields["verify_ssl"] = bool(profile.get("verify_ssl", False)) if transport_now == "http" else False
        fields["port"] = port
        fields["identity_file"] = ""
        if password is not None:
            fields["password"] = password
    else:
        keep = t("profiles.update.keep_auth")
        password_label = t("setup.auth.password")
        key_label = t("setup.auth.key")
        auth = _require(ask_select(t("setup.auth"), [keep, password_label, key_label], default=keep))
        if auth == password_label:
            password = _require(ask_password(t("setup.ssh_password")))
            fields["password"] = password
            fields["identity_file"] = ""
        elif auth == key_label:
            default_key = str(profile.get("identity_file") or os.path.expanduser("~/.ssh/id_ed25519_openwrt"))
            identity_file = _require(ask_text(t("setup.ssh_key"), default=default_key))
            if not os.path.exists(os.path.expanduser(identity_file)):
                app.console.print(f"[warning]{t('setup.key_missing', path=identity_file)}[/warning]")
                if not ask_confirm(t("setup.keep_path"), default=False):
                    raise typer.Exit(1)
            fields["identity_file"] = os.path.expanduser(identity_file)
            fields["password"] = ""
        port_default = str(profile.get("port") or 22) if transport_now == "ssh" else "22"
        fields["port"] = int(_require(ask_text(t("setup.ssh_port"), default=port_default, validate=_valid_port)))
        fields["scheme"] = ""
        fields["verify_ssl"] = ""

    stored = profile.get("mcp") if isinstance(profile.get("mcp"), dict) else {}
    current_mode = str(stored.get("mode") or MCP_MODE_INHERIT)
    mcp_choices = {
        MCP_MODE_INHERIT: t("profiles.update.mcp.inherit"),
        "readonly": t("profiles.update.mcp.readonly"),
        "readwrite": t("profiles.update.mcp.readwrite"),
    }
    if current_mode not in mcp_choices:
        current_mode = MCP_MODE_INHERIT
    picked = _require(ask_select(
        t("profiles.update.mcp"),
        list(mcp_choices.values()),
        default=mcp_choices[current_mode],
    ))
    mode = next(key for key, label in mcp_choices.items() if label == picked)
    new_name = renamed if renamed != current_name else None
    return fields, new_name, mode


@profiles_app.command("update")
def profiles_update(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help=_("help.profiles.update.name")),
    new_name: str | None = typer.Option(None, "--name", help=_("help.profiles.name")),
    host: str | None = typer.Option(None, "-H", "--host", help=_("help.opt.host")),
    user: str | None = typer.Option(None, "-u", "--user", help=_("help.opt.user")),
    port: int | None = typer.Option(None, "-p", "--port", help=_("help.opt.port")),
    identity_file: str | None = typer.Option(None, "-i", "--identity-file", help=_("help.opt.identity")),
    password: str | None = typer.Option(None, "--password", help=_("help.opt.password")),
    ssh: bool = typer.Option(False, "--ssh", help=_("help.opt.ssh")),
    http: bool = typer.Option(False, "--http", help=_("help.opt.http")),
    https: bool = typer.Option(False, "--https", help=_("help.opt.https")),
    mcp_mode_opt: str | None = typer.Option(None, "--mcp-mode", help=_("help.profiles.mcp_mode")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = apply_opts(get_app(ctx), format, yes)
    explicit = _explicit(app.cfg)
    if not _has_update_flags(new_name, host, user, port, identity_file, password, ssh, http, https, mcp_mode_opt, explicit):
        if not _can_pick_profile(app):
            key = "err.profile_update_name" if not name else "err.profile_update_fields"
            fail(app, t(key), exit_code=2, error="need_name" if not name else "need_fields")
        if not name:
            name = _pick_profile(app, "profiles.update.prompt")
        profile = find_profile(app.cfg, name)
        if profile is None:
            _fail_profile(app, ProfileError("missing", name))
        fields, new_name, mode = _guided_update(app, profile or {})
    else:
        if not name:
            fail(app, t("err.profile_update_name"), exit_code=2, error="need_name")
        mode = _parse_mode(app, mcp_mode_opt, allow_inherit=True)
        fields = _draft(
            find_profile(app.cfg, name) or {},
            app.cfg,
            host=host,
            user=user,
            port=port,
            identity_file=identity_file,
            password=password,
            ssh=ssh,
            http=http,
            https=https,
        )
    try:
        update_profile(app.cfg, name, fields, new_name=new_name, mcp_mode_value=mode)
    except ProfileError as exc:
        _fail_profile(app, exc)
    except McpModeError as exc:
        fail(app, t("err.mcp_mode", mode=exc.mode), error="mcp_mode")
    _save(app)


def _can_pick_profile(app) -> bool:
    return app.format == "text" and sys.stdin.isatty()


def _pick_profile(app, prompt_key: str = "profiles.use.prompt") -> str:
    """Arrow keys move the highlight. Enter confirms. Esc cancels."""
    profiles = [item for item in (app.cfg.get("profiles") or []) if isinstance(item, dict) and item.get("name")]
    if not profiles:
        fail(app, t("msg.no_profiles"), error="no_profiles")
    active = app.cfg.get("active")
    labels: list[str] = []
    by_label: dict[str, str] = {}
    default_label = None
    for profile in profiles:
        name = str(profile.get("name"))
        mark = "● " if name == active else "  "
        label = f"{mark}{name}   {profile_target(profile)}"
        labels.append(label)
        by_label[label] = name
        if name == active:
            default_label = label
    choice = questionary.select(
        _prompt_label(t(prompt_key)),
        choices=labels,
        default=default_label,
        qmark="▶",
        style=WIZARD_STYLE,
        use_arrow_keys=True,
        use_jk_keys=False,
        instruction=t("profiles.use.keys"),
    ).ask()
    if not choice:
        raise typer.Exit(1)
    return by_label[str(choice)]


@profiles_app.command("use")
def profiles_use(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help=_("help.profiles.use.name")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = apply_opts(get_app(ctx), format, yes)
    if not name:
        if not _can_pick_profile(app):
            fail(app, t("err.profile_use_name"), exit_code=2, error="need_name")
        name = _pick_profile(app)
    try:
        activate_profile(app.cfg, name)
    except ProfileError as exc:
        _fail_profile(app, exc)
    _save(app)


@profiles_app.command("del")
def profiles_del(
    ctx: typer.Context,
    name: str = typer.Argument(..., help=_("help.profiles.del.name")),
    format: FormatOpt = None,
    yes: YesOpt = False,
):
    app = apply_opts(get_app(ctx), format, yes)
    if find_profile(app.cfg, name) is None:
        _fail_profile(app, ProfileError("missing", name))
    message = t("confirm.profile_delete_last", name=name) if is_last_profile(app.cfg, name) else t("confirm.profile_delete", name=name)
    require_confirm(app.yes, message, fmt=app.format)
    try:
        delete_profile(app.cfg, name)
    except ProfileError as exc:
        _fail_profile(app, exc)
    _save(app)
