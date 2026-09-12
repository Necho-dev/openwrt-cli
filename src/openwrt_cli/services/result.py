from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandResult:
    ok: bool
    data: Any = None
    message: str | None = None
    warnings: list[str] = field(default_factory=list)
    degraded: bool = False
    transport: str = ""
    kind: str | None = None

    @classmethod
    def ok_data(cls, data: Any, *, transport: str, kind: str | None = None, message: str | None = None, warnings: list[str] | None = None, degraded: bool = False) -> CommandResult:
        return cls(
            ok=True,
            data=data,
            message=message,
            warnings=warnings or [],
            degraded=degraded,
            transport=transport,
            kind=kind,
        )

    @classmethod
    def fail(cls, message: str, *, transport: str = "", data: Any = None) -> CommandResult:
        return cls(ok=False, message=message, data=data, transport=transport)
