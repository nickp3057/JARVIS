"""Learned skills: new abilities JARVIS composes from the tools he already has.

The second half of Nick's "create new skills" goal (2026-11-19; first half
is ``brain/skill_requests.py``). A skill is a named, parameterised recipe of
existing tool calls, e.g. ``date_night(playlist)`` = Movie scene, play the
playlist on the speaker, dim the lamp. It is data, not code: JARVIS (in chat,
or the skill drafter subagent working on a skill request) proposes one with
``propose_skill``; it does nothing until Nick approves it (his "yes" in chat,
or Approve on the Control tab's Skills card). Active skills are listed in
every turn's ``<turn_context>`` and run with ``run_skill``.

Bounded on purpose:

* Steps may only use ``ALLOWED_TOOLS``: everyday, reversible actions and
  read-only lookups. No trading, settings, repairs, memory edits, deletions,
  browser control or other skills.
* Every step still goes through ``brain.tools.execute_tool``, so each tool's
  own gates (guest voices, confirmations) apply exactly as if asked directly.
* At most ``MAX_STEPS`` steps; a step that fails stops the run.

Wishes no recipe can meet (a new device or integration) get a "what it'd
take" note on the skill request instead. Stored in ``memory/skills.json``.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "skills.json"
_LOCK = RLock()
MAX_STEPS = 8
ALLOWED_TOOLS = frozenset({
    # house, scenes, room
    "activate_home_action", "control_device", "call_home_service", "home_status", "list_scenes",
    "barn_door_protocol", "light_sync", "announce_in_room", "show_screen",
    # music and TV
    "play_music", "pause_music", "resume_music", "skip_track", "previous_track", "current_track",
    "set_volume", "transfer_playback", "play_on_tv", "tv_power",
    # time, plans, notes
    "set_timer", "list_timers", "add_reminder", "list_reminders", "todays_agenda", "next_event",
    "get_time", "get_weather", "morning_briefing", "study_brief_preview", "capture_note",
    "add_to_shopping_list", "shopping_list",
    # read-only looks
    "download_status", "media_queue", "upcoming_media", "find_movie", "find_series",
    "portfolio_summary", "check_prices",
})
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("id")] if isinstance(data, list) else []


def _save(items: list[dict[str, Any]]) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items[-100:], indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_FILE)
    except OSError as exc:
        log.warning("Could not save skills: %s", exc)


def _placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_PLACEHOLDER_RE.findall(value))
    if isinstance(value, dict):
        return set().union(*(_placeholders(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_placeholders(v) for v in value)) if value else set()
    return set()


def _fill(value: Any, args: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER_RE.sub(lambda m: str(args.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: _fill(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, args) for v in value]
    return value


def validate(name: str, description: str, params: list[dict[str, Any]], steps: list[dict[str, Any]]) -> str | None:
    """Why this recipe can't be a skill, or None when it can."""
    from brain import tools
    if not _NAME_RE.match(name or ""):
        return "name must be snake_case, 3-41 characters (e.g. date_night)"
    if name in {t["name"] for t in tools._CUSTOM_TOOLS}:
        return f"{name} is already a built-in tool"
    if not (description or "").strip():
        return "a skill needs a one-line description"
    if not steps or len(steps) > MAX_STEPS:
        return f"a skill needs 1-{MAX_STEPS} steps"
    declared = set()
    for param in params:
        pname = str(param.get("name") or "")
        if not _NAME_RE.match(pname):
            return f"parameter '{pname}' must be snake_case"
        declared.add(pname)
    for index, step in enumerate(steps, 1):
        tool = str(step.get("tool") or "")
        if tool not in ALLOWED_TOOLS:
            return f"step {index} uses '{tool}', which skills may not use (allowed: everyday actions and lookups)"
        if not isinstance(step.get("input", {}), dict):
            return f"step {index}'s input must be an object"
        missing = _placeholders(step.get("input", {})) - declared
        if missing:
            return f"step {index} uses undeclared parameter(s): {', '.join(sorted(missing))}"
    return None


def propose(name: str, description: str, params: list[dict[str, Any]] | None, steps: list[dict[str, Any]],
            request_id: str = "", proposed_by: str = "chat") -> dict[str, Any]:
    """Store a draft. Raises ValueError when the recipe isn't allowed."""
    params = [{"name": str(p.get("name") or ""), "description": " ".join(str(p.get("description") or "").split())[:120]}
              for p in (params or []) if isinstance(p, dict)]
    steps = [{"tool": str(s.get("tool") or ""), "input": s.get("input") or {}} for s in (steps or []) if isinstance(s, dict)]
    name = (name or "").strip().lower()
    problem = validate(name, description, params, steps)
    if problem:
        raise ValueError(problem)
    record = {"id": uuid.uuid4().hex[:8], "name": name, "description": " ".join(description.split())[:200],
              "params": params, "steps": steps, "status": "draft", "request_id": request_id,
              "proposed_by": proposed_by, "created_at": time.time(), "uses": 0, "last_used": None}
    with _LOCK:
        items = [s for s in _load() if not (s.get("name") == name and s.get("status") == "draft")]
        items.append(record)
        _save(items)
    return dict(record)


def set_status(skill_id: str, status: str) -> dict[str, Any] | None:
    """draft -> active (Nick approved), active <-> disabled, or "deleted"."""
    if status not in {"active", "disabled", "deleted"}:
        raise ValueError("status must be active, disabled or deleted")
    with _LOCK:
        items = _load()
        record = next((s for s in items if s.get("id") == skill_id), None)
        if record is None:
            return None
        if status == "deleted":
            items.remove(record)
        else:
            if status == "active":
                # Re-checked at approval: the allowlist may have changed since the draft.
                problem = validate(record["name"], record["description"], record["params"], record["steps"])
                if problem:
                    raise ValueError(problem)
                # One active skill per name: the newer one replaces it.
                for other in items:
                    if other is not record and other.get("name") == record["name"] and other.get("status") == "active":
                        other["status"] = "disabled"
            record["status"] = status
            record[f"{status}_at"] = time.time()
        _save(items)
    if status == "active" and record.get("request_id"):
        from brain import skill_requests
        skill_requests.set_status(str(record["request_id"]), "done")
    return dict(record)


def listing(status: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        items = _load()
    return [dict(s) for s in reversed(items) if status is None or s.get("status") == status]


def describe(record: dict[str, Any]) -> str:
    params = ", ".join(p["name"] for p in record.get("params") or [])
    steps = "; ".join(f"{s['tool']}({json.dumps(s.get('input') or {}, ensure_ascii=False)})" for s in record["steps"])
    return f"{record['name']}({params}): {record['description']} Steps: {steps}"


def render_block() -> str:
    """Active skills for this turn's context, or ""."""
    active = listing("active")
    if not active:
        return ""
    lines = []
    for record in active[:20]:
        params = ", ".join(f"{p['name']}" for p in record.get("params") or [])
        lines.append(f"- {record['name']}({params}): {record['description']}")
    return ("\n\n# Skills you've learned\nRun one with run_skill(name, args) when Nick asks for what it does.\n"
            + "\n".join(lines))


def run(name: str, args: dict[str, Any] | None, session_id: str | None = None) -> str:
    from brain import tool_health, tools
    record = next((s for s in listing("active") if s["name"] == (name or "").strip().lower()), None)
    if record is None:
        return f"I don't have an active skill called '{name}', sir."
    args = {str(k): str(v) for k, v in (args or {}).items()}
    missing = [p["name"] for p in record.get("params") or [] if not args.get(p["name"])]
    if missing:
        return f"The {record['name']} skill needs: {', '.join(missing)}."
    outputs: list[str] = []
    for index, step in enumerate(record["steps"], 1):
        if step["tool"] not in ALLOWED_TOOLS:  # the allowlist may have shrunk since approval
            return f"Stopped at step {index}: {step['tool']} is no longer allowed in skills."
        result = tools.execute_tool(step["tool"], _fill(step.get("input") or {}, args), session_id)
        outputs.append(f"{index}. {step['tool']}: {str(result)[:300]}")
        if tool_health.looks_like_failure(str(result)):
            outputs.append(f"Stopped: step {index} failed, so the rest didn't run.")
            break
    with _LOCK:
        items = _load()
        for item in items:
            if item.get("id") == record["id"]:
                item["uses"] = int(item.get("uses") or 0) + 1
                item["last_used"] = time.time()
        _save(items)
    return f"Ran the {record['name']} skill:\n" + "\n".join(outputs)


# --- tool handlers --------------------------------------------------------------

def tool_propose(args: dict[str, Any]) -> str:
    session_id = args.get("_jarvis_session_id")
    try:
        record = propose(str(args.get("name") or ""), str(args.get("description") or ""), args.get("params"),
                         args.get("steps") or [], str(args.get("request_id") or ""))
    except ValueError as exc:
        return f"That can't be a skill: {exc}. If it needs a new integration, use request_skill instead."
    staged = (f"Drafted skill {record['id']}: {describe(record)} It does nothing until Nick approves it "
              "(Control tab → Skills, Approve).")
    if not session_id:
        return staged
    from brain import confirm_gate
    return staged + " " + confirm_gate.propose(
        session_id, f"learn the skill '{record['name']}' [skill {record['id']}]",
        lambda: _activate_reply(record["id"]))


def _activate_reply(skill_id: str) -> str:
    try:
        record = set_status(skill_id, "active")
    except ValueError as exc:
        return f"Couldn't activate it: {exc}"
    return f"Learned: {record['name']} is active, sir." if record else "That draft is gone, sir."


def tool_run(args: dict[str, Any]) -> str:
    return run(str(args.get("name") or ""), args.get("args") if isinstance(args.get("args"), dict) else {},
               args.get("_jarvis_session_id"))
