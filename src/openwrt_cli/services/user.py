from __future__ import annotations

from pathlib import Path

from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.ssh_key import (
    DEFAULT_KEY_PATH,
    REMOTE_AUTH_FILES,
    install_remote_pubkey,
    load_or_create_default_pubkey,
    normalize_openssh_pubkey,
)
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult


class UserService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def list(self) -> CommandResult:
        self.device.require(Capability.FILE_READ)
        raw = self.device.fs.read("/etc/passwd")
        users = []
        for line in raw.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 7:
                users.append({
                    "username": parts[0],
                    "password": parts[1],
                    "uid": parts[2],
                    "gid": parts[3],
                    "gecos": parts[4],
                    "home": parts[5],
                    "shell": parts[6],
                })
        return CommandResult.ok_data({"users": users}, transport=self.device.transport, kind="users")

    def login_users(self) -> list[str]:
        result = self.list()
        names = []
        for u in (result.data or {}).get("users") or []:
            if u.get("shell") not in ("/bin/false", "/usr/sbin/nologin", "/sbin/nologin"):
                names.append(u["username"])
        return names

    def groups(self, username: str | None = None) -> CommandResult:
        self.device.require(Capability.SHELL)
        if username:
            raw = self.device.shell.exec(f"groups {username} 2>/dev/null || cat /etc/group | grep {username}")
        else:
            raw = self.device.shell.exec("cat /etc/group")
        return CommandResult.ok_data({"raw": raw}, transport=self.device.transport)

    def add(self, username: str, password: str, groups: str | None = None) -> CommandResult:
        self.device.require(Capability.SHELL)
        grp = groups.replace(",", ",") if groups else ""
        extra = f"-G {grp}" if grp else ""
        self.device.shell.exec(f"mkdir -p /home/{username} && useradd -m -s /bin/ash {extra} {username}", check=False)
        out = self.device.shell.exec(f'echo "{username}:{password}" | chpasswd -c SHA512 2>&1')
        return CommandResult.ok_data(
            {"username": username, "groups": groups or "", "output": out.strip()},
            transport=self.device.transport,
            message=t("msg.user_created", name=username),
        )

    def passwd(self, username: str, password: str) -> CommandResult:
        self.device.require(Capability.SHELL)
        out = self.device.shell.exec(f'echo "{username}:{password}" | chpasswd -c SHA512 2>&1')
        if "error" in out.lower() or "failed" in out.lower():
            return CommandResult.fail(t("msg.user_passwd_fail", error=out), transport=self.device.transport)
        return CommandResult.ok_data(
            {"username": username, "output": out.strip()},
            transport=self.device.transport,
            message=t("msg.user_passwd", name=username),
        )

    def delete(self, username: str) -> CommandResult:
        self.device.require(Capability.SHELL)
        out = self.device.shell.exec(f"userdel {username} 2>&1 && rm -rf /home/{username}")
        return CommandResult.ok_data(
            {"username": username, "output": out.strip()},
            transport=self.device.transport,
            message=t("msg.user_deleted", name=username),
        )

    def key_list(self) -> CommandResult:
        self.device.require(Capability.FILE_READ)
        keys = []
        for path in REMOTE_AUTH_FILES:
            try:
                if not self.device.fs.exists(path):
                    continue
                raw = self.device.fs.read(path)
            except Exception:
                continue
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    keys.append({"file": path, "key": normalize_openssh_pubkey(line)})
                except ValueError:
                    keys.append({"file": path, "key": line, "warning": t("warn.key_format")})
        return CommandResult.ok_data({"keys": keys, "count": len(keys)}, transport=self.device.transport, kind="keys")

    def key_add(self, key_file: str | None = None) -> CommandResult:
        self.device.require(Capability.SHELL)
        created = False
        private_path = None
        if key_file:
            pub_file = Path(key_file).expanduser()
            if pub_file.suffix != ".pub" and Path(str(pub_file) + ".pub").exists():
                pub_file = Path(str(pub_file) + ".pub")
            if not pub_file.exists():
                return CommandResult.fail(t("err.pubkey_missing", path=pub_file), transport=self.device.transport)
            pubkey = normalize_openssh_pubkey(pub_file.read_text(encoding="utf-8"))
            if str(pub_file).endswith(".pub"):
                private_path = Path(str(pub_file)[:-4])
        else:
            pubkey, private_path, created = load_or_create_default_pubkey()
        written = install_remote_pubkey(self.device.shell, pubkey)
        data = {"pubkey": pubkey, "generated": created, "installed": written}
        if private_path:
            data["identity_file"] = str(private_path)
        if created:
            data["hint"] = t("msg.key_generated", path=DEFAULT_KEY_PATH)
        return CommandResult.ok_data(data, transport=self.device.transport, message=t("msg.key_installed"))
