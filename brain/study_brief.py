"""Daily study brief: a JARVIS-composed countdown to quizzes and exams.

The rules live in ``.env`` as ``STUDY_BRIEF_*`` settings so Nick can change
them by talking to JARVIS (``update_setting``): whether it runs, the hour
it fires, how many days of warning, which calendar-title keywords count as
an assessment, and a free-form style note. The phone schedules local
notifications from ``/api/study/brief``; this module decides *what days
get a brief* and *what each one says*, so the text sounds like JARVIS and
tracks the current settings instead of a template baked into the app.

Composition calls the model once per refresh (the cheap extraction model)
for the next few briefs and falls back to plain template lines when the
model is unavailable, so the brief never silently disappears.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

DEFAULT_KEYWORDS = "quiz,test,testlet,exam,midterm,final"
DEFAULT_ASSIGNMENT_KEYWORDS = "assignment,homework,hw,lab,project,paper,essay,due"
DEFAULT_ASSIGNMENT_WINDOW_DAYS = 7
DEFAULT_HOUR = 9
DEFAULT_WINDOW_DAYS = 14
_MAX_DAYS_AHEAD = 30       # briefs prepared for the phone to schedule
_MODEL_BRIEFS = 3          # how many upcoming briefs the model writes
_CACHE_TTL_S = 6 * 3600
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"

_client: Any | None = None
_client_failed = False
_cache: tuple[str, float, dict[str, Any]] | None = None
_cache_lock = threading.Lock()


# --- Settings ---------------------------------------------------------------

def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(str(os.getenv(name, "") or "").strip() or default)
    except ValueError:
        value = default
    return max(lo, min(hi, value))


def _keyword_list(env_name: str, default: str) -> list[str]:
    raw = os.getenv(env_name, "") or default
    seen: list[str] = []
    for word in raw.split(","):
        cleaned = " ".join(word.split()).strip().lower()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen or default.split(",")


def keywords() -> list[str]:
    """Calendar-title words that mark an assessment (quiz, testlet, exam...)."""
    return _keyword_list("STUDY_BRIEF_KEYWORDS", DEFAULT_KEYWORDS)


def assignment_keywords() -> list[str]:
    """Calendar-title words that mark work due (homework, lab, project...)."""
    return _keyword_list("STUDY_BRIEF_ASSIGNMENT_KEYWORDS", DEFAULT_ASSIGNMENT_KEYWORDS)


def _pattern(words: list[str]) -> re.Pattern[str]:
    parts = [re.escape(word) + r"(?:s|es|zes)?" for word in words]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


def keyword_pattern() -> re.Pattern[str]:
    """Match any configured assessment keyword as a whole word, tolerating plurals."""
    return _pattern(keywords())


def assignment_pattern() -> re.Pattern[str]:
    return _pattern(assignment_keywords())


def classify(title: str) -> str | None:
    """``"assessment"``, ``"assignment"`` or None. Assessment wins a tie, so
    "Quiz 2 due" counts as the quiz it is."""
    if keyword_pattern().search(title):
        return "assessment"
    if assignment_pattern().search(title):
        return "assignment"
    return None


def settings() -> dict[str, Any]:
    enabled = str(os.getenv("STUDY_BRIEF_ENABLED", "true") or "true").strip().lower()
    return {
        "enabled": enabled not in ("0", "false", "no", "off", "n"),
        "hour": _int_env("STUDY_BRIEF_HOUR", DEFAULT_HOUR, 0, 23),
        "window_days": _int_env("STUDY_BRIEF_WINDOW_DAYS", DEFAULT_WINDOW_DAYS, 1, 60),
        "keywords": keywords(),
        "assignment_keywords": assignment_keywords(),
        "assignment_window_days": _int_env(
            "STUDY_BRIEF_ASSIGNMENT_WINDOW_DAYS", DEFAULT_ASSIGNMENT_WINDOW_DAYS, 1, 60
        ),
        "style": " ".join((os.getenv("STUDY_BRIEF_STYLE", "") or "").split())[:400],
    }


# --- Planning ---------------------------------------------------------------

def dedupe_same_day(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse events that share a title and a local calendar day.

    School calendar exports commonly carry each assessment twice: an all-day
    "due" entry and a timed class-slot entry. Keep one per title per day,
    preferring the timed entry because it says when. Order is preserved.
    """
    best: dict[tuple[str, date], int] = {}
    kept: list[dict[str, Any]] = []
    for event in events:
        start = _event_start(event)
        title = " ".join(str(event.get("summary") or "").lower().split())
        if start is None or not title:
            kept.append(event)
            continue
        key = (title, start.date())
        timed = not bool(event.get("all_day", False))
        if key not in best:
            best[key] = len(kept)
            kept.append(event)
        elif timed and bool(kept[best[key]].get("all_day", False)):
            kept[best[key]] = event
    return kept


def _event_start(event: dict[str, Any]) -> datetime | None:
    start = event.get("start")
    if isinstance(start, datetime):
        return start.astimezone()
    if isinstance(start, (int, float)):
        return datetime.fromtimestamp(float(start)).astimezone()
    return None


def plan_briefs(
    events: list[dict[str, Any]], now: datetime | None = None, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """One entry per future morning that has at least one assessment inside
    the warning window. Mirrors what the phone will schedule."""
    cfg = cfg or settings()
    now = (now or datetime.now()).astimezone()
    today = now.date()

    upcoming: list[tuple[str, datetime, bool, str]] = []
    for event in dedupe_same_day(events):
        title = str(event.get("summary") or "").strip()
        start = _event_start(event)
        kind = classify(title) if title else None
        if start is None or kind is None:
            continue
        all_day = bool(event.get("all_day", False))
        if all_day:
            if start.date() < today:
                continue
        elif start <= now:
            continue
        upcoming.append((title, start, all_day, kind))
    upcoming.sort(key=lambda item: item[1])

    briefs: list[dict[str, Any]] = []
    for offset in range(_MAX_DAYS_AHEAD):
        day = today + timedelta(days=offset)
        fire = datetime.combine(day, datetime.min.time()).replace(hour=cfg["hour"]).astimezone()
        if fire <= now:
            continue
        items = []
        for title, start, all_day, kind in upcoming:
            remaining = (start.date() - day).days
            window = cfg["window_days"] if kind == "assessment" else cfg["assignment_window_days"]
            if remaining < 0 or remaining > window:
                continue
            if not all_day and start < fire:
                continue
            items.append({"title": title, "days_left": remaining, "kind": kind})
        if items:
            briefs.append({"id": day.isoformat(), "fire_at": fire.timestamp(), "items": items})
    return briefs


def template_line(title: str, days_left: int, kind: str = "assessment") -> str:
    verb = "due" if kind == "assignment" else "is"
    if days_left <= 0:
        return f"{title} {verb} today."
    if days_left == 1:
        return f"{title} {verb} tomorrow."
    return f"{title} {verb} in {days_left} days."


def template_body(items: list[dict[str, Any]]) -> str:
    lines = [
        template_line(str(i["title"]), int(i["days_left"]), str(i.get("kind", "assessment")))
        for i in items[:4]
    ]
    if len(items) > 4:
        lines.append(f"+{len(items) - 4} more in your Hub.")
    return "\n".join(lines)


# --- Composition ------------------------------------------------------------

def _model() -> str:
    return (
        (os.getenv("STUDY_BRIEF_MODEL", "") or "").strip()
        or (os.getenv("MEMORY_EXTRACT_MODEL", "") or "").strip()
        or _DEFAULT_MODEL
    )


def _get_client() -> Any | None:
    global _client, _client_failed
    if _client is not None:
        return _client
    if _client_failed:
        return None
    key = (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()
    if not key:
        _client_failed = True
        return None
    try:
        from anthropic import Anthropic  # type: ignore

        _client = Anthropic(api_key=key)
    except Exception as exc:  # noqa: BLE001
        log.warning("Study brief model client unavailable: %s", exc)
        _client_failed = True
        return None
    return _client


_WRITE_TOOL = {
    "name": "write_briefs",
    "description": "Return the notification body for each brief, in order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "bodies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One plain-text body per brief, same order as given.",
            }
        },
        "required": ["bodies"],
    },
}


def _system_prompt(cfg: dict[str, Any]) -> str:
    address = (os.getenv("ADDRESS", "sir") or "sir").strip() or "sir"
    style = cfg.get("style") or ""
    style_line = f"\nNick's standing style note for this brief: {style}" if style else ""
    return (
        "You are JARVIS, Nick's personal assistant: polished, dry British wit, "
        f"never breaks character, addresses him as \"{address}\". You are writing "
        "the body of a morning phone notification: a synopsis of his upcoming "
        "assessments (quizzes, testlets, exams) and assignments due.\n"
        "Rules: plain text only, no markdown or emoji. At most four short lines "
        "per brief, under 260 characters total. Every item listed must be named "
        "with its exact days-left figure (today, tomorrow, or 'in N days'); items "
        "of kind 'assignment' are work due, say 'due'; never invent or omit one. "
        "Keep the wit to a light touch; the facts come "
        f"first.{style_line}"
    )


def compose_bodies_with_model(client: Any, briefs: list[dict[str, Any]], cfg: dict[str, Any]) -> list[str]:
    """Ask the model for one body per brief. Raises on any failure so the
    caller can fall back; validates count and length."""
    payload = [
        {
            "day": b["id"],
            "items": [
                {"title": i["title"], "days_left": i["days_left"], "kind": i.get("kind", "assessment")}
                for i in b["items"]
            ],
        }
        for b in briefs
    ]
    response = client.messages.create(
        model=_model(),
        max_tokens=600,
        system=_system_prompt(cfg),
        tools=[_WRITE_TOOL],
        tool_choice={"type": "tool", "name": "write_briefs"},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    bodies: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            raw = getattr(block, "input", None) or {}
            bodies = [str(b).strip() for b in (raw.get("bodies") or [])]
            break
    if len(bodies) != len(briefs) or any(not b or len(b) > 400 for b in bodies):
        raise ValueError("model returned an unusable brief set")
    return bodies


def compose(
    events: list[dict[str, Any]], now: datetime | None = None, client: Any | None = None
) -> dict[str, Any]:
    """Full study-brief payload: settings plus the briefs the phone should schedule."""
    cfg = settings()
    result: dict[str, Any] = {
        "enabled": cfg["enabled"],
        "hour": cfg["hour"],
        "window_days": cfg["window_days"],
        "keywords": cfg["keywords"],
        "assignment_keywords": cfg["assignment_keywords"],
        "assignment_window_days": cfg["assignment_window_days"],
        "style": cfg["style"],
        "generated_at": time.time(),
        "composed_by": "template",
        "briefs": [],
    }
    if not cfg["enabled"]:
        return result

    briefs = plan_briefs(events, now=now, cfg=cfg)
    for brief in briefs:
        brief["body"] = template_body(brief["items"])

    head = briefs[:_MODEL_BRIEFS]
    if head:
        client = client if client is not None else _get_client()
        if client is not None:
            try:
                bodies = compose_bodies_with_model(client, head, cfg)
            except Exception as exc:  # noqa: BLE001
                log.warning("Study brief composition fell back to template: %s", exc)
            else:
                for brief, body in zip(head, bodies):
                    brief["body"] = body
                result["composed_by"] = "model"

    result["briefs"] = briefs
    return result


def _signature(events: list[dict[str, Any]], cfg: dict[str, Any], today: date) -> str:
    keys = []
    for event in events:
        start = _event_start(event)
        keys.append((str(event.get("id") or event.get("summary") or ""), start.isoformat() if start else ""))
    return json.dumps({"cfg": cfg, "today": today.isoformat(), "events": sorted(keys)}, sort_keys=True)


def compose_cached(events: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """``compose`` memoised on settings + events + calendar day, so the phone
    can poll freely without a model call each time. Changing any STUDY_BRIEF_*
    setting changes the signature and recomposes on the next request."""
    global _cache
    now_dt = (now or datetime.now()).astimezone()
    sig = _signature(events, settings(), now_dt.date())
    with _cache_lock:
        if _cache and _cache[0] == sig and _cache[1] > time.monotonic():
            return dict(_cache[2])
    result = compose(events, now=now_dt)
    with _cache_lock:
        _cache = (sig, time.monotonic() + _CACHE_TTL_S, result)
    return dict(result)


def clear_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def spoken_preview(payload: dict[str, Any], calendar_error: str = "") -> str:
    """Spoken-friendly summary for the study_brief_preview tool."""
    hour = int(payload.get("hour", DEFAULT_HOUR))
    clock = f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"
    window = int(payload.get("window_days", DEFAULT_WINDOW_DAYS))
    keywords_text = ", ".join(payload.get("keywords") or [])
    assignment_text = ", ".join(payload.get("assignment_keywords") or [])
    assignment_window = int(payload.get("assignment_window_days", DEFAULT_ASSIGNMENT_WINDOW_DAYS))
    style = payload.get("style") or "none"
    header = (
        f"Study brief: {'on' if payload.get('enabled') else 'off'}, fires daily at {clock}, "
        f"{window}-day warning for assessments ({keywords_text}), {assignment_window}-day "
        f"warning for assignments ({assignment_text}). Style note: {style}."
    )
    if calendar_error:
        return f"{header} {calendar_error}"
    if not payload.get("enabled"):
        return header
    briefs = payload.get("briefs") or []
    if not briefs:
        return f"{header} Nothing on the calendar matches within the window, so no brief is scheduled."
    nxt = briefs[0]
    when = datetime.fromtimestamp(float(nxt["fire_at"])).strftime("%A %-I %p")
    body = str(nxt.get("body", "")).replace("\n", " ")
    return f"{header} Next brief, {when}: {body}"
