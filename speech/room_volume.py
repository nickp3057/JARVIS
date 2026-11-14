"""The room speaker's volume, by voice, with a quieter night mode.

Levels are 0-10 like a smart speaker ("volume 4", "louder", "quieter"),
10% of the ALSA control each on its loudness-matched (``amixer -M``) scale. The card comes from ``AUDIO_OUTPUT_DEVICE``
(``plughw:1,0`` -> card 1), the control from ``ROOM_VOLUME_CONTROL``
(``Master``).

Night mode: during ``ROOM_QUIET_HOURS`` (``22-7``; empty turns it off) the
speaker uses the night level (``ROOM_NIGHT_VOLUME``, 4 until Nick changes it
at night by voice, or any time from the app's Settings via ``set_night_level``); outside them, the day level. The day level is whatever
the speaker was at the first time night mode kicked in, until Nick sets one.
Both live in ``memory/room_volume.json``. ``prepare()`` runs before the room
voice makes any sound, so the switch happens at the next sound, not on a
timer. Off Linux, or without ``amixer``, every call quietly does nothing.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parents[1] / "memory" / "room_volume.json"
_lock = threading.Lock()


def _card_args() -> list[str]:
    device = os.getenv("AUDIO_OUTPUT_DEVICE", "").strip()
    match = re.search(r"hw:(?:CARD=)?(\w+)", device)
    return ["-c", match.group(1)] if match else []


def _control() -> str:
    return os.getenv("ROOM_VOLUME_CONTROL", "Master").strip() or "Master"


def _scale_args() -> list[str]:
    """``-M`` = alsamixer's loudness-matched scale. Without it a percentage is
    raw hardware steps, which on the Surface's ALC274 made level 8 (80%)
    -12.75 dB, about a quarter as loud as 10. ``ROOM_VOLUME_MAPPED=0`` goes
    back to raw steps."""
    return [] if os.getenv("ROOM_VOLUME_MAPPED", "1").strip().lower() in {"0", "false", "no", "off"} else ["-M"]


def _amixer(*args: str) -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        done = subprocess.run(["amixer", *_scale_args(), *_card_args(), *args],
                              capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        log.debug("amixer failed", exc_info=True)
        return None
    return done.stdout if done.returncode == 0 else None


def _read_level() -> int | None:
    out = _amixer("sget", _control())
    match = re.search(r"\[(\d{1,3})%\]", out or "")
    return round(int(match.group(1)) / 10) if match else None


def _apply(level: int) -> None:
    _amixer("-q", "sset", _control(), f"{level * 10}%")


def quiet_now(now: datetime | None = None) -> bool:
    """Inside ROOM_QUIET_HOURS ("22-7", wrapping midnight; "" = never)."""
    spec = os.getenv("ROOM_QUIET_HOURS", "22-7").strip()
    match = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})", spec)
    if not match:
        return False
    start, end, hour = int(match.group(1)) % 24, int(match.group(2)) % 24, (now or datetime.now()).hour
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def _mode(now: datetime | None = None) -> str:
    return "night" if quiet_now(now) else "day"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(_FILE)  # atomic: a crash never leaves half a file
    except OSError:
        log.warning("Could not save %s", _FILE, exc_info=True)


def _alarm_default() -> int:
    try:
        return max(0, min(10, int(float(os.getenv("ROOM_ALARM_VOLUME", "8") or 8))))
    except ValueError:
        return 8


def ring_alarm(now: datetime | None = None) -> int:
    """Wake-up backup ring: the speaker at ``ROOM_ALARM_VOLUME`` (8) whatever
    the hour, never the night level. The next ``prepare()`` puts this time
    of day's level back (the day level is remembered first if none is stored
    yet, so the morning doesn't stay at alarm volume)."""
    target = _alarm_default()
    with _lock:
        data = _load()
        mode = _mode(now)
        if data.get("applied") == ["alarm", target]:
            return target
        if mode == "day" and data.get("day") is None:
            current = _read_level()
            if current is not None:
                data["day"] = current
        _apply(target)
        data["applied"] = ["alarm", target]
        _save(data)
    return target


def _night_default() -> int:
    try:
        return max(0, min(10, int(os.getenv("ROOM_NIGHT_VOLUME", "4"))))
    except ValueError:
        return 4


def level(now: datetime | None = None) -> int | None:
    """The level the speaker is (or will be) at now."""
    with _lock:
        data = _load()
    mode = _mode(now)
    stored = data.get(mode)
    if mode == "night" and stored is None:
        return _night_default()
    return stored if stored is not None else _read_level()


def prepare(now: datetime | None = None) -> None:
    """Put the speaker at this time of day's level, if it isn't already."""
    with _lock:
        data = _load()
        mode = _mode(now)
        if mode == "night" and data.get("day") is None:
            current = _read_level()
            if current is not None:
                data["day"] = current  # so mornings go back to how Nick had it
        target = data.get(mode, _night_default() if mode == "night" else None)
        if target is None or data.get("applied") == [mode, target]:
            if data != _load():
                _save(data)
            return
        _apply(int(target))
        data["applied"] = [mode, target]
        _save(data)


def night_level() -> int:
    """The level quiet hours use (stored, else ``ROOM_NIGHT_VOLUME``)."""
    with _lock:
        stored = _load().get("night")
    return int(stored) if stored is not None else _night_default()


def set_night_level(value: int, now: datetime | None = None) -> int:
    """Set the night level from any hour (the app's Settings); applied now
    only if it is night, otherwise at the next quiet-hours sound."""
    value = max(0, min(10, int(value)))
    if _mode(now) == "night":
        return set_level(value, now)
    with _lock:
        data = _load()
        data["night"] = value
        _save(data)
    return value


def set_level(value: int, now: datetime | None = None) -> int:
    """Set this time of day's level (0-10) and apply it now."""
    value = max(0, min(10, int(value)))
    with _lock:
        data = _load()
        mode = _mode(now)
        data[mode] = value
        data["applied"] = [mode, value]
        _apply(value)
        _save(data)
    return value


def step(delta: int, now: datetime | None = None) -> int:
    current = level(now)
    return set_level((current if current is not None else 5) + delta, now)
