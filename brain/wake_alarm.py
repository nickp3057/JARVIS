"""Nick's wake-up alarm: music on the TV at a set time on chosen days.

One alarm, kept in ``memory/wake_alarm.json``. Nick sets it by talking to
JARVIS (``set_wake_up`` / ``cancel_wake_up`` / ``get_wake_up``); the runtime's
wake monitor publishes a ``wake_up`` bus event (``push=False``, so the phone
stays quiet) at the alarm time, and the TV, which follows the event stream,
turns itself on and plays the music. Music is a Spotify link or URI
(playlist, album or track), or empty to resume whatever Spotify last played.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import threading
from typing import Any

_FILE = Path(__file__).resolve().parents[1] / "memory" / "wake_alarm.json"
_lock = threading.Lock()
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SPOTIFY_LINK = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(playlist|album|track|artist)/([A-Za-z0-9]+)")
_SPOTIFY_URI = re.compile(r"^spotify:(playlist|album|track|artist):[A-Za-z0-9]+$")


def load() -> dict[str, Any] | None:
    try:
        data = json.loads(_FILE.read_text())
        return data if isinstance(data, dict) and data.get("time") else None
    except (OSError, ValueError):
        return None


def _save(data: dict[str, Any] | None) -> None:
    with _lock:
        if data is None:
            _FILE.unlink(missing_ok=True)
            return
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_FILE)


def parse_time(text: str) -> str:
    """"7", "7:30", "7:30am", "19:05" -> "HH:MM" (24 h); ValueError otherwise."""
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*", text.lower())
    if not m:
        raise ValueError(f"I couldn't read {text!r} as a time.")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        if not 1 <= hour <= 12:
            raise ValueError(f"{text!r} isn't a valid time.")
        hour = hour % 12 + (12 if m.group(3).startswith("p") else 0)
    if hour > 23 or minute > 59:
        raise ValueError(f"{text!r} isn't a valid time.")
    return f"{hour:02d}:{minute:02d}"


def parse_days(text: str) -> list[int]:
    """"weekdays", "every day", "mon,wed,fri", "weekends" -> weekday numbers (Mon=0)."""
    t = (text or "every day").lower()
    if "weekday" in t:
        return [0, 1, 2, 3, 4]
    if "weekend" in t:
        return [5, 6]
    if "every" in t or "daily" in t or t.strip() in ("", "all"):
        return list(range(7))
    days = sorted({i for i, name in enumerate(DAY_NAMES) if name in t})
    if not days:
        raise ValueError(f"I couldn't tell which days {text!r} means.")
    return days


def normalize_music(music: str) -> str:
    """A Spotify link or URI -> ``spotify:<kind>:<id>``; empty means resume."""
    music = (music or "").strip()
    if not music or music.lower() in ("resume", "last played", "whatever was playing"):
        return ""
    if _SPOTIFY_URI.match(music):
        return music
    m = _SPOTIFY_LINK.search(music)
    if m:
        return f"spotify:{m.group(1)}:{m.group(2)}"
    raise ValueError("I need a Spotify link for that (Share > Copy link in Spotify), or say resume.")


def set_alarm(time_text: str, days_text: str = "every day", music: str = "", label: str = "",
              volume: int = 30) -> dict[str, Any]:
    data = {
        "time": parse_time(time_text),
        "days": parse_days(days_text),
        "music": normalize_music(music),
        "label": (label or "").strip()[:80],
        "volume": max(5, min(100, int(volume))),
        "enabled": True,
        "last_fired": "",
    }
    _save(data)
    return data


def cancel() -> bool:
    existed = load() is not None
    _save(None)
    return existed


def next_time(data: dict[str, Any], now: datetime | None = None) -> datetime | None:
    """The next moment the alarm goes off (local time), or None if it's off."""
    if not data or not data.get("enabled") or not data.get("days"):
        return None
    now = now or datetime.now()
    hour, minute = (int(x) for x in data["time"].split(":"))
    for offset in range(8):
        day = now + timedelta(days=offset)
        at = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at.weekday() in data["days"] and at > now:
            return at
    return None


def due(now: datetime | None = None) -> dict[str, Any] | None:
    """The alarm if it should fire now (within 10 minutes of its time, once a day)."""
    data = load()
    now = now or datetime.now()
    if not data or not data.get("enabled") or now.weekday() not in data.get("days", []):
        return None
    hour, minute = (int(x) for x in data["time"].split(":"))
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not at <= now < at + timedelta(minutes=10) or data.get("last_fired") == now.date().isoformat():
        return None
    data["last_fired"] = now.date().isoformat()
    _save(data)
    return data


def days_text(days: list[int]) -> str:
    if sorted(days) == list(range(7)):
        return "every day"
    if sorted(days) == [0, 1, 2, 3, 4]:
        return "weekdays"
    if sorted(days) == [5, 6]:
        return "weekends"
    return ", ".join(DAY_NAMES[d].title() for d in sorted(days))


def describe(data: dict[str, Any] | None = None) -> str:
    data = data if data is not None else load()
    if not data:
        return "No wake-up alarm is set, sir."
    when = datetime.strptime(data["time"], "%H:%M").strftime("%-I:%M %p")
    music = data.get("label") or ("the Spotify link you gave me" if data.get("music") else "whatever Spotify last played")
    return f"Wake-up at {when}, {days_text(data['days'])}, with {music} on the TV."


def public(now: datetime | None = None) -> dict[str, Any] | None:
    """For /api/glance: what the TV shows on its wake-up tile."""
    data = load()
    if not data:
        return None
    at = next_time(data, now)
    return {
        "time": data["time"],
        "days": days_text(data["days"]),
        "label": data.get("label", ""),
        "next_at": at.timestamp() if at else None,
    }
