"""Thin optional luci-app tabs. Not a plugin framework — next app copies this hook."""

from __future__ import annotations

from collections.abc import Iterator

from textual.app import ComposeResult

from openwrt_cli.core.device import DeviceClient
from openwrt_cli.i18n import t


class OptionalApp:
    id: str = ""

    def available(self) -> bool:
        return False

    def tab_title(self) -> str:
        return self.id

    def compose(self) -> ComposeResult:
        return
        yield


class PassWall2App(OptionalApp):
    id = "passwall2"

    def __init__(self, device: DeviceClient):
        self.device = device
        self._ok: bool | None = None

    def available(self) -> bool:
        if self._ok is None:
            try:
                from openwrt_cli.services.passwall2 import PassWall2Service
                self._ok = PassWall2Service(self.device).available()
            except Exception:
                self._ok = False
        return self._ok

    def tab_title(self) -> str:
        return t("tab.passwall2")

    def compose(self) -> Iterator:
        from openwrt_cli.tui.screens.passwall2 import compose_passwall2
        yield from compose_passwall2()


def probe_optional_apps(device: DeviceClient) -> list[OptionalApp]:
    apps: list[OptionalApp] = []
    try:
        pw2 = PassWall2App(device)
        if pw2.available():
            apps.append(pw2)
    except Exception:
        pass
    return apps
