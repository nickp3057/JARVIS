"""Failed-token lockout for Core's HTTP API (REMOTE_AND_MONEY_PLAN.md, Part 1).

Once the companion reaches Core through ``jarvis.nickolaspearson.com``, the
device certificate at Lightsail's Caddy keeps strangers out, and the token is
the second lock. This is the third: an address that keeps presenting a wrong
token is blocked for a while, and Nick hears about it once.

- ``JARVIS_AUTH_LOCKOUT_FAILURES`` (20) wrong tokens inside
  ``JARVIS_AUTH_LOCKOUT_WINDOW_S`` (600) block that address for
  ``JARVIS_AUTH_LOCKOUT_S`` (900). ``0`` failures turns the lockout off.
- A request with a valid token is never refused, blocked address or not: the
  lockout only stops more guessing.
- The address is the real client's: uvicorn rewrites it from
  ``X-Forwarded-For`` only for ``JARVIS_TRUSTED_PROXIES`` (Lightsail), so
  nobody else can spoof one.

State is in memory: a Core restart forgets the counts, which is fine for a
guessing alarm.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

from jarvis_logger import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
_blocked_until: dict[str, float] = {}
_alerted: dict[str, float] = {}
# Set by app/server.py: raise the one "someone is guessing" alert.
on_lockout: Callable[[str, int], None] | None = None


def _int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _limits() -> tuple[int, float, float]:
    return (_int("JARVIS_AUTH_LOCKOUT_FAILURES", 20),
            float(_int("JARVIS_AUTH_LOCKOUT_WINDOW_S", 600)),
            float(_int("JARVIS_AUTH_LOCKOUT_S", 900)))


def blocked(ip: str | None, now: float | None = None) -> bool:
    """Is this address in its lockout right now?"""
    if not ip:
        return False
    now = time.time() if now is None else now
    with _lock:
        until = _blocked_until.get(ip, 0.0)
        if until and until <= now:
            _blocked_until.pop(ip, None)
            return False
        return until > now


def record_failure(ip: str | None, now: float | None = None) -> bool:
    """Count one wrong token from ``ip``; True when this one starts a lockout."""
    failures, window, lockout = _limits()
    if not ip or failures <= 0:
        return False
    now = time.time() if now is None else now
    with _lock:
        recent = [t for t in _failures.get(ip, []) if now - t < window]
        recent.append(now)
        _failures[ip] = recent
        if len(recent) < failures or _blocked_until.get(ip, 0.0) > now:
            return False
        _blocked_until[ip] = now + lockout
        _failures.pop(ip, None)
        # One alert per address per day, however often it trips.
        fresh = now - _alerted.get(ip, -1e18) > 86_400
        if fresh:
            _alerted[ip] = now
        count = len(recent)
    log.warning("Locked out %s for %.0f s after %d wrong tokens", ip, lockout, count)
    if fresh and on_lockout is not None:
        try:
            on_lockout(ip, count)
        except Exception:  # noqa: BLE001 - the alert must never break auth
            log.exception("Lockout alert failed")
    return True


def reset_for_tests() -> None:
    with _lock:
        _failures.clear()
        _blocked_until.clear()
        _alerted.clear()
