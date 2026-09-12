"""Open an SSH or HTTP (ubus) DeviceClient from config."""

from openwrt_cli.core.errors import DeviceConnectionError
from openwrt_cli.core.http_client import HTTPDevice
from openwrt_cli.core.ssh_device import SSHDevice


def open_connection(cfg: dict):
    transport = (cfg.get("transport") or "ssh").lower()
    if transport == "http":
        return HTTPDevice.from_config(cfg)
    if transport == "auto":
        http_err = None
        try:
            return HTTPDevice.from_config(cfg)
        except Exception as e:
            http_err = e
        try:
            return SSHDevice.from_config(cfg)
        except Exception as e:
            raise DeviceConnectionError(f"HTTP 失败: {http_err}\nSSH 失败: {e}") from e
    return SSHDevice.from_config(cfg)
