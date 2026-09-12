from __future__ import annotations

from typing import Protocol


class FileChannel(Protocol):
    def exists(self, path: str) -> bool:
        ...

    def read(self, path: str) -> str:
        ...

    def write(self, path: str, content: str) -> None:
        ...
