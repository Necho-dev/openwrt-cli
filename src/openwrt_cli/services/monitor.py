from __future__ import annotations

import time
from typing import Any

from openwrt_cli.core.device import Capability, DeviceClient
from openwrt_cli.core.errors import DeviceCommandError
from openwrt_cli.i18n import t
from openwrt_cli.services.result import CommandResult


def _parse_uptime(seconds: float, idle: float = 0) -> dict[str, Any]:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    mins = int((seconds % 3600) // 60)
    return {
        "seconds": seconds,
        "human": f"{days}d {hours}h {mins}m",
        "idle_percent": min(100, idle / 10.0) if idle else None,
    }


def _kb_entry(num: int) -> dict[str, Any]:
    return {"raw": f"{num} kB", "mb": round(num / 1024, 1)}


class MonitorService:
    def __init__(self, device: DeviceClient):
        self.device = device

    def system(self, detail: bool = False) -> CommandResult:
        board = self.device.ubus.call("system", "board")
        info = self.device.ubus.call("system", "info")
        uptime = _parse_uptime(float(info.get("uptime") or 0))
        loads = [round(x / 65536.0, 2) for x in (info.get("load") or [0, 0, 0])]
        mem = info.get("memory") or {}

        def from_bytes(v) -> dict[str, Any]:
            return _kb_entry(int((v or 0) / 1024))

        data: dict[str, Any] = {
            "board": board,
            "uptime": uptime,
            "load_average": loads,
            "memory": {
                "total": from_bytes(mem.get("total")),
                "free": from_bytes(mem.get("free")),
                "available": from_bytes(mem.get("available")),
                "buffers": from_bytes(mem.get("buffered")),
                "cached": from_bytes(mem.get("cached")),
            },
        }
        if detail:
            data["system_info"] = info
            data["hostname"] = board.get("hostname")
        return CommandResult.ok_data(data, transport=self.device.transport, kind="system")

    def cpu(self) -> CommandResult:
        warnings: list[str] = []
        usage: float | None = None
        cpuinfo = ""
        top = ""
        if Capability.SHELL in self.device.capabilities:
            top = self.device.shell.exec("top -n1 2>/dev/null | head -15")
            cpuinfo = self.device.shell.exec(
                "cat /proc/cpuinfo 2>/dev/null | grep -E 'model name|cpu MHz|Processor|BogoMIPS' | head -10"
            )
            stat1 = self.device.shell.exec("cat /proc/stat")
            time.sleep(1)
            stat2 = self.device.shell.exec("cat /proc/stat")
            try:
                def parse_stat(s: str) -> tuple[int, int]:
                    fields = s.strip().splitlines()[0].split()
                    return sum(int(x) for x in fields[1:]), int(fields[4])
                t1, i1 = parse_stat(stat1)
                t2, i2 = parse_stat(stat2)
                usage = round((1 - (i2 - i1) / (t2 - t1)) * 100, 1) if (t2 - t1) > 0 else 0.0
            except (IndexError, ValueError, ZeroDivisionError):
                usage = None
        else:
            board = self.device.ubus.call("system", "board")
            cpuinfo = f"{board.get('system', '')} {board.get('model', '')}".strip()
            warnings.append(t("temp.cpu_http"))
        return CommandResult.ok_data(
            {"cpu_info": cpuinfo.strip(), "usage_percent": usage, "top_snapshot": top.strip()},
            transport=self.device.transport,
            kind="cpu",
            warnings=warnings,
            degraded=bool(warnings),
        )

    def memory(self) -> CommandResult:
        info = self.device.ubus.call("system", "info")
        mem = info.get("memory") or {}
        swap = info.get("swap") or {}

        def from_bytes(v) -> dict[str, Any]:
            return _kb_entry(int((v or 0) / 1024))

        return CommandResult.ok_data({
            "total": from_bytes(mem.get("total")),
            "free": from_bytes(mem.get("free")),
            "available": from_bytes(mem.get("available")),
            "buffers": from_bytes(mem.get("buffered")),
            "cached": from_bytes(mem.get("cached")),
            "swap_total": from_bytes(swap.get("total")),
            "swap_free": from_bytes(swap.get("free")),
        }, transport=self.device.transport, kind="memory")

    def processes(self) -> CommandResult:
        total_mb = 0.0
        try:
            mem = (self.device.ubus.call("system", "info").get("memory") or {})
            total_mb = float(mem.get("total") or 0) / (1024 * 1024)
        except (DeviceCommandError, TypeError, ValueError):
            total_mb = 0.0
        if Capability.SHELL in self.device.capabilities:
            ps = self.device.shell.exec(
                "ps ax -o pid,user,pcpu,pmem,rss,args --cols=240 2>/dev/null || ps w"
            )
            rows = _parse_ps_table(ps, total_mb)
        else:
            data = self.device.ubus.call("luci", "getProcessList")
            rows = []
            for p in data.get("result") or []:
                rows.append(_process_row(
                    pid=p.get("PID", ""),
                    user=p.get("USER", ""),
                    cpu=p.get("%CPU", ""),
                    mem_pct=p.get("%MEM", ""),
                    rss_kb=None,
                    vsz=p.get("VSZ", ""),
                    command=p.get("COMMAND", ""),
                    total_mb=total_mb,
                    ppid=p.get("PPID", ""),
                    stat=p.get("STAT", ""),
                ))
        rows.sort(key=lambda r: (-r.get("cpu_pct", 0.0), -r.get("mem_mb", 0.0)))
        return CommandResult.ok_data({"processes": rows}, transport=self.device.transport, kind="processes")

    def disk(self) -> CommandResult:
        if Capability.SHELL in self.device.capabilities:
            df = self.device.shell.exec("df -h 2>/dev/null | grep -v 'tmpfs\\|overlay\\|udev'")
            mounts = []
            for line in df.strip().splitlines():
                parts = line.split()
                if len(parts) >= 6:
                    mounts.append({
                        "filesystem": parts[0], "size": parts[1], "used": parts[2],
                        "avail": parts[3], "use_pct": parts[4], "mounted": parts[5],
                    })
            return CommandResult.ok_data({"filesystems": mounts}, transport=self.device.transport, kind="disk")

        data = self.device.ubus.call("luci", "getMountPoints")
        mounts = []
        for m in data.get("result") or []:
            size = int(m.get("size") or 0)
            free = int(m.get("free") or m.get("avail") or 0)
            used = max(size - free, 0)
            pct = f"{int(used * 100 / size)}%" if size else "0%"
            mounts.append({
                "filesystem": m.get("device", "?"),
                "size": str(size),
                "used": str(used),
                "avail": str(free),
                "use_pct": pct,
                "mounted": m.get("mount", ""),
            })
        return CommandResult.ok_data({"filesystems": mounts}, transport=self.device.transport, kind="disk")

    def network_stats(self) -> CommandResult:
        from openwrt_cli.services.network import NetworkService
        return NetworkService(self.device).stats()

    def temperature(self) -> CommandResult:
        if Capability.SHELL not in self.device.capabilities:
            return CommandResult.ok_data(
                {"celsius": None, "note": t("temp.http")},
                transport=self.device.transport,
                kind="temperature",
                degraded=True,
                warnings=[t("temp.need_shell")],
            )
        paths = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/hwmon/hwmon0/temp1_input",
            "/sys/class/hwmon/hwmon1/temp1_input",
            "/sys/devices/virtual/thermal/thermal_zone0/temp",
        ]
        for path in paths:
            raw = self.device.shell.exec(f"cat {path} 2>/dev/null")
            if raw.strip().isdigit():
                return CommandResult.ok_data(
                    {"celsius": int(raw.strip()) / 1000.0, "source": path},
                    transport=self.device.transport,
                    kind="temperature",
                )
        return CommandResult.ok_data(
            {"celsius": None, "note": t("temp.unavailable")},
            transport=self.device.transport,
            kind="temperature",
        )

    def realtime(self, device: str) -> list[list] | None:
        """LuCI luci-bwc window for load / connections / iface. None if missing."""
        if device == "load":
            params: dict[str, Any] = {"mode": "load"}
        elif device in ("connections", "conntrack"):
            params = {"mode": "conntrack"}
        else:
            params = {"mode": "interface", "device": device}
        try:
            data = self.device.ubus.call("luci", "getRealtimeStats", params)
        except DeviceCommandError:
            try:
                data = self.device.ubus.call("luci", "getRealtimeStats", {"device": device})
            except DeviceCommandError:
                return None
        if isinstance(data, dict) and data.get("error"):
            return None
        rows = data.get("result", data) if isinstance(data, dict) else data
        if not isinstance(rows, list) or not rows:
            return None
        if not isinstance(rows[0], (list, tuple)):
            return None
        return [list(r) for r in rows]

    def conntrack_count(self) -> int | None:
        if Capability.FILE_READ in self.device.capabilities:
            try:
                raw = self.device.fs.read("/proc/sys/net/netfilter/nf_conntrack_count")
                return int(raw.strip().split()[0])
            except (DeviceCommandError, ValueError, IndexError, OSError):
                pass
        if Capability.SHELL in self.device.capabilities:
            raw = self.device.shell.exec(
                "cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null"
            )
            try:
                return int(raw.strip().split()[0])
            except (ValueError, IndexError):
                return None
        return None

    def uptime(self) -> CommandResult:
        info = self.device.ubus.call("system", "info")
        return CommandResult.ok_data(
            _parse_uptime(float(info.get("uptime") or 0)),
            transport=self.device.transport,
            kind="uptime",
        )

    def status(self) -> CommandResult:
        """Overview snapshot: host, load, memory, WAN, conntrack mix."""
        from openwrt_cli.services.network import NetworkService

        base = self.system(detail=False)
        blob = base.data or {}
        board = blob.get("board") or {}
        release = board.get("release") or {}
        stats = NetworkService(self.device).stats(with_rates=True)
        wan = None
        for iface in (stats.data or {}).get("interfaces") or []:
            label = (iface.get("role") or iface.get("label") or iface.get("name") or "").lower()
            if label == "wan" or label.startswith("wan"):
                wan = iface
                break
        conn = {"udp": None, "tcp": None, "other": None, "total": self.conntrack_count()}
        rows = self.realtime("connections")
        if rows and len(rows[-1]) >= 4:
            try:
                udp, tcp, other = float(rows[-1][1]), float(rows[-1][2]), float(rows[-1][3])
                conn = {"udp": int(udp), "tcp": int(tcp), "other": int(other), "total": int(udp + tcp + other)}
            except (TypeError, ValueError, IndexError):
                pass
        warnings = list(base.warnings or []) + list(stats.warnings or [])
        return CommandResult.ok_data(
            {
                "hostname": board.get("hostname") or "",
                "version": release.get("version") or "",
                "kernel": board.get("kernel") or "",
                "arch": release.get("target") or "",
                "cpu": board.get("system") or "",
                "load": blob.get("load_average") or [],
                "memory": blob.get("memory") or {},
                "uptime": blob.get("uptime") or {},
                "wan": {
                    "name": wan.get("name") or "",
                    "rx_bps": wan.get("rx_bps"),
                    "tx_bps": wan.get("tx_bps"),
                    "rx_rate": wan.get("rx_rate"),
                    "tx_rate": wan.get("tx_rate"),
                    "rx_human": wan.get("rx_human"),
                    "tx_human": wan.get("tx_human"),
                } if wan else {},
                "connections": conn,
                "board": board,
            },
            transport=self.device.transport,
            kind="status",
            warnings=warnings,
            degraded=base.degraded or stats.degraded,
        )


def _parse_num(raw) -> float:
    try:
        return float(str(raw or "0").replace("%", "").strip() or 0)
    except ValueError:
        return 0.0


def _parse_size_kb(raw) -> float:
    s = str(raw or "").strip().lower().replace(" ", "")
    if not s:
        return 0.0
    try:
        if s.endswith("g"):
            return float(s[:-1]) * 1024 * 1024
        if s.endswith("m"):
            return float(s[:-1]) * 1024
        if s.endswith("k"):
            return float(s[:-1])
        return float(s)
    except ValueError:
        return 0.0


def _process_name(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return "—"
    # 内核线程 [kworker/2:1-events] 取括号内第一段，避免变成 "2:1-events]" 或整段挤空白
    if cmd.startswith("[") and "]" in cmd:
        inner = cmd[1:cmd.index("]")].strip()
        head = inner.split("/", 1)[0].strip()
        return head or inner or "—"
    first = cmd.split(None, 1)[0]
    if first.startswith("-") and len(first) > 1:
        first = first[1:]
    return first.rsplit("/", 1)[-1] or "—"


def _process_row(
    *,
    pid,
    user,
    cpu,
    mem_pct,
    rss_kb,
    vsz,
    command,
    total_mb: float,
    ppid="",
    stat="",
) -> dict[str, Any]:
    cpu_pct = _parse_num(cpu)
    pct = _parse_num(mem_pct)
    if rss_kb not in (None, "", "0", 0):
        mb = _parse_num(rss_kb) / 1024.0
    elif total_mb > 0 and pct > 0:
        mb = total_mb * pct / 100.0
    else:
        mb = _parse_size_kb(vsz) / 1024.0
    cmd = str(command or "").strip()
    name = _process_name(cmd)
    if name == "—" and pid not in (None, ""):
        name = f"pid-{pid}"
    return {
        "pid": str(pid or ""),
        "ppid": str(ppid or ""),
        "name": name,
        "command": cmd,
        "user": str(user or ""),
        "stat": str(stat or "").strip(),
        "vsz": str(vsz or ""),
        "cpu_pct": cpu_pct,
        "mem_pct": pct,
        "mem_mb": mb,
        "cpu": f"{cpu_pct:.2f}%",
        "mem": f"{mb:.2f}MB({pct:.0f}%)",
        "comm": cmd,
    }


def _parse_ps_table(raw: str, total_mb: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = [ln for ln in (raw or "").splitlines() if ln.strip()]
    if not lines:
        return rows
    header = lines[0].lower()
    if "pid" in header and ("%cpu" in header or "pcpu" in header or "cpu" in header):
        body = lines[1:]
        for line in body:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            rows.append(_process_row(
                pid=parts[0], user=parts[1], cpu=parts[2], mem_pct=parts[3],
                rss_kb=parts[4], vsz=None, command=parts[5], total_mb=total_mb,
            ))
        return rows
    for line in lines[1:] if "pid" in header else lines:
        parts = line.split(None, 4)
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        rows.append(_process_row(
            pid=parts[0], user=parts[1], cpu=0, mem_pct=0,
            rss_kb=None, vsz=None, command=parts[-1], total_mb=total_mb,
        ))
    return rows
