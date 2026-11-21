"""Configure and run the shared sampler from a terminal."""

from __future__ import annotations

import json
import signal
from datetime import datetime
from queue import Empty, Full, Queue

import typer
from rich.table import Table
from rich.text import Text

from openwrt_cli.commands.common import FormatOpt, apply_opts, fail, get_app
from openwrt_cli.context import AppContext
from openwrt_cli.core.config import ConfigManager, ProfileError, connection_for, find_profile
from openwrt_cli.core.sampling_config import parse_interval, sampling_settings
from openwrt_cli.i18n import _, t
from openwrt_cli.services.result import CommandResult
from openwrt_cli.services.runtime_logs import RuntimeLog, retention_settings
from openwrt_cli.services.sample_store import SampleWriterBusy
from openwrt_cli.services.sampler import Sampler, enabled_sources, now_ms
from openwrt_cli.ui.render import emit

sample_app = typer.Typer(no_args_is_help=True)
sample_config_app = typer.Typer(no_args_is_help=True)
sample_app.add_typer(sample_config_app, name="config", help=_("help.sample.config"))


def _show_config(app: AppContext, cfg: dict, path: str, profile: str | None, *, saved: bool = False) -> None:
    settings = sampling_settings(cfg, profile)
    raw = cfg.get("sampling") or {}
    profiles = raw.get("profiles", {}) if isinstance(raw, dict) else {}
    override = profiles.get(profile) if isinstance(profiles, dict) else None
    scope = "profile" if isinstance(override, dict) and isinstance(override.get("sections"), list) else "shared"
    shared_sections = sampling_settings(cfg)["logs"]["passwall2"]["acls"]["sections"]
    if app.format != "text":
        emit(app.console, CommandResult.ok_data({
            "path": path, "profile": profile, "saved": saved, "acl_scope": scope,
            "sampling": settings, "shared_acl_sections": shared_sections,
        }, transport="local", kind="sampling_config"), app.format)
        return
    if saved:
        app.console.print(Text(t("msg.written_ok", path=path), style="success"))
    table = Table(title=t("sample.cli.config_title"), show_lines=False)
    table.add_column(t("sample.cli.setting"))
    table.add_column(t("col.value"))
    table.add_row(t("col.profile"), Text(profile or "—"))
    table.add_row(t("sample.cli.acl_scope"), t(f"sample.cli.scope.{scope}"))

    def rows(node: dict, prefix: str = "") -> None:
        for key, value in node.items():
            label = f"{prefix}{key}"
            if isinstance(value, dict):
                rows(value, label + ".")
            elif isinstance(value, bool):
                table.add_row(label, t("col.enabled" if value else "col.disabled"))
            elif isinstance(value, list):
                table.add_row(label, Text(", ".join(value) if value else t("sample.all_acls")))
            else:
                table.add_row(label, str(value))

    rows(settings)
    app.console.print(table)


@sample_config_app.command("show", help=_("help.sample.show"))
def sample_config_show(
    ctx: typer.Context, profile: str | None = typer.Option(None, "--profile"), format: FormatOpt = None,
) -> None:
    app = apply_opts(get_app(ctx), format, False)
    profile = profile or app.cfg.get("active")
    if profile and find_profile(app.cfg, profile) is None:
        fail(app, t("err.profile_missing", name=profile), error="profile_missing")
    _show_config(app, app.cfg, app.cfg["_config_path"], profile)


@sample_config_app.command("set", help=_("help.sample.set"))
def sample_config_set(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", help=_("help.sample.profile_acls")),
    enabled: bool | None = typer.Option(None, "--enable/--disable", help=_("sample.enabled")),
    on_start: bool | None = typer.Option(None, "--on-start/--no-on-start", help=_("sample.on_start")),
    logs: bool | None = typer.Option(None, "--logs/--no-logs", help=_("sample.logs_enabled")),
    log_interval: str | None = typer.Option(None, "--log-interval", help=_("sample.interval")),
    system: bool | None = typer.Option(None, "--system/--no-system", help=_("sample.system")),
    kernel: bool | None = typer.Option(None, "--kernel/--no-kernel", help=_("sample.kernel")),
    passwall2: bool | None = typer.Option(None, "--passwall2/--no-passwall2", help=_("sample.passwall2")),
    runtime: bool | None = typer.Option(None, "--passwall2-runtime/--no-passwall2-runtime", help=_("sample.runtime")),
    acls: bool | None = typer.Option(None, "--passwall2-acls/--no-passwall2-acls", help=_("sample.acls")),
    acl_sections: list[str] | None = typer.Option(None, "--acl-section", help=_("help.sample.acl_section")),
    all_acls: bool = typer.Option(False, "--all-acls", help=_("sample.all_acls")),
    inherit_acls: bool = typer.Option(False, "--inherit-acls", help=_("help.sample.inherit_acls")),
    metrics: bool | None = typer.Option(None, "--metrics/--no-metrics", help=_("sample.metrics_enabled")),
    metric_interval: str | None = typer.Option(None, "--metric-interval", help=_("sample.interval")),
    bandix: bool | None = typer.Option(None, "--bandix/--no-bandix", help=_("sample.bandix")),
    format: FormatOpt = None,
) -> None:
    app = apply_opts(get_app(ctx), format, False)
    manager = ConfigManager(app.cfg["_config_path"])
    cfg = manager.load()
    if profile is not None and find_profile(cfg, profile) is None:
        fail(app, t("err.profile_missing", name=profile), error="profile_missing")
    settings = sampling_settings(cfg)
    raw = cfg.get("sampling")
    overrides = raw.get("profiles") if isinstance(raw, dict) else None
    if isinstance(overrides, dict):
        settings["profiles"] = overrides
    acl_settings = settings["logs"]["passwall2"]["acls"]
    updates = (
        (settings, {"enabled": enabled, "on_start": on_start}),
        (settings["logs"], {"enabled": logs, "system": system, "kernel": kernel}),
        (settings["logs"]["passwall2"], {"enabled": passwall2, "runtime": runtime}),
        (acl_settings, {"enabled": acls}),
        (settings["metrics"], {"enabled": metrics}),
        (settings["metrics"]["bandix"], {"enabled": bandix}),
    )
    has_global = any(value is not None for _, pairs in updates for value in pairs.values()) or (
        log_interval is not None or metric_interval is not None
    )
    acl_modes = int(bool(acl_sections)) + all_acls + inherit_acls
    if acl_modes > 1 or (inherit_acls and profile is None) or (profile is not None and has_global):
        fail(app, t("sample.cli.config_scope"), exit_code=2, error="sampling_scope")
    if not has_global and not acl_modes:
        fail(app, t("sample.cli.no_changes"), exit_code=2, error="sampling_no_changes")
    for node, pairs in updates:
        node.update({key: value for key, value in pairs.items() if value is not None})
    try:
        for key, value in (("logs", log_interval), ("metrics", metric_interval)):
            if value is not None:
                settings[key]["interval"] = parse_interval(value)
    except ValueError:
        fail(app, t("sample.invalid_interval"), exit_code=2, error="sampling_interval")
    if acl_modes:
        sections = list(dict.fromkeys(section.strip() for section in acl_sections or []))
        if "" in sections:
            fail(app, t("sample.pick_acl"), exit_code=2, error="sampling_acl")
        if profile is None:
            acl_settings["sections"] = sections
        else:
            per_profile = settings.setdefault("profiles", {})
            if inherit_acls:
                per_profile.pop(profile, None)
            else:
                per_profile[profile] = {"sections": sections}
    cfg["sampling"] = settings
    try:
        manager.save(cfg)
    except OSError as exc:
        fail(app, t("settings.save_error", error=type(exc).__name__), error="sampling_config_write")
    _show_config(app, cfg, manager.config_path, profile, saved=True)


def _print_event(app: AppContext, event: str, level: str, data: dict) -> None:
    if app.format != "text":
        typer.echo(json.dumps({"ok": level != "ERROR", "event": event, "level": level, **data},
                              ensure_ascii=False, separators=(",", ":")))
        return
    stamp = datetime.fromtimestamp(data["time_ms"] / 1000).strftime("%H:%M:%S")
    line = Text(stamp + "  ", style="muted")
    line.append(level.ljust(7) + " ", style={"WARNING": "warning", "ERROR": "error"}.get(level, "success"))
    if data.get("profile"):
        line.append(str(data["profile"]) + "  ", style="muted")
    if data.get("sample_source"):
        line.append(str(data["sample_source"]) + "  ", style="muted")
    line.append(str(data["message"]))
    app.console.print(line, soft_wrap=True)


@sample_app.command("run", help=_("help.sample.run"))
def sample_run(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", help=_("help.sample.profile")),
    show_logs: bool = typer.Option(False, "--show-logs", help=_("help.sample.show_logs")),
    format: FormatOpt = None,
) -> None:
    app = apply_opts(get_app(ctx), format, False)
    cfg = app.cfg

    def reject(message: str, error: str, *, code: int = 1) -> None:
        _print_event(app, "sampler.failed", "ERROR", {
            "time_ms": now_ms(), "profile": profile, "message": message, "error": error,
        })
        raise typer.Exit(code)

    if profile is not None:
        try:
            cfg = connection_for(cfg, profile)
        except ProfileError:
            reject(t("err.profile_missing", name=profile), "profile_missing", code=2)
    profile = cfg.get("active")
    settings = sampling_settings(cfg, profile)
    if not settings["enabled"]:
        reject(t("sample.cli.disabled"), "sampling_disabled", code=2)
    if not enabled_sources(settings):
        reject(t("sample.cli.no_sources"), "sampling_no_sources", code=2)
    if not cfg.get("host"):
        reject(t("msg.need_host"), "need_host", code=2)

    manager = ConfigManager(cfg["_config_path"])
    journal = RuntimeLog("sampler", policy=lambda: retention_settings(manager.load()))
    events: Queue[tuple[str, str, dict]] = Queue(maxsize=64)
    dropped = 0

    def observe(event: str, level: str, fields: dict) -> None:
        nonlocal dropped
        try:
            events.put_nowait((event, level, {"time_ms": now_ms(), "profile": profile, **fields}))
        except Full:
            dropped += 1

    def display(item: tuple[str, str, dict]) -> None:
        event, level, data = item
        entries = data.pop("entries", ())
        if event == "sampler.failed":
            busy = data.get("error") == SampleWriterBusy.__name__
            data["error_type"] = data["error"]
            data["error"] = "sampling_busy" if busy else "sampling_failed"
            data["message"] = t("sample.cli.busy") if busy else t("sample.cli.failed", **data)
        else:
            data["message"] = t(event, **data)
        _print_event(app, event, level, data)
        for entry in entries:
            if stopping:
                break
            message = str(entry.get("msg") or "")
            _print_event(app, "sampler.log", "INFO", {
                "time_ms": data["time_ms"], "profile": profile, "sample_source": data["sample_source"],
                "entry": entry, "message": message if app.format != "text" else (
                    message.replace("\x1b", "\\x1b").replace("\r", "\\r").replace("\n", "\\n")
                ),
            })

    sampler = Sampler(journal=journal, on_event=observe, show_logs=show_logs)
    stopping = False

    def stop_requested(*_args) -> None:
        nonlocal stopping
        stopping = True

    previous = {signum: signal.signal(signum, stop_requested) for signum in (signal.SIGINT, signal.SIGTERM)}
    waited = False
    try:
        sampler.configure(cfg)
        sampler.start()
        while not stopping:
            try:
                display(events.get(timeout=0.2))
            except Empty:
                if sampler.wait(0):
                    break
        sampler.stop()
        waited = True
        if not sampler.wait(35):
            reject(t("sample.cli.stop_timeout"), "sampling_stop_timeout")
        while not events.empty():
            display(events.get_nowait())
        if dropped:
            _print_event(app, "sampler.output_dropped", "WARNING", {
                "time_ms": now_ms(), "profile": profile, "dropped": dropped,
                "message": t("sample.cli.output_dropped", count=dropped),
            })
        if sampler.status.state == "error":
            raise typer.Exit(1)
    finally:
        try:
            if not waited:
                sampler.stop()
                sampler.wait(35)
        finally:
            journal.close()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
