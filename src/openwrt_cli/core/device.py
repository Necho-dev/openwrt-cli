"""DeviceClient protocol and capability flags."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from openwrt_cli.core.errors import CapabilityError
from openwrt_cli.i18n import t


class Capability(StrEnum):
    UBUS = "ubus"
    UCI = "uci"
    SHELL = "shell"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    INITD = "initd"
    IPTABLES = "iptables"
    TC = "tc"


@runtime_checkable
class DeviceClient(Protocol):
    transport: Literal["ssh", "http"]
    capabilities: frozenset[Capability]
    host: str
    user: str

    @property
    def ubus(self):
        ...

    @property
    def uci(self):
        ...

    @property
    def shell(self):
        ...

    @property
    def fs(self):
        ...

    def require(self, *caps: Capability) -> None:
        ...

    def close(self) -> None:
        ...

    def __enter__(self):
        ...

    def __exit__(self, exc_type, exc, tb) -> None:
        ...


class DeviceBase:
    """SSH / HTTP 设备的共用能力检查与上下文管理。"""

    transport: Literal["ssh", "http"]
    capabilities: frozenset[Capability]
    host: str
    user: str

    def require(self, *caps: Capability) -> None:
        missing = tuple(c.value for c in caps if c not in self.capabilities)
        if not missing:
            return
        names = ", ".join(missing)
        hint = t("err.cap_hint")
        if self.transport == "http":
            hint = t("err.cap_http", names=names)
        raise CapabilityError(
            t("err.cap_missing", transport=self.transport, names=names, hint=hint),
            missing=missing,
            hint=hint,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def keepalive(self) -> None:
        """探活并在会话失效时恢复。默认无操作。"""

    def close(self) -> None:
        raise NotImplementedError
