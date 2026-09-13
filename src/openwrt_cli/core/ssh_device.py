"""SSH transport with all four channels enabled."""

from __future__ import annotations

import json
from typing import Any, Literal

from openwrt_cli.core.channels.uci import encode_uci_values, option_from_values, resolve_section, section_id_from_add
from openwrt_cli.core.device import Capability, DeviceBase
from openwrt_cli.core.errors import DeviceCommandError
from openwrt_cli.core.ssh_client import SSHClient
from openwrt_cli.i18n import t


def _uci_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


class _SSHUbus:
    def __init__(self, ssh: SSHClient):
        self._ssh = ssh

    def call(self, obj: str, method: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> Any:
        if params:
            cmd = f"ubus call {obj} {method} '{json.dumps(params, ensure_ascii=False)}'"
        else:
            cmd = f"ubus call {obj} {method}"
        raw = self._ssh.exec(cmd, timeout=timeout or 30)
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise DeviceCommandError(f"ubus {obj}.{method} 返回非 JSON:\n{raw[:400]}") from e


class _SSHUci:
    def __init__(self, ssh: SSHClient, ubus: _SSHUbus):
        self._ssh = ssh
        self._ubus = ubus

    def show(self, config: str) -> dict[str, dict[str, Any]]:
        try:
            data = self._ubus.call("uci", "get", {"config": config})
            values = data.get("values") or {}
            if values:
                return values
        except DeviceCommandError:
            pass
        return self._parse_show(self._ssh.exec(f"uci show {config} 2>/dev/null"))

    def get(self, path: str) -> str:
        parts = path.split(".")
        if len(parts) >= 3 and parts[1].startswith("@"):
            values = self.show(parts[0])
            return option_from_values(values, path)
        return self._ssh.exec(f"uci get {path} 2>/dev/null").strip()

    def set(self, path: str, value: str) -> None:
        self._ssh.exec(f"uci set {path}={_uci_quote(value)}")

    def set_values(self, config: str, section: str, values: dict[str, Any]) -> None:
        payload = encode_uci_values(values)
        if not payload:
            return
        section = self.resolve(config, section)
        try:
            self._ubus.call("uci", "set", {"config": config, "section": section, "values": payload})
            return
        except DeviceCommandError:
            pass
        for key, value in payload.items():
            path = f"{config}.{section}.{key}"
            if isinstance(value, list):
                self._ssh.exec(f"uci delete {path} 2>/dev/null || true")
                for item in value:
                    self._ssh.exec(f"uci add_list {path}={_uci_quote(item)}")
            else:
                self.set(path, str(value))

    def add(self, config: str, typ: str, values: dict[str, Any] | None = None, name: str | None = None) -> str:
        payload = encode_uci_values(values)
        try:
            params: dict[str, Any] = {"config": config, "type": typ}
            if name:
                params["name"] = name
            if payload:
                params["values"] = payload
            sid = section_id_from_add(self._ubus.call("uci", "add", params))
            if sid:
                return sid
        except DeviceCommandError:
            pass
        if name:
            self._ssh.exec(f"uci set {config}.{name}={_uci_quote(typ)}")
            sid = name
        else:
            sid = self._ssh.exec(f"uci add {config} {typ}").strip()
        if not sid:
            raise DeviceCommandError(t("err.uci_add", config=config, typ=typ))
        if payload:
            self.set_values(config, sid, payload)
        return sid

    def delete(
        self,
        config: str,
        section: str,
        option: str | None = None,
        options: list[str] | None = None,
    ) -> None:
        section = self.resolve(config, section)
        names = [item for item in (options or ([option] if option else [])) if item]
        try:
            params: dict[str, Any] = {"config": config, "section": section}
            if len(names) == 1:
                params["option"] = names[0]
            elif names:
                params["options"] = names
            self._ubus.call("uci", "delete", params)
            return
        except DeviceCommandError:
            pass
        if names:
            for key in names:
                self._ssh.exec(f"uci delete {config}.{section}.{key} 2>/dev/null || true")
            return
        self._ssh.exec(f"uci delete {config}.{section}")

    def commit(self, config: str | None = None) -> None:
        self._ssh.exec(f"uci commit {config}" if config else "uci commit")

    def resolve(self, config: str, section: str) -> str:
        if not section.startswith("@"):
            return section
        return resolve_section(self.show(config), section)

    @staticmethod
    def _parse_show(raw: str) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for line in raw.strip().splitlines():
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            parts = key.split(".")
            if len(parts) < 2:
                continue
            name = parts[1]
            values.setdefault(name, {})
            val = val.strip().strip("'\"")
            if len(parts) == 2:
                values[name][".type"] = val
                values[name][".name"] = name
            else:
                opt = parts[2]
                if opt in values[name] and not isinstance(values[name][opt], list):
                    values[name][opt] = [values[name][opt], val]
                elif isinstance(values[name].get(opt), list):
                    values[name][opt].append(val)
                else:
                    values[name][opt] = val
        return values


class _SSHShell:
    def __init__(self, ssh: SSHClient):
        self._ssh = ssh

    def exec(self, cmd: str, timeout: int = 30, check: bool = False) -> str:
        return self._ssh.exec(cmd, timeout=timeout, check=check)

    def iter(self, cmd: str):
        yield from self._ssh.exec_iter(cmd)


class _SSHFs:
    def __init__(self, ssh: SSHClient):
        self._ssh = ssh

    def exists(self, path: str) -> bool:
        return self._ssh.file_exists(path)

    def read(self, path: str) -> str:
        return self._ssh.read_file(path)

    def write(self, path: str, content: str) -> None:
        self._ssh.write_file(path, content)


class SSHDevice(DeviceBase):
    transport: Literal["ssh", "http"] = "ssh"
    capabilities = frozenset(Capability)

    def __init__(self, ssh: SSHClient):
        self._ssh = ssh
        self.host = ssh.host
        self.user = ssh.user
        self.port = ssh.port
        self._ubus = _SSHUbus(ssh)
        self._uci = _SSHUci(ssh, self._ubus)
        self._shell = _SSHShell(ssh)
        self._fs = _SSHFs(ssh)

    @classmethod
    def from_config(cls, cfg: dict) -> SSHDevice:
        return cls(
            SSHClient(
                host=cfg["host"],
                user=cfg.get("user", "root"),
                port=cfg.get("port", 22),
                password=cfg.get("password"),
                identity_file=cfg.get("identity_file"),
            )
        )

    @property
    def ubus(self) -> _SSHUbus:
        return self._ubus

    @property
    def uci(self) -> _SSHUci:
        return self._uci

    @property
    def shell(self) -> _SSHShell:
        return self._shell

    @property
    def fs(self) -> _SSHFs:
        return self._fs

    def keepalive(self) -> None:
        self._ssh.keepalive()

    def close(self) -> None:
        self._ssh.close()
