"""Reminders and to-dos for JARVIS.

Distinct from timers (which are short countdowns). A reminder is either:
- a *timed* reminder with a due time ("remind me at 3pm to call mom"), which
  JARVIS speaks aloud when it comes due, or
- an open *to-do* with no due time ("add milk to my list"), which simply lives
  on the list until completed.

Persisted to memory/reminders.json so the list survives restarts. Timed
reminders that came due while JARVIS was closed are announced on next launch.
The store exposes pop_due() which the runtime folds into its proactive
announcement path alongside timers - each due reminder has a ``.message`` so it
flows through the existing voice announcement unchanged.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from brain import json_store
from jarvis_logger import get_logger

log = get_logger(__name__)

_REMINDERS_FILE = Path(__file__).resolve().parent.parent / "memory" / "reminders.json"


@dataclass
class Reminder:
    id: str
    text: str
    due_at: float | None = None     # Unix ts; None = open to-do (no due time)
    created_at: float = 0.0
    done: bool = False
    announced: bool = False         # spoken announcement already made

    @property
    def message(self) -> str:
        """Alias so reminders flow through the same announce path as timers."""
        return self.text

    def is_due(self, now: float | None = None) -> bool:
        if self.due_at is None or self.done:
            return False
        return self.due_at <= (now if now is not None else time.time())

    @classmethod
    def from_dict(cls, raw: dict) -> "Reminder":
        """A reminder saved by ``asdict``; KeyError/TypeError/ValueError if malformed."""
        return cls(
            id=str(raw["id"]),
            text=str(raw.get("text", "")),
            due_at=float(raw["due_at"]) if raw.get("due_at") is not None else None,
            created_at=float(raw.get("created_at", 0.0)),
            done=bool(raw.get("done", False)),
            announced=bool(raw.get("announced", False)),
        )

    def remaining_seconds(self) -> float | None:
        if self.due_at is None:
            return None
        return max(0.0, self.due_at - time.time())


def _order(r: Reminder) -> tuple[bool, float]:
    """Timed reminders soonest first, then open to-dos."""
    return (r.due_at is None, r.due_at or 0.0)


class _ReminderStore:
    def __init__(self) -> None:
        self._items: list[Reminder] = []
        self._lock = threading.Lock()
        self._load_from_disk()

    # --- Persistence --------------------------------------------------------

    def _persist_locked(self) -> None:
        json_store.save(_REMINDERS_FILE, "reminders", [asdict(r) for r in self._items])

    def _load_from_disk(self) -> None:
        restored = json_store.load(_REMINDERS_FILE, "reminders", Reminder.from_dict)
        with self._lock:
            self._items = sorted(restored, key=_order)
        if restored:
            log.info("Restored %d reminder(s) from disk.", len(restored))

    # --- Mutations ----------------------------------------------------------

    def add(self, text: str, due_at: float | None = None) -> Reminder:
        text = (text or "").strip() or "Reminder"
        reminder = Reminder(
            id="r" + uuid.uuid4().hex[:7],
            text=text,
            due_at=due_at,
            created_at=time.time(),
        )
        with self._lock:
            self._items.append(reminder)
            self._items.sort(key=_order)
            self._persist_locked()
        log.info("Reminder added: id=%s due=%s (%s)", reminder.id, due_at, text)
        return reminder

    def complete(self, reminder_id: str) -> str:
        reminder_id = (reminder_id or "").strip()
        with self._lock:
            for r in self._items:
                if r.id == reminder_id:
                    r.done = True
                    self._persist_locked()
                    return f"Marked done: {r.text}"
        return f"No reminder with id {reminder_id}."

    def rename(self, reminder_id: str, text: str) -> Reminder | None:
        reminder_id, text = (reminder_id or "").strip(), (text or "").strip()
        if not text:
            return None
        with self._lock:
            for r in self._items:
                if r.id == reminder_id:
                    r.text = text
                    self._persist_locked()
                    return r
        return None

    def delete(self, reminder_id: str) -> bool:
        reminder_id = (reminder_id or "").strip()
        with self._lock:
            before = len(self._items)
            self._items = [r for r in self._items if r.id != reminder_id]
            removed = len(self._items) != before
            if removed:
                self._persist_locked()
        return removed

    # --- Reading ------------------------------------------------------------

    def list_items(self, include_done: bool = False) -> list[Reminder]:
        with self._lock:
            return [r for r in self._items if include_done or not r.done]

    def pop_due(self) -> list[Reminder]:
        """Return timed reminders that just came due (announce once each)."""
        result: list[Reminder] = []
        now = time.time()
        with self._lock:
            for r in self._items:
                if r.is_due(now) and not r.announced:
                    r.announced = True
                    result.append(r)
            if result:
                self._persist_locked()
        return result


_store = _ReminderStore()


# --- Public API -------------------------------------------------------------

def add_reminder(text: str, due_at: float | None = None) -> Reminder:
    return _store.add(text, due_at)

def complete_reminder(reminder_id: str) -> str:
    return _store.complete(reminder_id)

def delete_reminder(reminder_id: str) -> str:
    reminder_id = (reminder_id or "").strip()
    return f"Deleted reminder {reminder_id}." if _store.delete(reminder_id) else f"No reminder with id {reminder_id}."

def rename_reminder(reminder_id: str, text: str) -> Reminder | None:
    """New wording for a reminder (the companion's rename in place)."""
    return _store.rename(reminder_id, text)

def remove_reminder(reminder_id: str) -> bool:
    """Delete outright (the companion's to-do check: done means gone)."""
    return _store.delete(reminder_id)


def active_reminders(include_done: bool = False) -> list[Reminder]:
    return _store.list_items(include_done)

def pop_due_reminders() -> list[Reminder]:
    return _store.pop_due()


def _format_due(due_at: float | None) -> str:
    if due_at is None:
        return "no due time"
    dt = datetime.fromtimestamp(due_at)
    hm = dt.strftime("%I:%M").lstrip("0")
    return f"{hm} {dt.strftime('%p')} on {dt.strftime('%a')}"


def list_reminders() -> str:
    """Spoken-friendly summary of open reminders and to-dos."""
    items = _store.list_items(include_done=False)
    if not items:
        return "You have no reminders, sir."
    timed = [r for r in items if r.due_at is not None]
    todos = [r for r in items if r.due_at is None]
    parts: list[str] = []
    for r in timed:
        parts.append(f"({r.id}) {r.text} at {_format_due(r.due_at)}")
    for r in todos:
        parts.append(f"({r.id}) {r.text}")
    return "Reminders: " + "; ".join(parts) + "."
