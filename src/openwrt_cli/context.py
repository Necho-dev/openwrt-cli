from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rich.console import Console

from openwrt_cli.core.connection import open_connection
from openwrt_cli.core.device import DeviceClient
from openwrt_cli.ui.console import get_console


@dataclass
class AppContext:
    cfg: dict
    format: Literal["text", "json", "compact"] = "text"
    yes: bool = False
    console: Console = field(default_factory=get_console)
    _client: DeviceClient | None = field(default=None, repr=False)

    @property
    def client(self) -> DeviceClient:
        if self._client is None:
            self._client = open_connection(self.cfg)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
