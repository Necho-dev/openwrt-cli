from __future__ import annotations

from typing import Any

from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.errors import DeviceCommandError
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult


class ServiceService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def list(self, running_only: bool = False) -> CommandResult:
        services: list[dict[str, Any]] = []
        if Capability.SHELL in self.device.capabilities:
            starts = _parse_init_start(self.device.shell.exec("grep -H '^START=' /etc/init.d/* 2>/dev/null"))
            raw = self.device.shell.exec("ls /etc/init.d/ 2>/dev/null")
            for name in raw.strip().splitlines():
                name = name.strip()
                if not name:
                    continue
                running = "running" in self.device.shell.exec(
                    f"/etc/init.d/{name} running 2>/dev/null && echo running || echo stopped"
                )
                enabled = "y" in self.device.shell.exec(
                    f"/etc/init.d/{name} enabled 2>/dev/null && echo y || echo n"
                )
                if running_only and not running:
                    continue
                services.append({
                    "name": name,
                    "running": running,
                    "enabled": enabled,
                    "priority": starts.get(name),
                })
        else:
            self.device.require(Capability.INITD)
            listing = self.device.ubus.call("rc", "list")
            for name, info in listing.items():
                running = bool(info.get("running"))
                if running_only and not running:
                    continue
                services.append({
                    "name": name,
                    "running": running,
                    "enabled": bool(info.get("enabled")),
                    "priority": info.get("start"),
                })
        services.sort(key=lambda s: (
            s.get("priority") is None,
            s.get("priority") if isinstance(s.get("priority"), (int, float)) else 999,
            s.get("name") or "",
        ))
        return CommandResult.ok_data({"services": services}, transport=self.device.transport, kind="services")

    def status(self, name: str) -> CommandResult:
        if Capability.SHELL in self.device.capabilities:
            raw = self.device.shell.exec(f"/etc/init.d/{name} running 2>&1")
            running = "running" in raw.lower() or "active" in raw.lower()
            return CommandResult.ok_data(
                {"service": name, "running": running, "raw_output": raw.strip()},
                transport=self.device.transport,
                kind="service",
            )
        listing = self.device.ubus.call("rc", "list")
        info = listing.get(name) or {}
        return CommandResult.ok_data(
            {
                "service": name,
                "running": bool(info.get("running")),
                "enabled": bool(info.get("enabled")),
                "priority": info.get("start"),
            },
            transport=self.device.transport,
            kind="service",
        )

    def show(self, name: str) -> CommandResult:
        status = self.status(name)
        payload = dict(status.data or {})
        uci_summary = None
        try:
            values = self.device.uci.show(name)
            if not values:
                raise DeviceCommandError(t("err.uci_pkg_missing", name=name))
            types: dict[str, int] = {}
            for sec in values.values():
                typ = str(sec.get(".type") or "?")
                types[typ] = types.get(typ, 0) + 1
            uci_summary = {"package": name, "sections": len(values), "types": types}
        except DeviceCommandError:
            uci_summary = None
        payload["uci"] = uci_summary
        payload["lifecycle"] = {
            "start": f"openwrt service start {name} --yes",
            "stop": f"openwrt service stop {name} --yes",
            "restart": f"openwrt service restart {name} --yes",
        }
        payload["note"] = t("svc.note.no_uci") if uci_summary is None else t("svc.note.summary")
        return CommandResult.ok_data(payload, transport=self.device.transport, kind="service")

    def action(self, name: str, action: str) -> CommandResult:
        self.device.require(Capability.INITD)
        if Capability.SHELL in self.device.capabilities:
            out = self.device.shell.exec(f"/etc/init.d/{name} {action} 2>&1")
        else:
            try:
                self.device.ubus.call("rc", "init", {"name": name, "action": action})
                out = ""
            except DeviceCommandError as e:
                return CommandResult.fail(str(e), transport=self.device.transport)
        return CommandResult.ok_data(
            {"service": name, "action": action, "output": out.strip() if isinstance(out, str) else ""},
            transport=self.device.transport,
            message=t("msg.svc_action", name=name, action=action),
        )


def _parse_init_start(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in (raw or "").splitlines():
        if ":START=" not in line:
            continue
        path, _, val = line.partition(":START=")
        name = path.rsplit("/", 1)[-1].strip()
        try:
            out[name] = int(val.strip())
        except ValueError:
            continue
    return out
