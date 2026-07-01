"""Proactive briefing composer for JARVIS.

Builds a short, spoken-style situational briefing from the things JARVIS
already knows: the time of day, current weather, and any running timers.
It is deliberately self-contained so it can be called from a tool, from the
runtime at launch, or from a scheduled task, without wiring.

Kept to 1-3 sentences to match JARVIS's spoken style.
"""

from __future__ import annotations

import os
from datetime import datetime

from brain.reminders import active_reminders
from brain.timers import active_timers
from brain.weather import get_weather_snapshot
from jarvis_logger import get_logger

log = get_logger(__name__)


def _address() -> str:
    return (os.getenv("ADDRESS") or "sir").strip() or "sir"


def _time_greeting(now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def _spoken_time(now: datetime) -> str:
    hour_minute = now.strftime("%I:%M").lstrip("0")
    am_pm = now.strftime("%p")
    weekday = now.strftime("%A")
    month = now.strftime("%B")
    return f"It's {hour_minute} {am_pm} on {weekday}, {month} {now.day}"


def compose_briefing() -> str:
    """Return a short spoken briefing: greeting, time, weather, timers."""
    address = _address()
    now = datetime.now()

    parts: list[str] = [f"{_time_greeting(now)}, {address}.", f"{_spoken_time(now)}."]

    try:
        weather = get_weather_snapshot()
    except Exception:  # noqa: BLE001
        weather = None
        log.debug("Briefing weather lookup failed.")

    if weather is not None and getattr(weather, "online", False) and weather.temperature_f is not None:
        sentence = f"It's currently {weather.temperature_f} degrees"
        if weather.description:
            sentence += f" and {weather.description}"
        if weather.high_f is not None and weather.low_f is not None:
            sentence += f", with a high of {weather.high_f} and a low of {weather.low_f}"
        parts.append(sentence + ".")

    try:
        from integrations.google_calendar import is_configured, todays_agenda
        if is_configured():
            agenda = todays_agenda()
            skip = ("isn't connected" in agenda or "couldn't reach" in agenda
                    or "clear for the rest" in agenda)
            if agenda and not skip:
                parts.append(agenda)
    except Exception:  # noqa: BLE001
        pass

    try:
        todos = active_reminders(include_done=False)
    except Exception:  # noqa: BLE001
        todos = []
    if todos:
        parts.append(
            f"You have {len(todos)} reminder{'s' if len(todos) != 1 else ''} on your list."
        )

    running = [t for t in active_timers() if not t.paused and not t.alarming]
    alarming = [t for t in active_timers() if t.alarming]
    if alarming:
        parts.append(
            f"{len(alarming)} timer{'s have' if len(alarming) != 1 else ' has'} "
            f"already gone off and {'are' if len(alarming) != 1 else 'is'} awaiting dismissal."
        )
    elif running:
        parts.append(
            f"You have {len(running)} timer{'s' if len(running) != 1 else ''} running."
        )

    return " ".join(parts)
