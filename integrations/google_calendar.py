"""Google Calendar integration for JARVIS.

Read-only access to the user's calendar so JARVIS can read the day's agenda,
report the next event, and proactively warn about upcoming meetings.

Configuration (.env):
- GOOGLE_CREDENTIALS_FILE : path to the OAuth client-secret JSON downloaded
  from Google Cloud Console (Desktop app credentials).
- GOOGLE_TOKEN_FILE       : where the cached auth token lives. Optional;
  defaults to memory/google_token.json.
- GOOGLE_CALENDAR_ID      : which calendars to read. ``all`` (the default)
  merges every calendar on the account (school, work, shared, holidays);
  otherwise a comma-separated list of calendar ids, e.g. ``primary``.

First run opens a browser once for consent and caches a token; subsequent runs
refresh silently. Everything degrades gracefully: if the libraries aren't
installed or nothing is configured, the spoken helpers say so politely instead
of raising.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from jarvis_logger import get_logger

log = get_logger(__name__)

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_TOKEN = _PROJECT_ROOT / "memory" / "google_token.json"

_service: Any | None = None
_service_failed = False


def _academic_pattern() -> re.Pattern[str]:
    """Assessment keywords come from STUDY_BRIEF_KEYWORDS so Nick can widen
    or narrow them by talking to JARVIS; the default matches quiz/test/exam/
    midterm/final with plurals."""
    from brain.study_brief import keyword_pattern

    return keyword_pattern()


def _dedupe_same_day(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from brain.study_brief import dedupe_same_day

    return dedupe_same_day(events)


_calendar_list_cache: tuple[float, list[dict[str, str]]] | None = None
_CALENDAR_LIST_TTL_S = 3600.0


def _calendar_setting() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID", "all").strip() or "all"


def _calendar_ids(svc: Any) -> list[dict[str, str]]:
    """``[{"id", "name"}, ...]`` to query. ``all`` expands to every calendar
    on the account (cached for an hour); anything else is a comma list."""
    global _calendar_list_cache
    setting = _calendar_setting()
    if setting.lower() != "all":
        return [{"id": part.strip(), "name": part.strip()} for part in setting.split(",") if part.strip()]
    import time as _time

    now = _time.monotonic()
    if _calendar_list_cache and _calendar_list_cache[0] > now:
        return list(_calendar_list_cache[1])
    try:
        resp = svc.calendarList().list(maxResults=250, showHidden=False).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("Calendar list lookup failed; using primary only: %s", exc)
        return [{"id": "primary", "name": "primary"}]
    calendars = [
        {"id": str(item.get("id", "")), "name": str(item.get("summaryOverride") or item.get("summary") or item.get("id", ""))}
        for item in resp.get("items", [])
        if item.get("id") and not item.get("deleted") and not item.get("hidden")
    ]
    if not calendars:
        calendars = [{"id": "primary", "name": "primary"}]
    _calendar_list_cache = (now + _CALENDAR_LIST_TTL_S, calendars)
    return list(calendars)


def _credentials_file() -> str:
    return os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()


def _token_file() -> str:
    return os.getenv("GOOGLE_TOKEN_FILE", str(_DEFAULT_TOKEN)).strip() or str(_DEFAULT_TOKEN)


def is_configured() -> bool:
    """True if we have either a cached token or a credentials file to use."""
    return bool(_credentials_file()) or Path(_token_file()).exists()


def _get_service(*, allow_interactive: bool = True) -> Any | None:
    global _service, _service_failed
    if _service is not None:
        return _service
    if _service_failed:
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("Google API libraries not installed; calendar disabled.")
        _service_failed = True
        return None

    creds = None
    token_path = Path(_token_file())
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
        except Exception:  # noqa: BLE001
            creds = None

    if creds is None or not creds.valid:
        if creds is not None and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:  # noqa: BLE001
                creds = None
        if creds is None:
            # Dashboard polling must never open a browser or block waiting for
            # OAuth consent. Explicit calendar tools retain the setup flow.
            if not allow_interactive:
                return None
            cred_file = _credentials_file()
            if not cred_file or not Path(cred_file).exists():
                log.warning("Google credentials file missing; calendar disabled.")
                _service_failed = True
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(cred_file, _SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as exc:  # noqa: BLE001
                log.warning("Google OAuth flow failed: %s", exc)
                _service_failed = True
                return None
        try:
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())
        except OSError:
            pass

    try:
        _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return _service
    except Exception as exc:  # noqa: BLE001
        log.warning("Calendar service build failed: %s", exc)
        _service_failed = True
        return None


def _parse_dt(node: dict[str, Any]) -> datetime | None:
    if "dateTime" in node:
        raw = node["dateTime"].replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw).astimezone()
        except ValueError:
            return None
    if "date" in node:
        try:
            return datetime.fromisoformat(node["date"]).astimezone()
        except ValueError:
            return None
    return None


def upcoming_events(max_results: int = 10, hours_ahead: float | None = None,
                    *, allow_interactive: bool = True) -> list[dict[str, Any]] | None:
    """Return upcoming events, or None if the calendar can't be reached."""
    svc = _get_service(allow_interactive=allow_interactive)
    if svc is None:
        return None
    now = datetime.now(timezone.utc)
    params: dict[str, Any] = {
        "timeMin": now.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max_results,
    }
    if hours_ahead:
        params["timeMax"] = (now + timedelta(hours=hours_ahead)).isoformat()

    out: list[dict[str, Any]] = []
    reached_any = False
    for calendar in _calendar_ids(svc):
        try:
            resp = svc.events().list(calendarId=calendar["id"], **params).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("Calendar list failed for %s: %s", calendar["name"], exc)
            continue
        reached_any = True
        for ev in resp.get("items", []):
            start = ev.get("start", {})
            out.append(
                {
                    "id": ev.get("id", ""),
                    "summary": ev.get("summary", "(no title)"),
                    "start": _parse_dt(start),
                    "all_day": "date" in start,
                    "location": ev.get("location", ""),
                    "calendar": calendar["name"],
                }
            )
    if not reached_any:
        return None
    # The same event often lives on two calendars (a class schedule imported
    # into the primary calendar as well as the school one). Keep the first
    # copy of each title + start; the calendars are queried in list order.
    seen: set[tuple[str, str, bool]] = set()
    unique: list[dict[str, Any]] = []
    for event in out:
        key = (
            " ".join(str(event["summary"]).lower().split()),
            event["start"].isoformat() if event["start"] else "",
            bool(event["all_day"]),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    # Merge across calendars in start order; unparseable starts sink to the end.
    unique.sort(key=lambda e: (e["start"] is None, e["start"] or datetime.max.replace(tzinfo=timezone.utc)))
    return unique[:max_results]


def upcoming_within(minutes: float) -> list[dict[str, Any]]:
    """Timed events starting within ``minutes`` from now (for proactive alerts)."""
    events = upcoming_events(max_results=10, hours_ahead=max(1.0, minutes / 60 + 1))
    if not events:
        return []
    now = datetime.now().astimezone()
    soon: list[dict[str, Any]] = []
    for e in events:
        st = e["start"]
        if st and not e["all_day"]:
            delta_min = (st - now).total_seconds() / 60.0
            if 0 <= delta_min <= minutes:
                item = dict(e)
                item["minutes_until"] = int(round(delta_min))
                soon.append(item)
    return soon


def academic_events_from(
    events: list[dict[str, Any]], now: datetime | None = None
) -> list[dict[str, Any]]:
    """Return quiz/test/exam events with a local-calendar-day countdown.

    Calendar titles are intentionally the source of truth: this needs no
    school-specific API, and it works with the read-only Google Calendar
    connection JARVIS already uses.
    """
    today = (now or datetime.now().astimezone()).astimezone().date()
    pattern = _academic_pattern()
    academic: list[dict[str, Any]] = []
    for event in _dedupe_same_day(events):
        start = event.get("start")
        title = str(event.get("summary") or "")
        if start is None or not pattern.search(title):
            continue
        item = dict(event)
        item["days_left"] = max(0, (start.astimezone().date() - today).days)
        academic.append(item)
    return academic


def academic_deadlines(days_ahead: int = 60) -> list[dict[str, Any]] | None:
    """Upcoming quizzes/tests/exams, or None when Calendar is unavailable."""
    events = upcoming_events(max_results=100, hours_ahead=max(1, days_ahead) * 24)
    if events is None:
        return None
    return academic_events_from(events)


def _spoken_time(dt: datetime) -> str:
    hm = dt.strftime("%I:%M").lstrip("0")
    return f"{hm} {dt.strftime('%p')}"


def todays_agenda() -> str:
    if not is_configured():
        return "My calendar isn't connected yet, sir."
    events = upcoming_events(max_results=20, hours_ahead=24)
    if events is None:
        return "I couldn't reach your calendar, sir."
    today = datetime.now().date()
    todays = [e for e in events if e["start"] and e["start"].date() == today]
    if not todays:
        return "Your calendar is clear for the rest of today, sir."
    parts: list[str] = []
    for e in todays:
        if e["all_day"]:
            parts.append(f"{e['summary']}, all day")
        else:
            parts.append(f"{e['summary']} at {_spoken_time(e['start'])}")
    return "On your calendar today: " + "; ".join(parts) + "."


def next_event_spoken() -> str:
    if not is_configured():
        return "My calendar isn't connected yet, sir."
    events = upcoming_events(max_results=1)
    if events is None:
        return "I couldn't reach your calendar, sir."
    if not events:
        return "Nothing on your calendar coming up, sir."
    e = events[0]
    st = e["start"]
    if st is None:
        return f"Next up: {e['summary']}."
    when = "all day" if e["all_day"] else f"at {_spoken_time(st)}"
    return f"Next up: {e['summary']} on {st.strftime('%A')} {when}."
