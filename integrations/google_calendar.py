"""Google Calendar integration for JARVIS.

Read-only access to the user's calendar so JARVIS can read the day's agenda,
report the next event, and proactively warn about upcoming meetings.

Configuration (.env):
- GOOGLE_CREDENTIALS_FILE : path to the OAuth client-secret JSON downloaded
  from Google Cloud Console (Desktop app credentials).
- GOOGLE_TOKEN_FILE       : where the cached auth token lives. Optional;
  defaults to memory/google_token.json.
- GOOGLE_CALENDAR_ID      : which calendar to read. Optional; defaults to
  'primary'.

First run opens a browser once for consent and caches a token; subsequent runs
refresh silently. Everything degrades gracefully: if the libraries aren't
installed or nothing is configured, the spoken helpers say so politely instead
of raising.
"""

from __future__ import annotations

import os
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


def _calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary"


def _credentials_file() -> str:
    return os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()


def _token_file() -> str:
    return os.getenv("GOOGLE_TOKEN_FILE", str(_DEFAULT_TOKEN)).strip() or str(_DEFAULT_TOKEN)


def is_configured() -> bool:
    """True if we have either a cached token or a credentials file to use."""
    return bool(_credentials_file()) or Path(_token_file()).exists()


def _get_service() -> Any | None:
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


def upcoming_events(max_results: int = 10, hours_ahead: float | None = None) -> list[dict[str, Any]] | None:
    """Return upcoming events, or None if the calendar can't be reached."""
    svc = _get_service()
    if svc is None:
        return None
    now = datetime.now(timezone.utc)
    params: dict[str, Any] = {
        "calendarId": _calendar_id(),
        "timeMin": now.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max_results,
    }
    if hours_ahead:
        params["timeMax"] = (now + timedelta(hours=hours_ahead)).isoformat()
    try:
        resp = svc.events().list(**params).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("Calendar list failed: %s", exc)
        return None
    out: list[dict[str, Any]] = []
    for ev in resp.get("items", []):
        start = ev.get("start", {})
        out.append(
            {
                "id": ev.get("id", ""),
                "summary": ev.get("summary", "(no title)"),
                "start": _parse_dt(start),
                "all_day": "date" in start,
                "location": ev.get("location", ""),
            }
        )
    return out


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
