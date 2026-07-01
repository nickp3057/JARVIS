"""In-process timers and reminders.

A thread-safe timer store that supports:
- set / cancel / list
- pause and resume (freezes the countdown)
- alarming state: when a timer expires it stays in the list (alarming=True)
  until explicitly dismissed, so the UI can show it and play a sound.
- persistence: timers are written to memory/timers.json so they survive a
  restart. A timer that lapses while JARVIS is closed is alarming on next launch.

The voice runner calls pop_newly_expired() each frame to announce timers by
voice. The UI reads list_active() and calls dismiss/pause/resume directly.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from jarvis_logger import get_logger

log = get_logger(__name__)

# Timers are persisted here so they survive a restart.
_TIMERS_FILE = Path(__file__).resolve().parent.parent / "memory" / "timers.json"


@dataclass
class Timer:
    id: str
    expires_at: float          # Unix timestamp (meaningless while paused)
    message: str
    paused: bool = False
    paused_remaining: float | None = None   # seconds remaining when paused
    alarming: bool = False                  # expired, awaiting dismiss
    announced: bool = False                 # voice announcement already made

    def remaining_seconds(self) -> float:
        if self.paused:
            return max(0.0, self.paused_remaining or 0.0)
        return max(0.0, self.expires_at - time.time())


class _TimerStore:
    def __init__(self) -> None:
        self._timers: list[Timer] = []
        self._lock = threading.Lock()
        self._load_from_disk()

    # --- Persistence --------------------------------------------------------

    def _persist_locked(self) -> None:
        """Write current timers to disk. Caller must hold the lock."""
        try:
            _TIMERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = _TIMERS_FILE.with_suffix(".json.tmp")
            payload = {"timers": [asdict(t) for t in self._timers]}
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            tmp_path.replace(_TIMERS_FILE)
        except OSError as exc:
            log.error("Could not persist timers: %s", exc)

    def _load_from_disk(self) -> None:
        """Rehydrate timers saved by a previous run, if any."""
        if not _TIMERS_FILE.exists():
            return
        try:
            with _TIMERS_FILE.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read timers file (%s); starting with none.", exc)
            return

        restored: list[Timer] = []
        for raw in data.get("timers", []):
            try:
                restored.append(
                    Timer(
                        id=str(raw["id"]),
                        expires_at=float(raw["expires_at"]),
                        message=str(raw.get("message", "Timer up")),
                        paused=bool(raw.get("paused", False)),
                        paused_remaining=(
                            float(raw["paused_remaining"])
                            if raw.get("paused_remaining") is not None
                            else None
                        ),
                        alarming=bool(raw.get("alarming", False)),
                        announced=bool(raw.get("announced", False)),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping malformed persisted timer %r: %s", raw, exc)

        with self._lock:
            self._timers = restored
            # Flag any timer that lapsed while JARVIS was closed so it alarms.
            self._mark_expired_locked()
            self._timers.sort(key=lambda t: t.expires_at)
        if restored:
            log.info("Restored %d timer(s) from disk.", len(restored))

    # --- Creating -----------------------------------------------------------

    def set_timer(self, duration_seconds: float, message: str) -> str:
        if duration_seconds <= 0:
            return "Refused: duration must be positive."
        message = (message or "").strip() or "Timer up"

        timer = Timer(
            id=uuid.uuid4().hex[:6],
            expires_at=time.time() + duration_seconds,
            message=message,
        )
        with self._lock:
            self._timers.append(timer)
            self._timers.sort(key=lambda t: t.expires_at)
            self._persist_locked()

        log.info("Timer set: id=%s in %.0fs (%s)", timer.id, duration_seconds, message)
        return _format_set_confirmation(timer, duration_seconds)

    # --- Cancelling / dismissing --------------------------------------------

    def cancel(self, timer_id: str) -> str:
        timer_id = (timer_id or "").strip()
        if not timer_id:
            return "Refused: missing timer id."
        with self._lock:
            before = len(self._timers)
            self._timers = [t for t in self._timers if t.id != timer_id]
            removed = before - len(self._timers)
            if removed:
                self._persist_locked()
        return f"Cancelled timer {timer_id}." if removed else f"No timer with id {timer_id}."

    def dismiss(self, timer_id: str) -> str:
        """Remove an alarming (expired) timer after the user acknowledges it."""
        timer_id = (timer_id or "").strip()
        with self._lock:
            before = len(self._timers)
            self._timers = [t for t in self._timers if t.id != timer_id]
            removed = before - len(self._timers)
            if removed:
                self._persist_locked()
        return f"Timer {timer_id} dismissed." if removed else f"No timer with id {timer_id}."

    # --- Pause / resume -----------------------------------------------------

    def pause(self, timer_id: str) -> str:
        timer_id = (timer_id or "").strip()
        with self._lock:
            for t in self._timers:
                if t.id == timer_id:
                    if t.paused:
                        return f"Timer {timer_id} is already paused."
                    if t.alarming:
                        return f"Timer {timer_id} has already expired - dismiss it instead."
                    t.paused_remaining = max(0.0, t.expires_at - time.time())
                    t.paused = True
                    self._persist_locked()
                    log.info("Timer %s paused (%.0fs remaining)", timer_id, t.paused_remaining)
                    return f"Paused timer {timer_id} with {_format_duration(t.paused_remaining)} remaining."
        return f"No active timer with id {timer_id}."

    def resume(self, timer_id: str) -> str:
        timer_id = (timer_id or "").strip()
        with self._lock:
            for t in self._timers:
                if t.id == timer_id:
                    if not t.paused:
                        return f"Timer {timer_id} is not paused."
                    remaining = t.paused_remaining or 0.0
                    t.expires_at = time.time() + remaining
                    t.paused = False
                    t.paused_remaining = None
                    self._persist_locked()
                    log.info("Timer %s resumed (%.0fs remaining)", timer_id, remaining)
                    return f"Resumed timer {timer_id}. {_format_duration(remaining)} remaining."
        return f"No active timer with id {timer_id}."

    # --- Reading ------------------------------------------------------------

    def list_active(self) -> list[Timer]:
        with self._lock:
            self._mark_expired_locked()
            return list(self._timers)

    def _mark_expired_locked(self) -> None:
        now = time.time()
        changed = False
        for t in self._timers:
            if not t.paused and not t.alarming and t.expires_at <= now:
                t.alarming = True
                changed = True
        if changed:
            self._persist_locked()

    def mark_expired(self) -> None:
        with self._lock:
            self._mark_expired_locked()

    def pop_newly_expired(self) -> list[Timer]:
        """Return newly expired timers for announcement.

        Timers remain in the list (with alarming=True) until dismiss() is called.
        Only returns each timer once (announced flag prevents re-announcement).
        UI code may mark timers alarming without consuming this announcement.
        """
        result: list[Timer] = []
        with self._lock:
            self._mark_expired_locked()
            for t in self._timers:
                if t.alarming and not t.announced:
                    t.announced = True
                    result.append(t)
            if result:
                self._persist_locked()
        return result

    # Legacy alias used by the voice runner.
    def pop_expired(self) -> list[Timer]:
        return self.pop_newly_expired()


_store = _TimerStore()


# --- Public API -------------------------------------------------------------

def set_timer(duration_seconds: float, message: str) -> str:
    return _store.set_timer(duration_seconds, message)

def cancel_timer(timer_id: str) -> str:
    return _store.cancel(timer_id)

def dismiss_timer(timer_id: str) -> str:
    return _store.dismiss(timer_id)

def pause_timer(timer_id: str) -> str:
    return _store.pause(timer_id)

def resume_timer(timer_id: str) -> str:
    return _store.resume(timer_id)

def active_timers() -> list[Timer]:
    return _store.list_active()

def mark_expired_timers() -> None:
    _store.mark_expired()

def list_timers() -> str:
    """Spoken-friendly summary of active timers."""
    timers = _store.list_active()
    if not timers:
        return "You have no active timers."
    parts: list[str] = []
    for t in timers:
        if t.alarming:
            parts.append(f"({t.id}) {t.message} - EXPIRED, awaiting dismiss")
        elif t.paused:
            parts.append(
                f"({t.id}) {t.message} - PAUSED, {_format_duration(t.paused_remaining or 0)} remaining"
            )
        else:
            parts.append(f"({t.id}) {t.message} in {_format_duration(t.remaining_seconds())}")
    return "Active timers: " + "; ".join(parts) + "."

def pop_expired() -> list[Timer]:
    return _store.pop_newly_expired()


# --- Helpers ----------------------------------------------------------------

def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rem}s" if rem else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _format_set_confirmation(timer: Timer, duration_seconds: float) -> str:
    return (
        f"Timer set for {_format_duration(duration_seconds)}: {timer.message}. "
        f"(id {timer.id})"
    )
