from __future__ import annotations

from typing import Protocol


class ShellChannel(Protocol):
    def exec(self, cmd: str, timeout: int = 30, check: bool = False) -> str:
        ...
