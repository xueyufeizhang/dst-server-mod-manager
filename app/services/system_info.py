"""Host status for the Server page: memory, disk, load, uptime.

Standard library only. Linux (the deployment target) is read via /proc;
macOS (development) falls back to sysctl/vm_stat. Anything unavailable is
simply omitted rather than failing the page.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Meter:
    label: str
    detail: str
    percent: int  # 0-100


@dataclass
class SystemInfo:
    hostname: str
    now: str
    cpu_count: int | None
    load: str  # "0.52 / 0.60 / 0.65", "" if unavailable
    uptime: str  # "" if unavailable
    memory: Meter | None
    disks: list[Meter] = field(default_factory=list)


def human_bytes(num: float) -> str:
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _memory_linux() -> tuple[int, int]:
    data: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            data[parts[0].rstrip(":")] = int(parts[1]) * 1024  # values are in kB
    return data["MemTotal"], data["MemAvailable"]


def _memory_macos() -> tuple[int, int]:
    total = int(
        subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    )
    vm_stat = subprocess.run(
        ["vm_stat"], capture_output=True, text=True, timeout=5
    ).stdout
    page_size = int(re.search(r"page size of (\d+)", vm_stat).group(1))

    def pages(name: str) -> int:
        match = re.search(name + r":\s+(\d+)", vm_stat)
        return int(match.group(1)) if match else 0

    available = (
        pages("Pages free") + pages("Pages inactive") + pages("Pages speculative")
    ) * page_size
    return total, available


def get_memory() -> tuple[int, int] | None:
    """(total_bytes, available_bytes) or None when it can't be determined."""
    try:
        return _memory_linux()
    except (OSError, KeyError):
        pass
    try:
        return _memory_macos()
    except Exception:
        return None


def _uptime_seconds() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:  # macOS: kern.boottime = "{ sec = 1719900000, usec = 0 } ..."
        out = subprocess.run(
            ["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5
        ).stdout
        match = re.search(r"sec = (\d+)", out)
        if match:
            return time.time() - int(match.group(1))
    except Exception:
        pass
    return None


def _format_uptime(seconds: float) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def gather(paths: list[Path]) -> SystemInfo:
    """Collect host status; `paths` decides which filesystems to report."""
    memory: Meter | None = None
    mem = get_memory()
    if mem is not None:
        total, available = mem
        used = total - available
        memory = Meter(
            label="RAM",
            detail=(
                f"{human_bytes(used)} used / {human_bytes(total)} total "
                f"— {human_bytes(available)} available"
            ),
            percent=round(used / total * 100) if total else 0,
        )

    disks: list[Meter] = []
    seen_devices: set[int] = set()
    for path in paths:
        target = path if path.exists() else path.parent
        try:
            device = os.stat(target).st_dev
            if device in seen_devices:
                continue  # same filesystem already reported
            seen_devices.add(device)
            usage = shutil.disk_usage(target)
        except OSError:
            continue
        disks.append(
            Meter(
                label=f"Disk — {path}",
                detail=(
                    f"{human_bytes(usage.used)} used / {human_bytes(usage.total)} total "
                    f"— {human_bytes(usage.free)} free"
                ),
                percent=round(usage.used / usage.total * 100) if usage.total else 0,
            )
        )

    try:
        load = "%.2f / %.2f / %.2f" % os.getloadavg()
    except OSError:
        load = ""

    uptime = _uptime_seconds()
    return SystemInfo(
        hostname=socket.gethostname(),
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        cpu_count=os.cpu_count(),
        load=load,
        uptime=_format_uptime(uptime) if uptime else "",
        memory=memory,
        disks=disks,
    )
