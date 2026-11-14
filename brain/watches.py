"""Durable "let me know when X happens" watches.

Nick says "tell me when the AP rankings are out" or "let me know when the PS5
restock happens". A watch is:

- ``query``: what to search for ("AP Top 25 college football poll week 12");
- ``done_when``: the condition that ends it ("the new week's AP poll has
  been released; answer with BYU's rank");
- a cadence (every 6 h by default; "hourly", "every morning", "twice a day",
  "every 3 hours" ...), an optional ``not_before`` for a known drop time,
  and an expiry (``WATCH_DEFAULT_EXPIRY_DAYS``, 14).

The runtime's watch monitor (``brain/runtime.py``) calls ``check_due`` every
few minutes. Due watches are checked by the ``watcher`` subagent
(``brain/subagents.py``): the cheap model plus Anthropic's web_search server
tool, one short run per watch, answering with a JSON verdict. When the
condition is met the runtime raises an alert with the answer and the watch
retires; an expired watch retires with a quiet inbox note.

Cost is capped twice: ``WATCH_MAX_CHECKS_PER_DAY`` across all watches and
``WATCH_MAX_CHECKS_PER_WATCH`` per watch per day. Every model call is
recorded with ``brain/usage.py`` by ``subagents.run``.

Store: ``memory/watches.json`` (active watches plus recently retired ones).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "watches.json"
_lock = threading.Lock()
_KEEP_RETIRED_S = 30 * 86_400
_MAX_ACTIVE = 25


@dataclass
class Watch:
    id: str
    label: str
    query: str
    done_when: str
    interval_s: float
    daily_at: str = ""          # "HH:MM" for "every morning"-style cadences; else every interval_s
    cadence: str = ""           # Nick's words, for list_watches
    not_before: float = 0.0     # no checks before this (a known drop time)
    expires_at: float = 0.0
    created_at: float = 0.0
    last_checked: float = 0.0
    checks: int = 0             # lifetime
    checks_day: str = ""        # the local date checks_today counts
    checks_today: int = 0
    last_note: str = ""         # what the last check found ("Week 12 poll not out yet")
    status: str = "active"      # active | done | expired | cancelled
    answer: str = ""
    finished_at: float = 0.0


# --- settings (read live) ----------------------------------------------------

def _num(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


def default_interval_s() -> float:
    return _num("WATCH_DEFAULT_CADENCE_HOURS", 6, 0.5, 168) * 3600


def min_interval_s() -> float:
    return _num("WATCH_MIN_INTERVAL_MIN", 30, 5, 1440) * 60


def max_checks_per_day() -> int:
    return int(_num("WATCH_MAX_CHECKS_PER_DAY", 12, 0, 500))


def max_checks_per_watch() -> int:
    return int(_num("WATCH_MAX_CHECKS_PER_WATCH", 4, 1, 100))


def default_expiry_days() -> float:
    return _num("WATCH_DEFAULT_EXPIRY_DAYS", 14, 0.04, 365)


def searches_per_check() -> int:
    return int(_num("WATCH_SEARCHES_PER_CHECK", 3, 1, 10))


# --- cadence and times ---------------------------------------------------------

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8,
          "ten": 10, "twelve": 12, "fifteen": 15, "thirty": 30, "a": 1, "an": 1}
_DAILY = {"morning": "08:00", "noon": "12:00", "midday": "12:00", "lunch": "12:00",
          "afternoon": "15:00", "evening": "19:00", "night": "21:00", "tonight": "21:00"}


def parse_cadence(text: str | None) -> tuple[float, str]:
    """Nick's words -> (interval_s, daily_at). Unknown or empty -> the default."""
    raw = (text or "").strip().lower()
    if not raw:
        return default_interval_s(), ""
    floor = min_interval_s()
    clock = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", raw)
    if clock and ("every" in raw or "daily" in raw or "each" in raw or "day" in raw):
        hour = int(clock.group(1)) % 12 + (12 if clock.group(3) == "pm" else 0)
        return 86_400.0, f"{hour:02d}:{int(clock.group(2) or 0):02d}"
    for word, at in _DAILY.items():
        if re.search(rf"\b(every|each)\s+{word}\b|\b{word}s\b|\bdaily\b.*\b{word}\b", raw):
            return 86_400.0, at
    if re.search(r"\b(hourly|every hour|each hour|once an hour)\b", raw):
        return max(floor, 3600.0), ""
    if re.search(r"\b(twice a day|twice daily|two times a day)\b", raw):
        return 43_200.0, ""
    if re.search(r"\b(three times a day)\b", raw):
        return 28_800.0, ""
    if re.search(r"\b(daily|every day|each day|once a day)\b", raw):
        return 86_400.0, ""
    if re.search(r"\b(weekly|every week|once a week)\b", raw):
        return 7 * 86_400.0, ""
    match = re.search(r"\bevery\s+(\d+|[a-z]+)\s*(minute|min|hour|hr|day)s?\b", raw)
    if match:
        count = match.group(1)
        n = int(count) if count.isdigit() else _WORDS.get(count, 0)
        unit = {"minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86_400}[match.group(2)]
        if n > 0:
            return max(floor, float(n * unit)), ""
    match = re.search(r"\bevery\s+(minute|half hour|half an hour)\b", raw)
    if match:
        return max(floor, 1800.0 if "half" in match.group(1) else 60.0), ""
    return default_interval_s(), ""


def parse_time(value: Any) -> float:
    """An ISO date/time (local when naive) or epoch seconds -> epoch; 0 if empty/bad."""
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        raise ValueError(f"I couldn't read the time '{value}'. Use an ISO date like 2026-11-16T12:00.")


def _today(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def next_due(watch: Watch) -> float:
    """When ``watch`` should next be checked (epoch seconds)."""
    if not watch.last_checked:
        # First look right away (it may already be out), or at the known drop time.
        return max(watch.created_at, watch.not_before)
    if watch.daily_at:
        hour, minute = (int(p) for p in watch.daily_at.split(":", 1))
        slot = datetime.fromtimestamp(watch.last_checked).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot.timestamp() <= watch.last_checked:
            slot += timedelta(days=1)
        return max(slot.timestamp(), watch.not_before)
    return max(watch.last_checked + watch.interval_s, watch.not_before)


# --- store ---------------------------------------------------------------------

def _load() -> list[Watch]:
    if not _FILE.exists():
        return []
    try:
        raw = json.loads(_FILE.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Watch)}
        return [Watch(**{k: v for k, v in item.items() if k in known}) for item in raw.get("watches", [])]
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        log.warning("Could not load watches: %s", exc)
        return []


def _save(items: list[Watch]) -> None:
    now = time.time()
    items = [w for w in items if w.status == "active" or now - w.finished_at < _KEEP_RETIRED_S]
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"watches": [asdict(w) for w in items]}, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


def add_watch(query: str, done_when: str, *, label: str = "", cadence: str = "",
              not_before: Any = None, expires_in_days: float | None = None,
              now: float | None = None) -> Watch:
    query, done_when = (query or "").strip(), (done_when or "").strip()
    if not query:
        raise ValueError("A watch needs something to search for.")
    if not done_when:
        raise ValueError("A watch needs a 'done when' condition.")
    now = time.time() if now is None else now
    interval, daily_at = parse_cadence(cadence)
    start = parse_time(not_before)
    days = default_expiry_days() if expires_in_days in (None, "", 0) else max(0.04, min(365.0, float(expires_in_days)))
    item = Watch(
        id="w" + uuid.uuid4().hex[:6],
        label=(label or query).strip()[:80],
        query=query[:400],
        done_when=done_when[:400],
        interval_s=interval,
        daily_at=daily_at,
        cadence=(cadence or "").strip()[:80],
        not_before=start if start > now else 0.0,
        expires_at=max(now, start) + days * 86_400,
        created_at=now,
    )
    with _lock:
        items = _load()
        if sum(1 for w in items if w.status == "active") >= _MAX_ACTIVE:
            raise ValueError(f"Already watching {_MAX_ACTIVE} things; cancel one first.")
        items.append(item)
        _save(items)
    return item


def active_watches() -> list[Watch]:
    with _lock:
        return [w for w in _load() if w.status == "active"]


def all_watches() -> list[Watch]:
    with _lock:
        return _load()


def cancel_watch(key: str) -> Watch | None:
    """Cancel by id, or by a word from the label/query when only one matches."""
    key = (key or "").strip().lower()
    if not key:
        return None
    with _lock:
        items = _load()
        active = [w for w in items if w.status == "active"]
        hit = [w for w in active if w.id == key] or \
            [w for w in active if key in w.label.lower() or key in w.query.lower()]
        if len(hit) != 1:
            return None
        hit[0].status, hit[0].finished_at = "cancelled", time.time()
        _save(items)
        return hit[0]


def _retire(watch_id: str, status: str, answer: str = "") -> Watch | None:
    with _lock:
        items = _load()
        for w in items:
            if w.id == watch_id and w.status == "active":
                w.status, w.answer, w.finished_at = status, answer, time.time()
                _save(items)
                return w
    return None


def _checks_today(items: list[Watch], day: str) -> int:
    return sum(w.checks_today for w in items if w.checks_day == day)


# --- checking ------------------------------------------------------------------

_SYSTEM = """You check one thing on the web for Nick, on behalf of JARVIS, his home assistant.

Search the web for the watch's query and decide whether its "done when"
condition is met RIGHT NOW (today is {today}). Be strict: an announcement that
something WILL happen, a rumour, or last week's/last year's edition does not
count. Prefer official and recent sources and check dates.

Reply with ONLY one JSON object, nothing else:
{{"met": true|false, "answer": "<when met: the answer itself, one or two short spoken sentences, specific (names, numbers, dates), no URLs>", "note": "<when not met: what you found, under 20 words>"}}"""


def _spec() -> Any:
    from brain import subagents

    return subagents.SubagentSpec(
        name="watcher",
        system=_SYSTEM.format(today=datetime.now().strftime("%A %B %d, %Y")),
        tools=[],
        model_setting="WATCH_MODEL",
        max_rounds=4,
        max_tokens=700,
        web_search_uses=searches_per_check(),
    )


def _verdict(text: str) -> dict[str, Any]:
    """The JSON verdict from the subagent's reply; not met when unreadable."""
    for match in re.finditer(r"\{.*?\}", text or "", re.S):
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "met" in data:
            met = data.get("met")
            met = met is True or str(met).strip().lower() == "true"
            return {"met": met, "answer": str(data.get("answer") or "").strip(),
                    "note": str(data.get("note") or "").strip()}
    return {"met": False, "answer": "", "note": (text or "").strip()[:160] or "No readable verdict."}


def check_one(watch: Watch, *, client: Any = None) -> dict[str, Any]:
    """Run the watcher subagent for ``watch``: {met, answer, note, ok, counted}."""
    from brain import subagents

    task = (f"Watch: {watch.label}\nSearch for: {watch.query}\nDone when: {watch.done_when}\n"
            f"Watching since {datetime.fromtimestamp(watch.created_at).strftime('%Y-%m-%d')}.")
    result = subagents.run(_spec(), task, client=client)
    if not result.ok:
        # No key/client: nothing was spent, so it doesn't count against the caps.
        return {"met": False, "answer": "", "note": f"Check failed: {result.error}"[:200],
                "ok": False, "counted": result.rounds > 0}
    verdict = _verdict(result.text)
    if verdict["met"] and not verdict["answer"]:
        verdict["answer"] = verdict["note"] or result.text.strip()[:300]
    return {**verdict, "ok": True, "counted": True}


def check_due(*, now: float | None = None, client: Any = None, force_id: str = "") -> list[dict[str, Any]]:
    """Expire old watches and check the due ones, within the daily caps.

    Returns outcomes for the runtime: {"event": "met"|"expired", "watch": Watch,
    "answer": str}. ``force_id`` checks that watch now (still capped)."""
    now = time.time() if now is None else now
    day = _today(now)
    outcomes: list[dict[str, Any]] = []
    with _lock:
        items = _load()
        changed = False
        for w in items:
            if w.status == "active" and w.expires_at and now >= w.expires_at:
                w.status, w.finished_at = "expired", now
                outcomes.append({"event": "expired", "watch": w, "answer": w.last_note})
                changed = True
            if w.checks_day != day:
                w.checks_day, w.checks_today = day, 0
                changed = True
        if changed:
            _save(items)
        due = [w for w in items if w.status == "active"
               and (w.id == force_id or next_due(w) <= now)]
        budget = max_checks_per_day() - _checks_today(items, day)
    due.sort(key=next_due)
    for w in due:
        if budget <= 0:
            log.info("Watch checks capped for today (WATCH_MAX_CHECKS_PER_DAY=%d)", max_checks_per_day())
            break
        if w.checks_today >= max_checks_per_watch():
            continue
        result = check_one(w, client=client)
        with _lock:
            items = _load()
            current = next((x for x in items if x.id == w.id), None)
            if current is None or current.status != "active":
                continue  # cancelled while the check ran
            current.last_checked = now
            current.last_note = (result["answer"] if result["met"] else result["note"])[:300]
            if result["counted"]:
                current.checks += 1
                current.checks_today += 1
                current.checks_day = day
                budget -= 1
            if result["met"]:
                current.status, current.answer, current.finished_at = "done", result["answer"], now
                outcomes.append({"event": "met", "watch": current, "answer": result["answer"]})
            _save(items)
    return outcomes


def due_count(now: float | None = None) -> int:
    """Watches a ``check_due`` pass would act on right now: expired ones, plus
    due ones the daily caps still allow (so a capped day starts no empty jobs)."""
    now = time.time() if now is None else now
    day = _today(now)
    items = all_watches()
    active = [w for w in items if w.status == "active"]
    expired = sum(1 for w in active if w.expires_at and now >= w.expires_at)
    if max_checks_per_day() - _checks_today(items, day) <= 0:
        return expired
    checkable = sum(1 for w in active if not (w.expires_at and now >= w.expires_at)
                    and next_due(w) <= now
                    and (w.checks_day != day or w.checks_today < max_checks_per_watch()))
    return expired + checkable


# --- descriptions --------------------------------------------------------------

def when(ts: float) -> str:
    moment = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    if moment.date() == today:
        day = "today"
    elif moment.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = moment.strftime("%a %b %d")
    return f"{day} {moment.strftime('%I:%M %p').lstrip('0')}"


def describe_cadence(w: Watch) -> str:
    if w.daily_at:
        hour, minute = (int(p) for p in w.daily_at.split(":", 1))
        return "daily at " + datetime(2000, 1, 1, hour, minute).strftime("%I:%M %p").lstrip("0")
    hours = w.interval_s / 3600
    if hours < 1:
        return f"every {int(w.interval_s // 60)} min"
    return "hourly" if hours == 1 else f"every {hours:g} h"


def describe(w: Watch) -> str:
    return (f"({w.id}) {w.label}: done when {w.done_when}; {describe_cadence(w)}, "
            f"next check {when(next_due(w))}, until {when(w.expires_at)}"
            + (f"; last: {w.last_note}" if w.last_note else ""))


def describe_watches() -> str:
    items = all_watches()
    active = [w for w in items if w.status == "active"]
    recent = sorted((w for w in items if w.status != "active"), key=lambda w: -w.finished_at)[:3]
    if not active and not recent:
        return "You're not watching for anything, sir."
    lines = [describe(w) for w in active] or ["No active watches."]
    for w in recent:
        lines.append(f"Finished ({w.status}) {w.label}" + (f": {w.answer}" if w.answer else ""))
    used = _checks_today(items, _today(time.time()))
    lines.append(f"{used} of {max_checks_per_day()} checks used today.")
    return "\n".join(lines)
