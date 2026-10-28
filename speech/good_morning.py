"""The morning routine: "Jarvis, good morning."

Like "Hey Google, good morning", with no model in the loop:

    JARVIS: Good morning, sir. It's 7:05 on Thursday. It's 43 and clear,
            heading for 60. First up, Chem quiz at 9:00, then 2 more today.

It also does what I'm up does on the phone or TV: a pending snooze is
cleared, the TV's wake screen closes (``wake_dismiss`` on the bus) and the
good-morning lights come on (``wake_alarm.good_morning``: HA's Good Morning
scene), in the background so the briefing isn't kept waiting.
"""
from __future__ import annotations

from datetime import datetime
import re
import threading
from typing import Any, Callable

from jarvis_logger import get_logger

log = get_logger(__name__)

_TRIGGERS = {"good morning", "morning", "goodmorning", "i'm up", "im up", "i am up", "i'm awake", "im awake",
             "rise and shine", "wake me up", "good morning sir"}


def is_good_morning(text: str) -> bool:
    said = re.sub(r"[^\w\s']", " ", (text or "").lower())
    said = re.sub(r"\b(hey|jarvis|ok|okay)\b", " ", said)
    return " ".join(said.split()) in _TRIGGERS


def _weather_line() -> str:
    from app import status_hub
    try:
        weather = status_hub.weather_status()
    except Exception:  # noqa: BLE001
        return ""
    temp = weather.get("temperature_f")
    if weather.get("error") or temp is None:
        return ""
    line = f"It's {round(temp)} and {weather.get('description') or 'fair'}"
    high = weather.get("high_f")
    if high is not None and round(high) > round(temp):
        line += f", heading for {round(high)}"
    return line + "."


def _today(now: datetime) -> list[dict[str, Any]]:
    from app import status_hub
    try:
        events = status_hub._glance_events()  # noqa: SLF001 - same calendar view as the TV
    except Exception:  # noqa: BLE001
        return []
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    end = start + 86400
    return [e for e in events if start <= float(e.get("start_at") or 0) < end]


def _calendar_line(now: datetime) -> str:
    events = _today(now)
    timed = [e for e in events if not e.get("all_day") and float(e["start_at"]) >= now.timestamp() - 1800]
    if not events:
        return "Nothing on the calendar today."
    if not timed:
        names = ", ".join(e["title"] for e in events[:2])
        return f"Today: {names}."
    first = timed[0]
    at = datetime.fromtimestamp(float(first["start_at"])).strftime("%I:%M").lstrip("0")
    rest = len(events) - 1
    more = f", then {rest} more today" if rest > 0 else ""
    return f"First up, {first['title']} at {at}{more}."


def briefing(now: datetime | None = None) -> str:
    now = now or datetime.now()
    clock = now.strftime("%I:%M").lstrip("0")
    parts = [f"Good morning, sir. It's {clock} on {now.strftime('%A')}.", _weather_line(), _calendar_line(now)]
    return " ".join(p for p in parts if p)


def _wake_up() -> None:
    from brain import event_bus, wake_alarm
    try:
        wake_alarm.awake()
        event_bus.publish("wake_dismiss", "Up and at 'em", "", source="room-voice", severity="info", push=False)
        ok, detail = wake_alarm.good_morning()
        if not ok:
            log.warning("Good-morning lights failed: %s", detail)
    except Exception:  # noqa: BLE001
        log.exception("Good-morning routine's wake-up step failed")


def run(say: Callable[[str], None]) -> str:
    """Lights and the TV in the background; the briefing spoken now."""
    threading.Thread(target=_wake_up, name="good-morning", daemon=True).start()
    line = briefing()
    say(line)
    return line
