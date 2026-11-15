"""The room camera: the iPads are the lenses, Nick's phone is the only viewer.

A deliberately visible feature, not a hidden one. An iPad only captures
while Nick's phone is actively watching (the phone sends a heartbeat every
couple of seconds; the capture stops within ``VIEWER_TTL`` of the last one),
and while it captures it shows a green JARVIS orb so anyone in the room can
see it's a camera. iOS's own green dot is on too.

Two angles: each iPad uploads under its own lens name, ``door`` (the iPad
Air by the door) or ``bed`` (the nightstand iPad mini). One viewer wakes
every lens; the phone pulls each lens's newest frame and shows them side by
side. An upload with no lens name is the door's (iPads before 6.34).

"Jarvis, kill the camera" (or the ``kill_camera`` tool, or the phone) sets a
hard lockout: for ``KILL_SECONDS`` (300) nothing can turn any lens on, no
matter who is asking or where from, so Nick can come ask why it was on.

State is in memory only (a frame is never written to disk): each lens's
latest JPEG, when a viewer was last seen, and when the lockout ends.
"""
from __future__ import annotations

import re
import threading
import time

VIEWER_TTL = 6.0       # no heartbeat for this long -> nobody is watching
FRAME_TTL = 10.0       # a frame older than this is stale (the lens stopped)
KILL_SECONDS = 300.0   # a kill locks the lens off for five minutes
DEFAULT_LENS = "door"  # the iPad Air by the door; uploads without a name
LENS_ORDER = ("door", "bed")  # how the phone lays the angles out
_LENS_NAME = re.compile(r"^[a-z0-9_-]{1,20}$")

_lock = threading.Lock()
_viewer_last_seen = 0.0
_kill_until = 0.0
_frames: dict[str, tuple[bytes, float]] = {}


def lens_name(raw: str | None) -> str | None:
    """A lens name as sent by an iPad or the phone: lower-cased, ``door``
    when empty, None when it isn't a plausible name."""
    name = (raw or "").strip().lower() or DEFAULT_LENS
    return name if _LENS_NAME.match(name) else None


def view_heartbeat(now: float | None = None) -> None:
    """The phone is watching right now."""
    global _viewer_last_seen
    with _lock:
        _viewer_last_seen = now or time.time()


def leave() -> None:
    """The phone closed the live view: every lens should stop now, not after
    ``VIEWER_TTL``. The iPads' next frame uploads are refused and they stop."""
    global _viewer_last_seen
    with _lock:
        _viewer_last_seen = 0.0
        _frames.clear()


def kill(now: float | None = None) -> float:
    """Lock every lens off for KILL_SECONDS. Returns when it lifts. Also
    drops any viewer and every frame at once."""
    global _kill_until, _viewer_last_seen
    now = now or time.time()
    with _lock:
        _kill_until = now + KILL_SECONDS
        _viewer_last_seen = 0.0
        _frames.clear()
        return _kill_until


def killed_for(now: float | None = None) -> float:
    """Seconds of lockout left (0 when the lens is free)."""
    now = now or time.time()
    with _lock:
        return max(0.0, _kill_until - now)


def wanted(now: float | None = None) -> bool:
    """Should the iPads be capturing? Only with a live viewer and no lockout."""
    now = now or time.time()
    with _lock:
        if now < _kill_until:
            return False
        return now - _viewer_last_seen < VIEWER_TTL


def put_frame(data: bytes, now: float | None = None, lens: str = DEFAULT_LENS) -> bool:
    """One iPad's latest frame. Refused (False) during a lockout or once no
    viewer is left, so a kill or the phone closing stops every lens at once."""
    now = now or time.time()
    with _lock:
        if now < _kill_until or now - _viewer_last_seen >= VIEWER_TTL:
            return False
        _frames[lens] = (data, now)
        return True


def _fresh(now: float) -> dict[str, bytes]:
    return {name: data for name, (data, at) in _frames.items() if now - at <= FRAME_TTL}


def get_frame(now: float | None = None, lens: str | None = None) -> bytes | None:
    """The latest frame from ``lens``, or None if it hasn't a fresh one. With
    no lens (a phone before 6.34): the door's, else any fresh angle."""
    now = now or time.time()
    with _lock:
        fresh = _fresh(now)
        if lens is not None:
            return fresh.get(lens)
        if DEFAULT_LENS in fresh:
            return fresh[DEFAULT_LENS]
        return next(iter(fresh.values()), None)


def lenses(now: float | None = None) -> list[str]:
    """The angles sending fresh frames right now, door first then bed."""
    now = now or time.time()
    with _lock:
        names = list(_fresh(now))
    rank = {name: i for i, name in enumerate(LENS_ORDER)}
    return sorted(names, key=lambda n: (rank.get(n, len(rank)), n))


def status(now: float | None = None) -> dict[str, object]:
    now = now or time.time()
    with _lock:
        locked = max(0.0, _kill_until - now)
        viewing = now - _viewer_last_seen < VIEWER_TTL and locked == 0
        fresh = bool(_fresh(now))
    return {
        "wanted": viewing,
        "viewing": viewing,
        "killed": locked > 0,
        "kill_seconds_left": round(locked),
        "has_frame": fresh,
        "lenses": lenses(now),
    }


def reset_for_tests() -> None:
    global _viewer_last_seen, _kill_until
    with _lock:
        _viewer_last_seen = _kill_until = 0.0
        _frames.clear()
