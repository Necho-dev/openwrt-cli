from __future__ import annotations

from typing import Any, Protocol


class UbusChannel(Protocol):
    def call(self, obj: str, method: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> Any:
        ...
