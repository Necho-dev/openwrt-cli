from __future__ import annotations

from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult


class BackupService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def create(self, output: str = "/tmp/openwrt-backup.tar.gz", exclude: list[str] | None = None) -> CommandResult:
        self.device.require(Capability.SHELL)
        exclude = exclude or []
        exclude_args = " ".join(f"--exclude={e}" for e in exclude)
        cmd = (
            f"sysupgrade -b {output} 2>/dev/null || "
            f"tar -czf {output} {exclude_args} "
            f"/etc/config/ /etc/crontabs/ /etc/passwd /etc/group "
            f"/etc/shadow /etc/dropbear/ /etc/firewall.user "
            f"/etc/rc.local /etc/sysctl.conf /etc/hotplug.d/ "
            f"2>/dev/null"
        )
        out = self.device.shell.exec(cmd, timeout=60)
        sha256 = ""
        if self.device.fs.exists(output):
            sha256 = self.device.shell.exec(f"sha256sum {output}").strip().split()[0]
        size = self.device.shell.exec(f"ls -lh {output} 2>/dev/null").strip()
        return CommandResult.ok_data(
            {"backup_file": output, "size": size, "sha256": sha256, "output": out.strip()},
            transport=self.device.transport,
            message=t("msg.backup_written", path=output),
        )

    def restore(self, backup_file: str) -> CommandResult:
        self.device.require(Capability.SHELL)
        if not self.device.fs.exists(backup_file):
            return CommandResult.fail(t("err.file_missing", path=backup_file), transport=self.device.transport)
        out = self.device.shell.exec(f"sysupgrade -r {backup_file} 2>&1", timeout=60)
        return CommandResult.ok_data(
            {"backup_file": backup_file, "output": out.strip()},
            transport=self.device.transport,
            message=t("msg.backup_restored"),
        )

    def list(self) -> CommandResult:
        self.device.require(Capability.SHELL)
        raw = self.device.shell.exec("find /tmp -name '*.tar.gz' -mtime -7 2>/dev/null | sort")
        files = []
        for path in raw.strip().splitlines():
            info = self.device.shell.exec(f"ls -lh {path} 2>/dev/null").strip()
            files.append({"path": path, "info": info})
        return CommandResult.ok_data({"recent_backups": files}, transport=self.device.transport, kind="backups")
