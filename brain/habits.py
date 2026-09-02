"""Behind-the-scenes habit learning.

Durable facts (``brain/memory.py``) are things Nick *said*. Habits are things
Nick *does*: when he talks to JARVIS, which tools he leans on, which devices,
playlists, and apps he asks for most. Every finished turn appends one small
event here; a bounded digest is derived at prompt-build time so it stays
current as behaviour changes and never needs manual curation.

No model call is involved. The store is a bounded JSON event log at
``memory/habits.json``; aggregates are recomputed on read and cached behind
the file's modification time.
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HABITS_FILE = _PROJECT_ROOT / "memory" / "habits.json"
_MAX_TURNS = 2000
_LOCK = threading.Lock()

# Which argument carries the human-meaningful target for a tool, so counts
# group by *what* Nick asked for rather than only which tool ran.
_TARGET_ARGS: dict[str, str] = {
    "control_device": "command",
    "activate_home_action": "name",
    "call_home_service": "entity_id",
    "play_music": "query",
    "transfer_playback": "device_name",
    "open_app": "name",
    "focus_app": "name",
    "close_app": "name",
    "find_movie": "title",
    "find_series": "title",
    "set_timer": "message",
    "add_reminder": "text",
    "get_weather": "location",
}

# Meta/memory tools say nothing about Nick's routine.
_IGNORED_TOOLS = {
    "remember_fact", "remember_profile_record", "forget_fact", "update_fact",
    "adjust_fact_confidence", "list_memory_facts", "find_memory_contradictions",
    "propose_memory_fact", "list_memory_proposals", "apply_memory_proposal",
    "reject_memory_proposal", "search_memory", "search_session_summaries",
    "list_debug_reports", "inspect_debug_report", "clear_conversation",
    "conversation_history_stats", "get_setting", "list_settings", "update_setting",
    "get_time", "observed_habits", "capture_note",
}

_TOOL_LABELS: dict[str, str] = {
    "control_device": "home control",
    "activate_home_action": "home scenes",
    "call_home_service": "home services",
    "home_status": "home status",
    "play_music": "music",
    "resume_music": "music",
    "pause_music": "music",
    "skip_track": "music",
    "previous_track": "music",
    "current_track": "music",
    "set_volume": "music",
    "open_app": "app launching",
    "focus_app": "app launching",
    "close_app": "app launching",
    "look_at_screen": "screen questions",
    "read_clipboard": "clipboard",
    "write_clipboard": "clipboard",
    "get_weather": "weather",
    "morning_briefing": "briefings",
    "set_timer": "timers",
    "add_reminder": "reminders",
    "todays_agenda": "calendar",
    "next_event": "calendar",
    "find_movie": "media requests",
    "add_movie": "media requests",
    "find_series": "media requests",
    "add_series": "media requests",
    "download_status": "downloads",
    "media_queue": "downloads",
    "web_search": "web search",
}

_cache_key: tuple[float, int] | None = None
_cache_value: str = ""


def _period(hour: int) -> str:
    if 5 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 16:
        return "afternoon"
    if 17 <= hour <= 21:
        return "evening"
    return "late night"


def _min_turns() -> int:
    try:
        return max(1, min(500, int(os.getenv("HABITS_MIN_TURNS", "10") or 10)))
    except (TypeError, ValueError):
        return 10


def _load() -> dict[str, Any]:
    if not _HABITS_FILE.exists():
        return {"turns": []}
    try:
        data = json.loads(_HABITS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("turns"), list):
            return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read habits file (%s); starting fresh.", exc)
    return {"turns": []}


def _save(data: dict[str, Any]) -> bool:
    try:
        _HABITS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = _HABITS_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_HABITS_FILE)
        return True
    except OSError as exc:
        log.error("Could not write habits file: %s", exc)
        return False


def _target_for(name: str, args: dict[str, Any]) -> str:
    arg = _TARGET_ARGS.get(name)
    if not arg:
        return ""
    value = " ".join(str(args.get(arg) or "").split()).lower()
    return value[:80]


def record_turn(
    tools: list[tuple[str, dict[str, Any]]] | None,
    *,
    at: datetime | None = None,
) -> None:
    """Append one finished turn. Cheap, and safe to call from any thread."""
    moment = at or datetime.now().astimezone()
    used: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for name, args in tools or []:
        if not name or name in _IGNORED_TOOLS:
            continue
        target = _target_for(name, args if isinstance(args, dict) else {})
        if (name, target) in seen:
            continue
        seen.add((name, target))
        used.append([name, target])
    entry = {
        "t": moment.isoformat(timespec="minutes"),
        "h": moment.hour,
        "d": moment.weekday(),
        "tools": used,
    }
    with _LOCK:
        data = _load()
        data["turns"].append(entry)
        data["turns"] = data["turns"][-_MAX_TURNS:]
        _save(data)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _label(name: str) -> str:
    return _TOOL_LABELS.get(name, name.replace("_", " "))


def _describe_target(name: str, target: str) -> str:
    verb = {
        "control_device": "asks for",
        "activate_home_action": "runs",
        "call_home_service": "calls",
        "play_music": "plays",
        "transfer_playback": "moves playback to",
        "open_app": "opens",
        "focus_app": "switches to",
        "close_app": "closes",
        "find_movie": "looks up",
        "find_series": "looks up",
        "set_timer": "sets a timer for",
        "add_reminder": "sets a reminder about",
        "get_weather": "checks weather for",
    }.get(name, "uses")
    return f"{verb} \"{target}\""


def summarize(turns: list[dict[str, Any]], *, now: datetime | None = None) -> list[str]:
    """Turn the raw event log into a handful of plain observations."""
    if not turns:
        return []
    now = now or datetime.now().astimezone()
    lines: list[str] = []
    total = len(turns)

    periods = Counter(_period(int(turn.get("h", 0))) for turn in turns)
    top_period, top_count = periods.most_common(1)[0]
    weekend = sum(1 for turn in turns if int(turn.get("d", 0)) >= 5)
    weekday_note = ""
    if total >= 14:
        weekend_share = weekend / total
        if weekend_share <= 0.15:
            weekday_note = ", almost always on weekdays"
        elif weekend_share >= 0.5:
            weekday_note = ", mostly on weekends"
    lines.append(
        f"Most conversations happen in the {top_period} "
        f"({round(100 * top_count / total)}% of {total} turns{weekday_note})."
    )

    cutoff = now - timedelta(days=7)
    recent = 0
    for turn in turns:
        moment = _parse_time(str(turn.get("t", "")))
        if moment is None:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=now.tzinfo)
        if moment >= cutoff:
            recent += 1
    if recent:
        lines.append(f"{recent} turns in the last seven days.")

    categories: Counter[str] = Counter()
    targets: Counter[tuple[str, str]] = Counter()
    target_periods: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for turn in turns:
        hour = int(turn.get("h", 0))
        used_categories: set[str] = set()
        for item in turn.get("tools") or []:
            if not isinstance(item, list) or not item:
                continue
            name = str(item[0])
            target = str(item[1]) if len(item) > 1 else ""
            used_categories.add(_label(name))
            if target:
                key = (name, target)
                targets[key] += 1
                target_periods[key][_period(hour)] += 1
        categories.update(used_categories)

    if categories:
        top = ", ".join(f"{label} ({count})" for label, count in categories.most_common(3))
        lines.append(f"Leans on: {top}.")

    frequent = [(key, count) for key, count in targets.most_common(6) if count >= 3][:4]
    for (name, target), count in frequent:
        when, when_count = target_periods[(name, target)].most_common(1)[0]
        when_note = f", usually {when}" if when_count / count >= 0.6 else ""
        lines.append(f"Often {_describe_target(name, target)} ({count}x{when_note}).")

    return lines


def render_habits_block() -> str:
    """Bounded digest for the system prompt, or "" until enough turns exist."""
    global _cache_key, _cache_value
    try:
        stat = _HABITS_FILE.stat()
        key: tuple[float, int] | None = (stat.st_mtime, stat.st_size)
    except OSError:
        key = None
    if key is not None and key == _cache_key:
        return _cache_value

    turns = _load()["turns"]
    block = ""
    if len(turns) >= _min_turns():
        block = "\n".join(f"- {line}" for line in summarize(turns))
    _cache_key = key
    _cache_value = block
    return block


def describe_habits() -> str:
    """Human-readable version for the observed_habits tool."""
    turns = _load()["turns"]
    if len(turns) < _min_turns():
        return (
            f"Not enough history yet ({len(turns)} of {_min_turns()} turns) "
            "to say anything about Nick's habits."
        )
    return "What I've noticed:\n" + "\n".join(f"- {line}" for line in summarize(turns))
