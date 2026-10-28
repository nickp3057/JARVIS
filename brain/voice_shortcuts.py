"""Voice shortcuts JARVIS learns from Nick's habits.

When Nick says the same thing to the room voice and Claude answers it the
same way, with one simple home or music action that worked ("cosy lights"
-> activate scene Movie), the second time it happens (``VOICE_SHORTCUT_AFTER``)
the phrase becomes a shortcut: from then on the room voice runs that action
itself, at quick-command speed, with no model call.

Only low-stakes, undoable actions are learnable (lights, scenes, music); the
store lives in ``memory/voice_shortcuts.json``. Nick can list or forget them
by asking (the ``voice_shortcuts`` tool). A shortcut that stops working is
dropped, so the next time goes back through Claude.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from brain import json_store

_FILE = Path(__file__).resolve().parents[1] / "memory" / "voice_shortcuts.json"
_lock = threading.Lock()
_MAX_SEEN = 200
_MAX_LEARNED = 100

LEARNABLE = frozenset({
    "control_device", "activate_home_action", "set_light_brightness", "set_light_color",
    "play_music", "pause_music", "resume_music", "skip_track", "previous_track", "set_volume",
})
_FILLER = re.compile(r"\b(please|jarvis|hey|the|could you|can you|would you|for me|now)\b")


def normalize(phrase: str) -> str:
    text = re.sub(r"[^\w\s']", " ", (phrase or "").lower())
    return re.sub(r"\s+", " ", _FILLER.sub(" ", text)).strip()


def _learn_after() -> int:
    try:
        return max(1, int(os.getenv("VOICE_SHORTCUT_AFTER", "2")))
    except ValueError:
        return 2


def _load() -> dict[str, dict[str, Any]]:
    rows = json_store.load(_FILE, "shortcuts", lambda raw: dict(raw, phrase=str(raw["phrase"])))
    return {row["phrase"]: row for row in rows}


def _save(rows: dict[str, dict[str, Any]]) -> None:
    learned = sorted((r for r in rows.values() if r.get("learned")), key=lambda r: r.get("last", 0))[-_MAX_LEARNED:]
    seen = sorted((r for r in rows.values() if not r.get("learned")), key=lambda r: r.get("last", 0))[-_MAX_SEEN:]
    json_store.save(_FILE, "shortcuts", learned + seen)


def observe(phrase: str, tools: list[tuple[str, dict[str, Any]]]) -> bool:
    """A room-voice turn that Claude finished with one action that worked.
    True when this made the phrase a shortcut."""
    key = normalize(phrase)
    if not key or len(key.split()) > 8 or len(tools) != 1 or tools[0][0] not in LEARNABLE:
        return False
    tool, args = tools[0][0], {k: v for k, v in tools[0][1].items() if not k.startswith("_")}
    with _lock:
        rows = _load()
        row = rows.get(key)
        if row is not None and row.get("learned"):
            return False  # already a shortcut
        if row is None or row["tool"] != tool or row["args"] != args:
            row = {"phrase": key, "tool": tool, "args": args, "count": 0}  # new, or answered differently: restart
        row["count"] += 1
        row["last"] = time.time()
        learned_now = row["count"] >= _learn_after()
        if learned_now:
            row.update(learned=True, learned_at=row["last"], uses=0)
        rows[key] = row
        _save(rows)
    return learned_now


def lookup(phrase: str) -> tuple[str, dict[str, Any]] | None:
    row = _load().get(normalize(phrase))
    return (row["tool"], dict(row["args"])) if row and row.get("learned") else None


def used(phrase: str, worked: bool) -> None:
    """Count a use; a shortcut that failed is forgotten (Claude handles it next time)."""
    key = normalize(phrase)
    with _lock:
        rows = _load()
        if key not in rows:
            return
        if worked:
            rows[key]["uses"] = rows[key].get("uses", 0) + 1
            rows[key]["last"] = time.time()
        else:
            del rows[key]
        _save(rows)


def forget(phrase: str) -> int:
    """Forget one learned shortcut, or every one with "all"."""
    key = normalize(phrase)
    with _lock:
        rows = _load()
        doomed = [p for p, r in rows.items() if r.get("learned") and (key == "all" or p == key)]
        for p in doomed:
            del rows[p]
        if doomed:
            _save(rows)
    return len(doomed)


def describe() -> str:
    learned = [r for r in _load().values() if r.get("learned")]
    if not learned:
        return "I haven't learned any voice shortcuts yet, sir."
    parts = [f'"{r["phrase"]}" -> {r["tool"]}({", ".join(f"{k}={v}" for k, v in r["args"].items())}), '
             f'used {r.get("uses", 0)}x' for r in sorted(learned, key=lambda r: r["phrase"])]
    return "Voice shortcuts I've learned: " + "; ".join(parts) + "."
