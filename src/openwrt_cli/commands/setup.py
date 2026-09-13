from __future__ import annotations

import os

import typer
from rich.console import Console

from openwrt_cli.core.config import ConfigManager, mcp_mode, normalize_config
from openwrt_cli.core.connection import open_connection
from openwrt_cli.core.errors import DeviceConnectionError
from openwrt_cli.i18n import detect_system_language, normalize_language, set_language, t
from openwrt_cli.i18n.catalog import SUPPORTED
from openwrt_cli.ui.logo import render_logo
from openwrt_cli.ui.render import setup_complete_panel
from openwrt_cli.ui.wizard import ask_confirm, ask_password, ask_select, ask_text


def _locale_hint() -> str:
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    try:
        import locale

        return locale.getlocale()[0] or ""
    except Exception:
        return ""


def _ask_language(console: Console, cfg: dict) -> str:
    detected = detect_system_language()
    saved = normalize_language(cfg.get("language"))
    preferred = saved or detected
    console.print(t("setup.lang.detected", label=_lang_label(detected), locale=_locale_hint() or detected))
    labels = [_lang_label(code) for code in SUPPORTED]
    choice = ask_select(
        t("setup.language"),
        labels,
        default=_lang_label(preferred),
    )
    if not choice:
        raise typer.Exit(1)
    lang = next((code for code in SUPPORTED if _lang_label(code) == choice), preferred)
    set_language(lang)
    return lang


def _lang_label(code: str) -> str:
    key = f"setup.lang.{code}"
    label = t(key)
    return label if label != key else code


def run_setup(console: Console, config_path: str | None = None) -> None:
    mgr = ConfigManager(config_path)
    cfg = mgr.load()

    console.print()
    console.print(render_logo())
    language = _ask_language(console, cfg)
    console.print()
    console.print(f"[accent]{t('setup.title')}[/accent]")
    host = ask_text(t("setup.host"), default=cfg.get("host") or "192.168.1.1")
    if not host:
        typer.secho(t("setup.cancelled"), err=True)
        raise typer.Exit(1)

    transport_choice = ask_select(t("setup.transport"), [
        t("setup.transport.http"),
        t("setup.transport.ssh"),
    ])
    if not transport_choice:
        raise typer.Exit(1)
    transport = "http" if "HTTP" in transport_choice else "ssh"

    user = ask_text(t("setup.user"), default=cfg.get("user") or "root")
    if not user:
        raise typer.Exit(1)

    password = None
    identity_file = None
    scheme = cfg.get("scheme") or "https"
    port = cfg.get("port") or 22

    if transport == "http":
        scheme_choice = ask_select(t("setup.http_scheme"), ["HTTPS(443)", "HTTP(80)"])
        scheme = "https" if scheme_choice and scheme_choice.startswith("HTTPS") else "http"
        default_saved = cfg.get("port") if (cfg.get("transport") or "") == "http" else None
        if default_saved in (None, 22):
            default_http = str(443 if scheme == "https" else 80)
        else:
            default_http = str(default_saved)
        port_str = ask_text(
            t("setup.port"),
            default=default_http,
            validate=lambda s: (s.isdigit() and 1 <= int(s) <= 65535) or t("setup.port_range"),
        )
        port = int(port_str) if port_str else int(default_http)
        password = ask_password(t("setup.http_password"))
        if not password:
            typer.secho(t("setup.http_need_password"), fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    else:
        auth = ask_select(t("setup.auth"), [
            t("setup.auth.password"),
            t("setup.auth.key"),
            t("setup.auth.skip"),
        ])
        if not auth:
            raise typer.Exit(1)
        if auth == t("setup.auth.password"):
            password = ask_password(t("setup.ssh_password"))
        elif auth == t("setup.auth.key"):
            default_key = cfg.get("identity_file") or os.path.expanduser("~/.ssh/id_ed25519_openwrt")
            identity_file = ask_text(t("setup.ssh_key"), default=default_key)
            if identity_file and not os.path.exists(os.path.expanduser(identity_file)):
                console.print(f"[warning]{t('setup.key_missing', path=identity_file)}[/warning]")
                if not ask_confirm(t("setup.keep_path"), default=False):
                    raise typer.Exit(0)
        port_str = ask_text(
            t("setup.ssh_port"),
            default=str(port or 22),
            validate=lambda s: (s.isdigit() and 1 <= int(s) <= 65535) or t("setup.port_range"),
        )
        port = int(port_str) if port_str else 22

    new_cfg = {k: v for k, v in cfg.items() if k not in ("_config_path", "http_port")}
    new_cfg.update({"host": host, "user": user, "transport": transport, "port": port, "language": language})
    if transport == "http":
        new_cfg["scheme"] = scheme
        new_cfg["verify_ssl"] = False
        new_cfg.pop("identity_file", None)
    else:
        new_cfg.pop("scheme", None)
        new_cfg.pop("verify_ssl", None)
    if password:
        new_cfg["password"] = password
    if identity_file:
        new_cfg["identity_file"] = os.path.expanduser(identity_file)

    mgr.save(new_cfg)
    console.print(f"[muted]{t('setup.saved', path=mgr.config_path)}[/muted]")

    hostname = ""
    try:
        client = open_connection(new_cfg)
        try:
            hostname = (client.ubus.call("system", "board") or {}).get("hostname", "")
        finally:
            client.close()
        rows = [
            (t("cfg.authenticated"), t("label.yes")),
            (t("cfg.transport"), transport),
            (t("cfg.host"), host),
            (t("cfg.user"), user),
            (t("cfg.language"), language),
        ]
        if transport == "http":
            rows.append((t("cfg.url"), f"{scheme}://{host}:{port}/ubus"))
        else:
            rows.append((t("cfg.port"), str(port)))
            if identity_file:
                rows.append((t("cfg.identity"), identity_file))
        if hostname:
            rows.append((t("cfg.hostname"), hostname))
        rows.append((t("cfg.mcp_mode"), mcp_mode(normalize_config(new_cfg))))
        console.print()
        console.print(setup_complete_panel(rows, [
            "openwrt doctor",
            "openwrt skill install",
            "openwrt mcp json",
            "openwrt tui",
        ]))
    except DeviceConnectionError as e:
        console.print(f"[warning]{t('setup.saved_but_fail')}[/warning]\n  {e}")
        console.print(f"[muted]{t('setup.fix')}[/muted]")
    except Exception as e:
        console.print(f"[warning]{t('setup.saved_but_error', error=e)}[/warning]")
