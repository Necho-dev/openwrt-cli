from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.errors import DeviceCommandError
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult

SOURCE_KLOG = 0
_POLL_SEC = 1.5


class SystemService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def info(self) -> CommandResult:
        data = self.device.ubus.call("system", "info")
        board = self.device.ubus.call("system", "board")
        hostname = board.get("hostname") or ""
        release = board.get("release") or {}
        uname = f"Linux {hostname} {board.get('kernel', '')} {board.get('system', '')} {release.get('distribution', 'OpenWrt')}"
        return CommandResult.ok_data(
            {"system_info": data, "hostname": hostname, "uname": uname, "board": board},
            transport=self.device.transport,
            kind="info",
        )

    def board(self) -> CommandResult:
        data = self.device.ubus.call("system", "board")
        return CommandResult.ok_data({"board": data}, transport=self.device.transport, kind="board")

    def hostname(self, new_hostname: str | None = None) -> CommandResult:
        current = ""
        try:
            current = self.device.uci.get("system.@system[0].hostname")
        except (DeviceCommandError, KeyError):
            board = self.device.ubus.call("system", "board")
            current = board.get("hostname") or ""
        if not new_hostname:
            return CommandResult.ok_data(
                {"hostname": current, "source": "uci"},
                transport=self.device.transport,
                kind="hostname",
            )
        self.device.uci.set("system.@system[0].hostname", new_hostname)
        self.device.uci.commit("system")
        if Capability.SHELL in self.device.capabilities:
            self.device.shell.exec(f"hostname {new_hostname}")
            try:
                self.device.shell.exec("/etc/init.d/system reload 2>&1")
            except DeviceCommandError:
                pass
        return CommandResult.ok_data(
            {"old_hostname": current, "new_hostname": new_hostname},
            transport=self.device.transport,
            message=t("msg.hostname_set", name=new_hostname),
        )

    def reboot(self) -> CommandResult:
        try:
            self.device.ubus.call("system", "reboot")
        except DeviceCommandError:
            if Capability.SHELL in self.device.capabilities:
                self.device.shell.exec("reboot")
            else:
                raise
        return CommandResult.ok_data(
            {"action": "reboot"},
            transport=self.device.transport,
            message=t("msg.rebooting"),
        )

    def shutdown(self) -> CommandResult:
        try:
            self.device.ubus.call("system", "poweroff")
        except DeviceCommandError:
            if Capability.SHELL in self.device.capabilities:
                self.device.shell.exec("poweroff")
            else:
                raise
        return CommandResult.ok_data(
            {"action": "shutdown"},
            transport=self.device.transport,
            message=t("msg.shutting_down"),
        )

    def logs(
        self,
        *,
        kernel: bool = False,
        lines: int | None = None,
        tail: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CommandResult:
        n = tail if tail is not None else (lines if lines is not None else 200)
        entries = self._collect(kernel=kernel, tail=n, since=since, until=until)
        kind = "dmesg" if kernel else "syslog"
        return CommandResult.ok_data(
            {"source": kind, "entries": entries, "count": len(entries)},
            transport=self.device.transport,
            kind="logs",
        )

    def follow_logs(
        self,
        *,
        kernel: bool = False,
        tail: int = 200,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[dict[str, Any]]:
        """先吐出符合条件的最近若干条，再持续产出新行。

        系统日志：SSH 走 logread -f，HTTP 轮询 log.read。
        内核日志：对齐 LuCI，轮询 dmesg（busybox 没有 dmesg -w）。
        """
        if kernel:
            yield from self._poll_follow(kernel=True, tail=tail, since=since, until=until)
            return
        iter_fn = getattr(self.device.shell, "iter", None) if Capability.SHELL in self.device.capabilities else None
        if iter_fn:
            cmd = f"logread -l {int(max(tail, 8000) if (since or until) else tail)} -f 2>/dev/null || logread -f"
            for line in iter_fn(cmd):
                if not line:
                    continue
                entry = _line_to_entry(line)
                decision = _time_decision(entry, since=since, until=until)
                if decision == "after":
                    return
                if decision == "skip":
                    continue
                yield entry
            return
        yield from self._poll_follow(kernel=False, tail=tail, since=since, until=until)

    def _poll_follow(
        self,
        *,
        kernel: bool,
        tail: int,
        since: datetime | None,
        until: datetime | None,
    ) -> Iterator[dict[str, Any]]:
        seen: set[Any] = set()
        for entry in self._collect(kernel=kernel, tail=tail, since=since, until=until):
            seen.add(_entry_key(entry))
            yield entry
        while True:
            if until is not None and datetime.now() > until:
                return
            time.sleep(_POLL_SEC)
            for entry in self._read_entries(kernel=kernel, lines=max(tail, 400)):
                key = _entry_key(entry)
                if key in seen:
                    continue
                seen.add(key)
                if len(seen) > 4000:
                    seen = set(list(seen)[-2000:])
                decision = _time_decision(entry, since=since, until=until)
                if decision == "after":
                    return
                if decision == "skip":
                    continue
                yield entry

    def _collect(
        self,
        *,
        kernel: bool,
        tail: int,
        since: datetime | None,
        until: datetime | None,
    ) -> list[dict[str, Any]]:
        fetch = max(tail, 8000) if (since is not None or until is not None) else tail
        entries = self._read_entries(kernel=kernel, lines=fetch)
        entries = [e for e in entries if _time_decision(e, since=since, until=until) == "keep"]
        return entries[-tail:]

    def _read_entries(self, *, kernel: bool, lines: int) -> list[dict[str, Any]]:
        if kernel:
            return self._read_kernel(lines)
        try:
            data = self.device.ubus.call("log", "read", {"stream": False, "lines": lines})
            return list(data.get("log") or [])
        except DeviceCommandError:
            pass
        if Capability.SHELL not in self.device.capabilities:
            raise DeviceCommandError(t("err.logs_http"))
        raw = self.device.shell.exec(f"logread -l {int(lines)} 2>/dev/null || logread 2>/dev/null")
        rows = raw.strip().splitlines()[-lines:]
        return [{"msg": line} for line in rows if line]

    def _read_kernel(self, lines: int) -> list[dict[str, Any]]:
        raw = self._dmesg_text()
        if raw is not None:
            return _dmesg_to_entries(raw)[-lines:]
        try:
            data = self.device.ubus.call("log", "read", {"stream": False, "lines": max(lines, 20000)})
            entries = [e for e in (data.get("log") or []) if _is_kernel_entry(e)]
            return entries[-lines:]
        except DeviceCommandError:
            pass
        raise DeviceCommandError(t("err.klog"))

    def _dmesg_text(self) -> str | None:
        if Capability.SHELL in self.device.capabilities:
            raw = self.device.shell.exec("dmesg -r 2>/dev/null || dmesg 2>/dev/null")
            return raw
        for params in (
            {"command": "/bin/dmesg", "params": ["-r"]},
            {"command": "/bin/dmesg"},
        ):
            try:
                data = self.device.ubus.call("file", "exec", params)
            except DeviceCommandError:
                continue
            stdout = data.get("stdout")
            if stdout:
                return str(stdout)
            if data.get("code") in (0, None) and stdout == "":
                return ""
        return None


_RELATIVE = re.compile(r"^(\d+)\s*([smhd])$", re.I)
_LOGREAD_STAMP = re.compile(
    r"^([A-Z][a-z]{2} [A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2} \d{4})"
)
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%H:%M:%S",
    "%H:%M",
)


def parse_log_time(value: str, *, now: datetime | None = None) -> datetime:
    """Parse --since / --until: 10m / 2h, Unix timestamp, or calendar time."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError(t("err.time_empty"))
    now = now or datetime.now()
    rel = _RELATIVE.fullmatch(raw)
    if rel:
        n = int(rel.group(1))
        unit = rel.group(2).lower()
        delta = {
            "s": timedelta(seconds=n),
            "m": timedelta(minutes=n),
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
        }[unit]
        return now - delta
    if raw.isdigit():
        t = int(raw)
        if t >= 10**11:
            t = t / 1000
        try:
            return datetime.fromtimestamp(t)
        except (OSError, OverflowError, ValueError) as e:
            raise ValueError(t("err.unix_ts")) from e
    for fmt in _TIME_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt in ("%H:%M:%S", "%H:%M"):
            parsed = parsed.replace(year=now.year, month=now.month, day=now.day)
        return parsed
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(t("err.time_format")) from e
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def format_log_line(entry: dict[str, Any]) -> str:
    msg = str(entry.get("msg") or "").rstrip()
    if _LOGREAD_STAMP.match(msg):
        return msg
    ts = entry.get("time")
    if ts:
        dt = _as_datetime(ts)
        if dt is not None:
            return f"{dt.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    return msg


def _line_to_entry(line: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "msg": line,
        "source": SOURCE_KLOG if _is_kernel_line(line) else 1,
    }
    dt = _parse_logread_stamp(line)
    if dt is not None:
        entry["time"] = int(dt.timestamp() * 1000)
    return entry


def _time_decision(
    entry: dict[str, Any],
    *,
    since: datetime | None,
    until: datetime | None,
) -> str:
    """keep / skip / after（已超过 --until，跟随时应停止）。"""
    if since is None and until is None:
        return "keep"
    dt = _entry_datetime(entry)
    if dt is None:
        return "keep"
    if since is not None and dt < since:
        return "skip"
    if until is not None and dt > until:
        return "after"
    return "keep"


def _entry_datetime(entry: dict[str, Any]) -> datetime | None:
    dt = _as_datetime(entry.get("time"))
    if dt is not None:
        return dt
    return _parse_logread_stamp(str(entry.get("msg") or ""))


def _as_datetime(ts: Any) -> datetime | None:
    if ts in (None, "", 0, "0"):
        return None
    try:
        t = int(ts)
    except (TypeError, ValueError):
        return None
    if t >= 10**11:
        t = t / 1000
    try:
        return datetime.fromtimestamp(t)
    except (OSError, OverflowError, ValueError):
        return None


def _parse_logread_stamp(msg: str) -> datetime | None:
    m = _LOGREAD_STAMP.match(msg)
    if not m:
        return None
    try:
        _dow, mon, day, hms, year = m.group(1).split()
        month = _MONTHS[mon]
        hh, mm, ss = (int(x) for x in hms.split(":"))
        return datetime(int(year), month, int(day), hh, mm, ss)
    except (ValueError, KeyError):
        return None


_DMESG_PRIO = re.compile(r"^<[^>]+>")


def _dmesg_to_entries(raw: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        entries.append({"msg": _DMESG_PRIO.sub("", line), "source": SOURCE_KLOG})
    return entries


def _is_kernel_entry(entry: dict[str, Any]) -> bool:
    try:
        if int(entry.get("source", -1)) == SOURCE_KLOG:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if (int(entry.get("priority", -1)) >> 3) == 0:
            return True
    except (TypeError, ValueError):
        pass
    return _is_kernel_line(str(entry.get("msg") or ""))


def _is_kernel_line(line: str) -> bool:
    text = line.lstrip()
    return " kern." in f" {line}" or text.startswith("[") or text.startswith("<")


def _entry_key(entry: dict[str, Any]) -> Any:
    eid = entry.get("id")
    if eid not in (None, "", 0, "0"):
        return ("id", int(eid))
    return ("msg", entry.get("time"), entry.get("msg"))

