"""Skill requests: what Nick asked for that JARVIS had no tool to do.

Nick, 2026-11-19: "I want JARVIS to create new skills if he doesn't have
them... instead of erroring out, or even just storing it... eventually one day
I don't wanna come to Claude to adjust JARVIS." This is the first slice: when
a request needs a capability no tool covers, JARVIS says so plainly and files
it here (``request_skill``) instead of improvising or pretending. Asking for
the same thing again bumps its count, so the most-wanted skills rise to the
top. The companion shows the open ones (Control → Skill requests) where Nick
marks them done or not needed; a future self-patch flow drafts tools for the
top requests (FUTURE_SESSIONS.md, Tier 1 #1).

Stored in ``memory/skill_requests.json``. No model calls here.
"""
from __future__ import annotations

import difflib
import json
import re
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "skill_requests.json"
_LOCK = RLock()
_MAX = 200
STATUSES = ("open", "done", "declined")
# Two summaries this alike are the same wish asked again.
_SAME = 0.72


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []


def _save(items: list[dict[str, Any]]) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items[-_MAX:], indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_FILE)
    except OSError as exc:
        log.warning("Could not save skill requests: %s", exc)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def _clip(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def file(summary: str, ask: str = "", why: str = "", now: float | None = None) -> dict[str, Any]:
    """Record one wish, or count it again if an open one matches. Returns the record."""
    now = time.time() if now is None else now
    summary, ask, why = _clip(summary, 160), _clip(ask, 400), _clip(why, 300)
    if not summary:
        raise ValueError("A skill request needs a summary")
    key = _norm(summary)
    with _LOCK:
        items = _load()
        match = max(
            (r for r in items if r.get("status") == "open"),
            key=lambda r: difflib.SequenceMatcher(None, key, _norm(str(r.get("summary", "")))).ratio(),
            default=None,
        )
        if match is not None and difflib.SequenceMatcher(None, key, _norm(str(match.get("summary", "")))).ratio() >= _SAME:
            match["count"] = int(match.get("count") or 1) + 1
            match["last_at"] = now
            if ask:
                asks = [a for a in match.get("asks", []) if a != ask]
                match["asks"] = (asks + [ask])[-5:]
            if why and not match.get("why"):
                match["why"] = why
            _save(items)
            return dict(match)
        record = {"id": uuid.uuid4().hex[:8], "summary": summary, "asks": [ask] if ask else [], "why": why,
                  "count": 1, "created_at": now, "last_at": now, "status": "open"}
        items.append(record)
        _save(items)
        return dict(record)


def listing(status: str = "open") -> list[dict[str, Any]]:
    """Most-asked first, then most recent."""
    with _LOCK:
        items = _load()
    wanted = [r for r in items if status == "all" or r.get("status") == status]
    return sorted(wanted, key=lambda r: (-int(r.get("count") or 1), -float(r.get("last_at") or 0)))


def set_status(request_id: str, status: str) -> dict[str, Any] | None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    with _LOCK:
        items = _load()
        for record in items:
            if record.get("id") == request_id:
                record["status"] = status
                record["closed_at" if status != "open" else "reopened_at"] = time.time()
                _save(items)
                return dict(record)
    return None


def annotate(request_id: str, **fields: Any) -> dict[str, Any] | None:
    """Attach the drafter's outcome: ``draft_skill_id`` and/or ``note``."""
    allowed = {k: v for k, v in fields.items() if k in {"draft_skill_id", "note", "drafting"}}
    with _LOCK:
        items = _load()
        for record in items:
            if record.get("id") == request_id:
                record.update(allowed)
                _save(items)
                return dict(record)
    return None


# --- tool handlers --------------------------------------------------------------

def tool_request(args: dict[str, Any]) -> str:
    try:
        record = file(str(args.get("summary") or ""), str(args.get("ask") or ""), str(args.get("why") or ""))
    except ValueError as exc:
        return f"Couldn't file the skill request: {exc}"
    if record["count"] > 1:
        return (f"Logged again: \"{record['summary']}\" has now been asked for {record['count']} times. "
                "Tell Nick plainly you can't do it yet and that it's on his Skill requests list.")
    return (f"Filed skill request {record['id']}: \"{record['summary']}\". Tell Nick plainly you can't do it "
            "yet and that it's on his Skill requests list in the app.")


def tool_list(args: dict[str, Any]) -> str:
    status = str(args.get("status") or "open")
    items = listing(status if status in STATUSES + ("all",) else "open")
    if not items:
        return f"No {status} skill requests, sir."
    lines = [f"{len(items)} {status} skill request(s), most asked first:"]
    for r in items[:15]:
        times = f" ({r['count']}x)" if int(r.get("count") or 1) > 1 else ""
        lines.append(f"- [{r['id']}] {r['summary']}{times} ({r.get('status')})")
    return "\n".join(lines)
