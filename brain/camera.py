"""The room camera: the iPad is the lens, Nick's phone is the only viewer.

A deliberately visible feature, not a hidden one. The iPad only captures
while Nick's phone is actively watching (the phone sends a heartbeat every
couple of seconds; the capture stops within ``VIEWER_TTL`` of the last one),
and while it captures it shows a green JARVIS orb so anyone in the room can
see it's a camera. iOS's own green dot is on too.

"Jarvis, kill the camera" (or the ``kill_camera`` tool, or the phone) sets a
hard lockout: for ``KILL_SECONDS`` (300) nothing can turn the lens on, no
matter who is asking or where from, so Nick can come ask why it was on.

State is in memory only (a frame is never written to disk): the latest JPEG,
when a viewer was last seen, and when the lockout ends.
"""
from __future__ import annotations

import threading
import time

VIEWER_TTL = 6.0       # no heartbeat for this long -> nobody is watching
FRAME_TTL = 10.0       # a frame older than this is stale (the lens stopped)
KILL_SECONDS = 300.0   # a kill locks the lens off for five minutes

_lock = threading.Lock()
_viewer_last_seen = 0.0
_kill_until = 0.0
_frame: bytes | None = None
_frame_at = 0.0


def view_heartbeat(now: float | None = None) -> None:
    """The phone is watching right now."""
    global _viewer_last_seen
    with _lock:
        _viewer_last_seen = now or time.time()


def kill(now: float | None = None) -> float:
    """Lock the lens off for KILL_SECONDS. Returns when it lifts. Also drops
    any viewer and the last frame at once."""
    global _kill_until, _viewer_last_seen, _frame
    now = now or time.time()
    with _lock:
        _kill_until = now + KILL_SECONDS
        _viewer_last_seen = 0.0
        _frame = None
        return _kill_until


def killed_for(now: float | None = None) -> float:
    """Seconds of lockout left (0 when the lens is free)."""
    now = now or time.time()
    with _lock:
        return max(0.0, _kill_until - now)


def wanted(now: float | None = None) -> bool:
    """Should the iPad be capturing? Only with a live viewer and no lockout."""
    now = now or time.time()
    with _lock:
        if now < _kill_until:
            return False
        return now - _viewer_last_seen < VIEWER_TTL


def put_frame(data: bytes, now: float | None = None) -> bool:
    """The iPad's latest frame. Refused (False) during a lockout, so a kill
    stops frames even if the lens is mid-shutdown."""
    global _frame, _frame_at
    now = now or time.time()
    with _lock:
        if now < _kill_until:
            return False
        _frame, _frame_at = data, now
        return True


def get_frame(now: float | None = None) -> bytes | None:
    """The latest frame, or None if there isn't a fresh one."""
    now = now or time.time()
    with _lock:
        if _frame is None or now - _frame_at > FRAME_TTL:
            return None
        return _frame


def status(now: float | None = None) -> dict[str, object]:
    now = now or time.time()
    with _lock:
        locked = max(0.0, _kill_until - now)
        viewing = now - _viewer_last_seen < VIEWER_TTL and locked == 0
        fresh = _frame is not None and now - _frame_at <= FRAME_TTL
        return {
            "wanted": viewing,
            "viewing": viewing,
            "killed": locked > 0,
            "kill_seconds_left": round(locked),
            "has_frame": fresh,
        }


def reset_for_tests() -> None:
    global _viewer_last_seen, _kill_until, _frame, _frame_at
    with _lock:
        _viewer_last_seen = _kill_until = _frame_at = 0.0
        _frame = None
