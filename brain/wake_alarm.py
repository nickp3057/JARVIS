"""Nick's wake-up alarm: music on the TV, at its own time on each day.

A weekly schedule (a time, or none, per weekday) plus an optional one-off,
kept in ``memory/wake_alarm.json``. Nick sets it by talking to JARVIS
(``set_wake_up`` / ``cancel_wake_up`` / ``get_wake_up``) or in the companion
app's Wake-up editor (``GET``/``PUT /api/wake``); the runtime's
wake monitor publishes a ``wake_up`` bus event (``push=False``, so the phone
stays quiet) at the alarm time, and the TV, which follows the event stream,
turns itself on and plays the music. Music is a Spotify link or URI
(playlist, album or track), a search Spotify runs itself ("Back in Black
AC/DC", stored as ``search:...``), or empty to resume whatever Spotify last
played. A time like "in 3 minutes" makes the one-off unless days are given.
Music starts at the alarm's volume (no fade-in); the TV offers I'm up / Snooze.

The event alone can't wake a TV that dropped off Wi-Fi in standby, so Core
also powers it on through Home Assistant (``tv_remote.power``). If the TV
still isn't on ``WAKE_BACKUP_DELAY_S`` (90) later and Nick hasn't answered,
the backup ring starts: the room voice beeps the Optiplex speaker until I'm
up, Snooze, "stop" by voice, or ``WAKE_BACKUP_RING_S`` (600) runs out.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parents[1] / "memory" / "wake_alarm.json"
_lock = threading.Lock()
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SPOTIFY_LINK = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(playlist|album|track|artist)/([A-Za-z0-9]+)")
_SPOTIFY_URI = re.compile(r"^spotify:(playlist|album|track|artist):[A-Za-z0-9]+$")


def _blank() -> dict[str, Any]:
    return {"times": [None] * 7, "music": "", "label": "", "volume": 30,
            "enabled": True, "once": None, "overrides": {}, "last_fired": ""}


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """The pre-schedule file (one ``time`` on a list of ``days``) -> ``times``."""
    if "times" in data:
        return data
    new = _blank()
    for key in ("music", "label", "volume"):
        new[key] = data.get(key, new[key])
    if data.get("once"):
        if data.get("enabled", True) and data.get("days"):
            hour, minute = (int(x) for x in data["time"].split(":"))
            today = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
            at = today + timedelta(days=(data["days"][0] - today.weekday()) % 7)
            new["once"] = {"at": at.isoformat(timespec="minutes"), "music": None, "label": ""}
    else:
        for day in data.get("days", []):
            new["times"][day] = data["time"]
        new["enabled"] = bool(data.get("enabled", True))
    return new


def load() -> dict[str, Any] | None:
    """The schedule, or None when there is nothing on it at all."""
    try:
        data = json.loads(_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data = _migrate(data)
    times = data.get("times")
    if not isinstance(times, list) or len(times) != 7:
        data["times"] = [None] * 7
    today = datetime.now().date().isoformat()
    overrides = data.get("overrides") if isinstance(data.get("overrides"), dict) else {}
    data["overrides"] = {d: t for d, t in overrides.items() if d >= today}  # past days drop off
    return data if any(data["times"]) or data.get("once") or data["overrides"] else None


def _save(data: dict[str, Any] | None) -> None:
    with _lock:
        if data is None:
            _FILE.unlink(missing_ok=True)
            return
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_FILE)


_RELATIVE = re.compile(r"\s*in\s+(\d+)\s*(m|min|mins|minutes?|h|hrs?|hours?)\s*", re.IGNORECASE)


def relative_time(text: str, now: datetime | None = None) -> str | None:
    """"in 3 minutes" / "in 2 hours" -> "HH:MM" from now; None if not relative."""
    m = _RELATIVE.fullmatch(text or "")
    if not m:
        return None
    amount = int(m.group(1))
    delta = timedelta(hours=amount) if m.group(2).lower().startswith("h") else timedelta(minutes=amount)
    return ((now or datetime.now()) + delta).strftime("%H:%M")


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
    """A Spotify link or URI -> ``spotify:<kind>:<id>``; words -> ``search:<words>``
    (Spotify on the TV searches and plays the top hit); empty means resume."""
    music = (music or "").strip()
    if not music or music.lower() in ("resume", "last played", "whatever was playing"):
        return ""
    if _SPOTIFY_URI.match(music):
        return music
    if music.startswith("search:"):
        return music[:127]
    m = _SPOTIFY_LINK.search(music)
    if m:
        return f"spotify:{m.group(1)}:{m.group(2)}"
    return "search:" + music[:120]


def _one_day(when: str, now: datetime) -> str | None:
    """"today" / "tomorrow" / "YYYY-MM-DD" -> that date (ISO); None otherwise."""
    if when == "today":
        return now.date().isoformat()
    if when == "tomorrow":
        return (now + timedelta(days=1)).date().isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        datetime.strptime(when, "%Y-%m-%d")  # ValueError if it isn't a real date
        if when < now.date().isoformat():
            raise ValueError(f"{when} has already been.")
        return when
    return None


def set_alarm(time_text: str, days_text: str | None = None, music: str | None = None,
              label: str = "", volume: int | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Put a time on the schedule for some days (other days keep theirs), or
    set the one-off. ``music`` None keeps what the schedule already plays."""
    now = now or datetime.now()
    relative = relative_time(time_text, now)
    hhmm = relative or parse_time(time_text)
    when = (days_text or "").strip().lower()
    data = load() or _blank()
    music = None if music is None else normalize_music(music)
    label = (label or "").strip() or (music[7:] if music and music.startswith("search:") else "")
    day = None if relative is not None else _one_day(when, now)
    if day:
        # Just that one day; the week after, the schedule is as it was.
        data["overrides"][day] = hhmm
    elif when in ("once", "one time") or (relative is not None and not days_text):
        hour, minute = (int(x) for x in hhmm.split(":"))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        data["once"] = {"at": at.isoformat(timespec="minutes"), "music": music, "label": label[:80]}
    else:
        for day in parse_days(days_text or "every day"):
            data["times"][day] = hhmm
        data["enabled"] = True
        if music is not None:
            data["music"], data["label"] = music, label[:80]
    if volume is not None:
        data["volume"] = max(5, min(100, int(volume)))
    _save(data)
    return data


def set_schedule(times: list[str | None], music: str = "", label: str = "", volume: int = 30,
                 enabled: bool = True) -> dict[str, Any]:
    """Replace the weekly schedule (the companion app's editor); keeps the one-off."""
    if len(times) != 7:
        raise ValueError("A schedule needs a time (or none) for each of the 7 days.")
    data = load() or _blank()
    data["times"] = [parse_time(t) if t else None for t in times]
    music = normalize_music(music)
    data.update(music=music, label=((label or "").strip() or (music[7:] if music.startswith("search:") else ""))[:80],
                volume=max(5, min(100, int(volume))), enabled=bool(enabled))
    _save(data)
    return data


def skip(day_text: str = "tomorrow", restore: bool = False, now: datetime | None = None) -> str:
    """No alarm on one day (or, with ``restore``, that day back to normal).
    Returns the ISO date it applied to."""
    now = now or datetime.now()
    day = _one_day((day_text or "tomorrow").strip().lower(), now)
    if day is None:
        raise ValueError(f"I need today, tomorrow or a date like 2026-10-30, not {day_text!r}.")
    data = load() or _blank()
    if restore:
        data["overrides"].pop(day, None)
    else:
        data["overrides"][day] = ""
    _save(data)
    return day


SNOOZE_MINUTES = 9


# Backup ring state (process-wide): set when the alarm fires, cleared by an answer.
_answered = threading.Event()
_ring_until = 0.0
_ring_lock = threading.Lock()


def alarm_fired() -> None:
    """A new alarm went off: nobody has answered it yet."""
    _answered.clear()


def answered() -> bool:
    return _answered.is_set()


def start_backup_ring(now: float | None = None) -> bool:
    """Ring the room speaker (the room voice beeps while ``ringing()``),
    unless Nick already answered. False if it didn't start."""
    global _ring_until
    if _answered.is_set():
        return False
    try:
        seconds = max(30.0, float(os.getenv("WAKE_BACKUP_RING_S", "600")))
    except ValueError:
        seconds = 600.0
    with _ring_lock:
        _ring_until = (now or time.monotonic()) + seconds
    return True


def ringing(now: float | None = None) -> bool:
    return (now or time.monotonic()) < _ring_until


def stop_ring() -> bool:
    """Silence the backup ring; True if it was ringing."""
    global _ring_until
    with _ring_lock:
        was = ringing()
        _ring_until = 0.0
    return was


def _answer() -> None:
    _answered.set()
    stop_ring()


def tv_is_on() -> bool:
    """The room TV's remote entity reads "on" in Home Assistant."""
    from integrations import home_assistant, tv_remote
    ok, states = home_assistant.get_states()
    if not ok or not isinstance(states, list):
        return False
    entity = tv_remote.entity()
    return any(s.get("entity_id") == entity and s.get("state") == "on" for s in states)


def wake_tv_with_backup(stop: threading.Event) -> str:
    """Run off the monitor thread when an alarm fires: power the TV on, give
    it ``WAKE_BACKUP_DELAY_S`` to come up, then ring the speaker if it
    didn't and nobody answered. Returns what happened (for logs and tests)."""
    from integrations import tv_remote
    try:
        ok, detail = tv_remote.power(True)
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)
    if not ok:
        log.warning("Wake-up: couldn't power the TV on: %s", detail)
    try:
        delay = max(10.0, float(os.getenv("WAKE_BACKUP_DELAY_S", "90")))
    except ValueError:
        delay = 90.0
    if stop.wait(delay):
        return "stopped"
    if _answered.is_set():
        return "answered"
    try:
        on = tv_is_on()
    except Exception:  # noqa: BLE001
        on = False
    if on:
        return "tv"
    if not start_backup_ring():
        return "answered"
    log.warning("Wake-up: the TV didn't come on; ringing the room speaker")
    return "ringing"


def im_up() -> None:
    """I'm up (phone, TV, or "stop" to the room voice while the backup rings):
    clears a snooze, silences the backup ring, closes the TV's wake screen
    and turns on the good-morning lights in the background."""
    from brain import event_bus
    awake()
    threading.Thread(target=_good_morning_logged, daemon=True).start()
    event_bus.publish("wake_dismiss", "Up and at 'em", "", source="wake-up", severity="info", push=False)


def _good_morning_logged() -> None:
    try:
        ok, detail = good_morning()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)
    if not ok:
        log.warning("Good-morning lights failed: %s", detail)


def snooze(now: datetime | None = None) -> datetime:
    """Snoozed from the phone: ring again (resuming the music) in 9 minutes.
    Core rings it, so the phone gets its I'm up / Snooze buttons again."""
    _answer()
    now = now or datetime.now()
    data = load() or _blank()
    at = (now + timedelta(minutes=SNOOZE_MINUTES)).replace(second=0, microsecond=0)
    data["once"] = {"at": at.isoformat(timespec="minutes"), "music": "", "label": "", "snooze": True}
    _save(data)
    return at


def awake() -> None:
    """I'm up: a pending snooze doesn't ring, and the backup ring stops."""
    _answer()
    data = load()
    if data and (data.get("once") or {}).get("snooze"):
        data["once"] = None
        _save(data)


def good_morning_targets(states: list[dict[str, Any]]) -> list[str]:
    """Every light, minus per-segment entities of LED strips (the strip itself covers them)."""
    return sorted(s["entity_id"] for s in states
                  if s.get("entity_id", "").startswith("light.") and "_segment_" not in s["entity_id"]
                  and s.get("state") != "unavailable")


def good_morning() -> tuple[bool, str]:
    """I'm up -> the good-morning scene. ``WAKE_UP_SCENE`` (a ``scene.`` or
    ``script.`` entity) if set, else HA's ``scene.good_morning`` if it exists,
    else every light on. ``WAKE_UP_LIGHTS=0`` turns it off. Called off the
    request thread; Home Assistant can be slow."""
    if os.getenv("WAKE_UP_LIGHTS", "1").strip().lower() in ("0", "false", "off", "no"):
        return True, "Good-morning lights are off in settings."
    from integrations import home_assistant
    scene = os.getenv("WAKE_UP_SCENE", "").strip()
    if scene:
        domain = scene.split(".", 1)[0]
        return home_assistant.call_service(domain, "turn_on", scene)
    ok, states = home_assistant.get_states()
    if not ok or not isinstance(states, list):
        return False, f"Couldn't read the lights: {states}"
    if any(s.get("entity_id") == "scene.good_morning" for s in states):
        return home_assistant.call_service("scene", "turn_on", "scene.good_morning")
    lights = good_morning_targets(states)
    if not lights:
        return False, "No lights found in Home Assistant."
    return home_assistant.call_service("light", "turn_on", service_data={"entity_id": lights})


def cancel(days_text: str | None = None) -> bool:
    """Clear some days (keeping the rest), or the whole schedule and the one-off."""
    data = load()
    if data is None:
        return False
    if not days_text:
        _save(None)
        return True
    if days_text.strip().lower() in ("once", "one-off", "one off"):
        existed, data["once"] = data["once"] is not None, None
    else:
        days = parse_days(days_text)
        existed = any(data["times"][d] for d in days)
        for day in days:
            data["times"][day] = None
    _save(data)
    return existed


def _at(day: datetime, hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _upcoming(data: dict[str, Any], now: datetime, *, exclude_fired: bool = False) -> tuple[datetime, dict[str, Any]] | None:
    """(when, what plays) for the next alarm after ``now``."""
    found: list[tuple[datetime, dict[str, Any]]] = []
    once = data.get("once")
    if once:
        at = datetime.fromisoformat(once["at"])
        if at > now:
            found.append((at, {"music": data["music"] if once.get("music") is None else once["music"],
                               "label": once.get("label") or (data["label"] if once.get("music") is None else ""),
                               "once": True}))
    overrides = data.get("overrides", {})
    for offset in range(8):
        day = now + timedelta(days=offset)
        date = day.date().isoformat()
        # A one-day change wins (even while the schedule is paused); "" skips the day.
        hhmm = overrides[date] if date in overrides else data["times"][day.weekday()] if data.get("enabled", True) else None
        if hhmm and _at(day, hhmm) > now:
            found.append((_at(day, hhmm), {"music": data["music"], "label": data["label"], "once": False}))
            break
    if exclude_fired:
        found = [pair for pair in found if pair[0].isoformat(timespec="minutes") > (data.get("last_fired") or "")]
    return min(found, key=lambda pair: pair[0]) if found else None


def next_time(data: dict[str, Any] | None, now: datetime | None = None) -> datetime | None:
    """The next moment an alarm goes off (local time), or None if nothing is set."""
    if not data:
        return None
    upcoming = _upcoming(data, now or datetime.now())
    return upcoming[0] if upcoming else None


def due(now: datetime | None = None) -> dict[str, Any] | None:
    """What to play if an alarm should fire now (within 10 minutes of its
    time, once per alarm): ``{"music", "label", "volume", "time"}``."""
    data = load()
    now = now or datetime.now()
    if not data:
        return None
    upcoming = _upcoming(data, now - timedelta(minutes=10), exclude_fired=True)
    if not upcoming or upcoming[0] > now:
        return None
    at, what = upcoming
    key = at.isoformat(timespec="minutes")
    if data.get("last_fired") == key:
        return None
    data["last_fired"] = key
    if what["once"]:
        data["once"] = None  # a one-off: gone after it goes off
    _save(data)
    return {"music": what["music"], "label": what["label"], "volume": data["volume"], "time": at.strftime("%H:%M")}


def days_text(days: list[int], once: bool = False) -> str:
    if once:
        return "once"
    if sorted(days) == list(range(7)):
        return "every day"
    if sorted(days) == [0, 1, 2, 3, 4]:
        return "weekdays"
    if sorted(days) == [5, 6]:
        return "weekends"
    return ", ".join(DAY_NAMES[d].title() for d in sorted(days))


def _clock(hhmm: str) -> str:
    return datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p")


def schedule_text(data: dict[str, Any]) -> str:
    """"6:30 AM weekdays, 9:00 AM Sat" -- days grouped by shared time."""
    groups: dict[str, list[int]] = {}
    for day, hhmm in enumerate(data["times"]):
        if hhmm:
            groups.setdefault(hhmm, []).append(day)
    return "; ".join(f"{_clock(t)} {days_text(d)}" for t, d in sorted(groups.items(), key=lambda g: g[1][0]))


def describe(data: dict[str, Any] | None = None, now: datetime | None = None) -> str:
    data = data if data is not None else load()
    if not data:
        return "No wake-up alarm is set, sir."
    parts = []
    if any(data["times"]):
        music = data.get("label") or ("the Spotify link you gave me" if data.get("music") else "whatever Spotify last played")
        paused = "" if data.get("enabled", True) else " (paused)"
        parts.append(f"Wake-up schedule{paused}: {schedule_text(data)}, with {music} on the TV.")
    for date, hhmm in sorted(data.get("overrides", {}).items()):
        day = datetime.strptime(date, "%Y-%m-%d").strftime("%a %b %-d")
        parts.append(f"{day}: skipped." if not hhmm else f"{day}: {_clock(hhmm)} instead.")
    if data.get("once"):
        at = datetime.fromisoformat(data["once"]["at"])
        parts.append(f"A one-off at {at.strftime('%-I:%M %p')} {at.strftime('%a')}.")
    return " ".join(parts)


def _when(at: datetime, now: datetime) -> str:
    days = (at.date() - now.date()).days
    return "Today" if days == 0 else "Tomorrow" if days == 1 else at.strftime("%a")


def public(now: datetime | None = None) -> dict[str, Any] | None:
    """For /api/glance: the next alarm, for the TV's wake-up tile."""
    data = load()
    now = now or datetime.now()
    upcoming = _upcoming(data, now) if data else None
    if not upcoming:
        return None
    at, what = upcoming
    return {
        "time": at.strftime("%H:%M"),
        "days": _when(at, now),
        "label": what["label"],
        "next_at": at.timestamp(),
    }


def editor(now: datetime | None = None) -> dict[str, Any]:
    """For the companion app's schedule editor (GET/PUT /api/wake)."""
    data = load() or _blank()
    at = next_time(data, now)
    return {
        "times": data["times"],
        "music": data["music"],
        "label": data["label"],
        "volume": data["volume"],
        "enabled": data.get("enabled", True),
        "once_at": data["once"]["at"] if data.get("once") else None,
        "overrides": [{"date": d, "time": t or None} for d, t in sorted(data.get("overrides", {}).items())],
        "next_at": at.timestamp() if at else None,
        "summary": describe(data) if load() else "No wake-up set.",
    }
