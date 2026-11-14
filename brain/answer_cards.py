"""Answer screens: typed readouts JARVIS puts up on Nick's displays.

When Nick asks for something with a visual shape ("what's the weather?",
"what's my day look like?", "what timers have I got?") the model calls the
``show_screen`` tool instead of the plain lookup. The builder here fetches
the data once, publishes it as a card on the event bus
(``event_bus.publish_card``), and returns the same facts as text for JARVIS
to speak. Clients (iPad Room tab, the phone, JARVIS TV) receive cards on
``GET /api/clients/events`` (``event: card``), render them with one renderer
per type, and dismiss them after ``ttl_s`` or on tap.

Adding a card type = one builder in ``_BUILDERS`` plus one renderer per
client. A builder returns ``(spoken, title, data)``; ``data`` None means
there is nothing worth showing (an error, no timers), so only the text goes
back.
"""
from __future__ import annotations

from datetime import datetime
import time
from typing import Any, Callable

from brain import event_bus
from jarvis_logger import get_logger

log = get_logger(__name__)

Built = tuple[str, str, Any]  # (spoken, title, data dict or None)


def _clock(epoch: float) -> str:
    moment = datetime.fromtimestamp(epoch)
    return moment.strftime("%I:%M %p").lstrip("0") if moment.minute else moment.strftime("%I %p").lstrip("0")


# --- weather_today --------------------------------------------------------------

def _weather_today(args: dict[str, Any]) -> Built:
    from brain import weather
    data = weather.get_weather_today(args.get("location"))
    if not data.get("online"):
        return str(data.get("error") or "The weather service isn't answering."), "", None
    parts = [f"In {data['location']} it's {data['temp_f']} degrees and {data['description']}."]
    if data.get("high_f") is not None and data.get("low_f") is not None:
        parts.append(f"High {data['high_f']}, low {data['low_f']}.")
    wet = [h for h in data.get("hours", []) if (h.get("rain_chance") or 0) >= 30]
    if wet:
        peak = max(wet, key=lambda h: h["rain_chance"])
        parts.append(f"Rain chance peaks at {peak['rain_chance']} percent around {_clock(peak['at'])}.")
    elif data.get("hours"):
        parts.append("No rain to speak of for the rest of the day.")
    if data.get("wind_mph") is not None:
        gust = data.get("gust_mph")
        parts.append(f"Wind {data['wind_mph']} mph" + (f", gusting {gust}." if gust and gust >= data["wind_mph"] + 10 else "."))
    if data.get("sunset") and data["sunset"] > time.time():
        parts.append(f"Sunset at {_clock(data['sunset'])}.")
    place = str(data["location"]).split(",")[0]
    return " ".join(parts), f"Today · {place}", data


# --- calendar_day ---------------------------------------------------------------

def _calendar_day(_: dict[str, Any]) -> Built:
    from integrations import google_calendar
    if not google_calendar.is_configured():
        return "My calendar isn't connected yet, sir.", "", None
    events = google_calendar.upcoming_events(max_results=20, hours_ahead=24, allow_interactive=False)
    if events is None:
        return "I couldn't reach your calendar, sir.", "", None
    today = datetime.now().astimezone().date()
    todays = [e for e in events if e.get("start") and e["start"].astimezone().date() == today]
    shown = [{
        "title": str(e.get("summary") or "(no title)"),
        "start_at": e["start"].timestamp(),
        "all_day": bool(e.get("all_day")),
        "location": str(e.get("location") or ""),
        "calendar": str(e.get("calendar") or ""),
    } for e in todays]
    if not shown:
        spoken = "Your calendar is clear for the rest of today, sir."
    else:
        spoken = "On your calendar today: " + "; ".join(
            f"{e['title']}, all day" if e["all_day"] else f"{e['title']} at {_clock(e['start_at'])}"
            for e in shown) + "."
    data = {"date": datetime.combine(today, datetime.min.time()).astimezone().timestamp(), "events": shown}
    return spoken, datetime.now().strftime("%A, %B %-d"), data


# --- timers -----------------------------------------------------------------------

def _timers(_: dict[str, Any]) -> Built:
    from brain import timers
    active = timers.active_timers()
    if not active:
        return "You have no active timers.", "", None
    rows = [{
        "id": t.id,
        "label": t.message,
        "remaining_s": round(t.remaining_seconds(), 1),
        "paused": t.paused,
        "alarming": t.alarming,
    } for t in active]
    return timers.list_timers(), "Timers", {"timers": rows}


_BUILDERS: dict[str, Callable[[dict[str, Any]], Built]] = {
    "weather_today": _weather_today,
    "calendar_day": _calendar_day,
    "timers": _timers,
}
CARD_TYPES = tuple(_BUILDERS)


def show(card: str, args: dict[str, Any] | None = None, *, source: str = "conversation") -> str:
    """Build ``card``, put it on every screen, and return what to say."""
    builder = _BUILDERS.get(card)
    if builder is None:
        return f"Tool error: no screen called {card!r}; choose one of {', '.join(CARD_TYPES)}."
    spoken, title, data = builder(dict(args or {}))
    if data is None:
        return spoken
    event_bus.publish_card(card, title, data, source=source)
    return f"{spoken}\n(On screen now. Keep the spoken reply to a sentence or two; the screen has the detail.)"
