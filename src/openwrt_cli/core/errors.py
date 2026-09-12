"""Device connection and command errors."""


class DeviceConnectionError(Exception):
    """SSH / HTTP 连接失败。"""


class DeviceCommandError(Exception):
    """远程命令或 ubus 调用失败。"""


class CapabilityError(DeviceCommandError):
    """当前传输缺少执行该操作所需的能力。"""

    def __init__(self, message: str, missing: tuple[str, ...] = (), hint: str | None = None):
        self.missing = missing
        self.hint = hint
        super().__init__(message)


# 旧名别名，避免外部脚本立刻炸掉
SSHConnectionError = DeviceConnectionError
SSHCommandError = DeviceCommandError
ConnectionError = DeviceConnectionError
CommandError = DeviceCommandError
