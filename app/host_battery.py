"""The battery of the machine Core runs on (the Surface Laptop), read from
Linux's ``/sys/class/power_supply``. Read-only; the companion's Settings ›
Laptop polls it through ``GET /api/host/battery``. A machine without a
battery (or a Mac dev box) reports ``present: false``."""
from __future__ import annotations

import socket
import time
from pathlib import Path

POWER_SUPPLY = Path("/sys/class/power_supply")


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _uptime_seconds() -> int | None:
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def status(root: Path = POWER_SUPPLY) -> dict[str, object]:
    battery = None
    plugged_in = None
    if root.is_dir():
        for supply in sorted(root.iterdir()):
            kind = _read(supply / "type")
            if kind == "Battery" and battery is None:
                battery = supply
            elif kind == "Mains":
                plugged_in = _read(supply / "online") == "1"
    payload: dict[str, object] = {"host": socket.gethostname(), "uptime_seconds": _uptime_seconds(),
                                  "checked_at": time.time(), "present": battery is not None}
    if battery is None:
        return payload
    capacity = _read(battery / "capacity")
    state = _read(battery / "status")  # Charging / Discharging / Not charging / Full
    if plugged_in is None:
        plugged_in = state in {"Charging", "Not charging", "Full"}
    payload.update({
        "percent": int(capacity) if capacity.isdigit() else None,
        "state": state.lower() or None,
        "plugged_in": plugged_in,
    })
    return payload
