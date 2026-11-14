"""Lessons: what JARVIS learned from his own mistakes.

The fourth learning store, and the only one about JARVIS rather than Nick:

- ``brain/memory.py`` holds facts about Nick;
- ``brain/standing_orders.py`` holds Nick's explicit rules ("stop doing X");
- ``brain/habits.py`` holds derived usage patterns;
- this module holds one-line lessons the reflector (``brain/reflector.py``)
  drew from turns that went badly: Nick had to repeat himself, said "no,
  that's wrong", or a tool failed while the reply claimed success.

Lessons decay. Each has a weight of ``hits`` halved every
``LESSONS_HALF_LIFE_DAYS`` since it was last reinforced; a lesson that fades
below ``_PRUNE_SCORE`` is dropped. Learning the same lesson again (same tool,
similar wording) reinforces the existing one instead of adding a duplicate.
The store is capped at ``LESSONS_MAX``; the weakest go first.

The top few, ranked by weight and by overlap with Nick's current message,
ride in the per-turn ``<turn_context>`` block (``render_block``), never the
system prompt, so the persona stays cacheable.

Persisted to ``memory/lessons.json``. Nothing here calls the model.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "lessons.json"
_lock = threading.Lock()

MAX_CHARS = 220
_PRUNE_SCORE = 0.15
_SIMILAR = 0.6
_DAY = 86400.0
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the to of and or in on at for with is it be that this when if you your i me my "
    "not no do does did dont don't before after from by as are was were then than so".split()
)


# --- Settings ----------------------------------------------------------------

def enabled() -> bool:
    return (os.getenv("LESSONS_ENABLED", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}


def _int_setting(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)) or default)))
    except (TypeError, ValueError):
        return default


def _max_lessons() -> int:
    return _int_setting("LESSONS_MAX", 40, 1, 500)


def _in_context() -> int:
    return _int_setting("LESSONS_IN_CONTEXT", 4, 0, 20)


def _half_life_days() -> float:
    try:
        return max(0.5, float(os.getenv("LESSONS_HALF_LIFE_DAYS", "14") or 14))
    except (TypeError, ValueError):
        return 14.0


# --- Storage -----------------------------------------------------------------

def _path() -> Path:
    return _FILE


def _load() -> list[dict[str, Any]]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("Could not read lessons: %s", exc)
        return []
    items = raw.get("lessons", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [dict(i) for i in items if isinstance(i, dict) and str(i.get("text", "")).strip()]


def _save(items: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"lessons": items}, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _clean(text: Any, limit: int = MAX_CHARS) -> str:
    return " ".join(str(text or "").split())[:limit].strip()


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOP and len(w) > 1}


def similarity(a: str, b: str) -> float:
    """0..1: the better of character-sequence and content-word overlap."""
    a_l, b_l = " ".join(a.lower().split()), " ".join(b.lower().split())
    if not a_l or not b_l:
        return 0.0
    seq = SequenceMatcher(None, a_l, b_l).ratio()
    wa, wb = _words(a_l), _words(b_l)
    jac = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
    return max(seq, jac)


# --- Decay -------------------------------------------------------------------

def score(item: dict[str, Any], now: float | None = None) -> float:
    """Current weight: hits halved every half-life since last reinforced."""
    now = time.time() if now is None else now
    try:
        hits = max(1, int(item.get("hits", 1)))
        last = float(item.get("last_reinforced") or item.get("created") or now)
    except (TypeError, ValueError):
        return 0.0
    age_days = max(0.0, now - last) / _DAY
    return hits * 0.5 ** (age_days / _half_life_days())


def _prune(items: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    kept = [i for i in items if score(i, now) >= _PRUNE_SCORE]
    cap = _max_lessons()
    if len(kept) > cap:
        kept.sort(key=lambda i: score(i, now), reverse=True)
        kept = kept[:cap]
        kept.sort(key=lambda i: float(i.get("created") or 0))
    return kept


# --- Public API --------------------------------------------------------------

def learn(text: Any, *, tool: Any = "", situation: Any = "", signal: Any = "",
          now: float | None = None) -> str:
    """Record a lesson, or reinforce the matching one. Returns what happened."""
    cleaned = _clean(text)
    if not cleaned:
        return "A lesson needs some wording."
    tool_name = _clean(tool, 64)
    now = time.time() if now is None else now
    with _lock:
        items = _prune(_load(), now)
        for item in items:
            if _clean(item.get("tool"), 64).lower() != tool_name.lower():
                continue
            if similarity(cleaned, str(item.get("text", ""))) >= _SIMILAR:
                item["hits"] = int(item.get("hits", 1)) + 1
                item["last_reinforced"] = now
                if situation:
                    item["situation"] = _clean(situation)
                _save(items)
                log.info("Lesson %s reinforced (%d hits): %s", item["id"], item["hits"], item["text"])
                return f"Reinforced lesson {item['id']}: {item['text']}"
        item = {
            "id": uuid.uuid4().hex[:8],
            "text": cleaned,
            "tool": tool_name,
            "situation": _clean(situation),
            "signal": _clean(signal, 40),
            "created": now,
            "last_reinforced": now,
            "hits": 1,
        }
        items.append(item)
        _save(_prune(items, now))
    log.info("Lesson learned %s: %s", item["id"], cleaned)
    return f"Learned lesson {item['id']}: {cleaned}"


def all_lessons(now: float | None = None) -> list[dict[str, Any]]:
    """Live lessons (faded ones excluded), strongest first, with their score."""
    now = time.time() if now is None else now
    items = _prune(_load(), now)
    out = [{**i, "score": round(score(i, now), 3)} for i in items]
    out.sort(key=lambda i: i["score"], reverse=True)
    return out


def list_lessons() -> str:
    items = all_lessons()
    if not items:
        return "No lessons on file. Either I have been flawless, sir, or nobody has told me otherwise."
    lines = []
    for item in items:
        tool = f" [{item['tool']}]" if item.get("tool") else ""
        hits = int(item.get("hits", 1))
        lines.append(f"{item['id']}{tool}: {item['text']} ({hits} time{'s' if hits != 1 else ''})")
    return "Lessons learned from my own mistakes:\n" + "\n".join(lines)


def forget_lesson(identifier: Any) -> str:
    """Remove by exact id, a unique text fragment, or 'all'."""
    needle = _clean(identifier, 200)
    if not needle:
        return "Say which lesson to forget, by id or a few of its words."
    with _lock:
        items = _load()
        if needle.lower() == "all":
            _save([])
            return f"Forgot all {len(items)} lesson{'s' if len(items) != 1 else ''}."
        matches = [i for i in items if str(i.get("id")) == needle]
        if not matches:
            matches = [i for i in items if needle.lower() in str(i.get("text", "")).lower()]
        if not matches:
            return f"No lesson matches '{needle}'."
        if len(matches) > 1:
            return "Several lessons match. Use the id: " + "; ".join(f"{m['id']}: {m['text']}" for m in matches)
        target = matches[0]
        _save([i for i in items if i is not target])
    log.info("Lesson forgotten %s: %s", target["id"], target["text"])
    return f"Forgot lesson {target['id']}: {target['text']}"


def render_block(latest_user_text: str = "", now: float | None = None) -> str:
    """The few lessons most relevant to this message, or "" (off / none)."""
    limit = _in_context()
    if not enabled() or limit <= 0:
        return ""
    items = all_lessons(now)
    if not items:
        return ""
    query = _words(latest_user_text or "")

    def relevance(item: dict[str, Any]) -> float:
        bag = _words(f"{item.get('text', '')} {item.get('situation', '')} {str(item.get('tool', '')).replace('_', ' ')}")
        overlap = len(query & bag)
        return item["score"] * (1.0 + overlap)

    ranked = sorted(items, key=relevance, reverse=True)[:limit]
    lines = []
    for item in ranked:
        tool = f" ({item['tool']})" if item.get("tool") else ""
        lines.append(f"- {item['text']}{tool}")
    return "\n".join(lines)
