"""Presence: is Nick home? From his iPhone's GPS, through Home Assistant.

Asked 2026-11-17: "the room mutes when Nick leaves whatever the scene, and
wakes up again when he's home." Home Assistant's companion app already
tracks the iPhone (``person.nick``, GPS, location "Always"), so:

* ``location()`` — "home", "away", or None when HA can't say (unavailable,
  unknown, unreachable). ``AWAY_PERSON`` picks the entity (default
  ``person.nick``). Cached ``PRESENCE_CACHE_S`` (30 s) so the room voice can
  ask on every alert.
* ``app.status_hub.room_away`` uses it first: away means quiet, home means
  talk, and only when GPS can't be read does the old guess (Peace scene with
  every light off) apply.
* ``PresenceWatch`` turns location samples into departures and arrivals,
  each confirmed only after ``PRESENCE_CONFIRM_S`` (180 s) so GPS jitter at
  the edge of the home zone doesn't flap. The runtime's presence monitor
  acts on them: an inbox line both ways, and on leaving, a push if room
  lights were left on, with a Turn them off button (category ``LEFT_ON``).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, NamedTuple

from jarvis_logger import get_logger

log = get_logger(__name__)

_cache: dict[str, Any] = {"at": 0.0, "value": None}
_lock = threading.Lock()


def person_entity() -> str:
    return (os.getenv("AWAY_PERSON", "") or "person.nick").strip().lower()


def _cache_s() -> float:
    try:
        return max(0.0, float(os.getenv("PRESENCE_CACHE_S", "") or 30))
    except ValueError:
        return 30.0


def _read() -> str | None:
    from integrations import home_assistant
    ok, payload = home_assistant.get_states()
    if not ok or not isinstance(payload, list):
        return None
    wanted = person_entity()
    entity = next((e for e in payload if isinstance(e, dict) and str(e.get("entity_id", "")).lower() == wanted), None)
    if entity is None:
        return None
    state = str(entity.get("state", "")).strip().lower()
    if state in {"", "unknown", "unavailable"}:
        return None
    return "home" if state == "home" else "away"  # "not_home" or another zone's name


def location(*, fresh: bool = False) -> str | None:
    """'home', 'away', or None when it can't be told. Never raises."""
    if os.getenv("PRESENCE_ENABLED", "true").strip().lower() in {"0", "false", "off", "no"}:
        return None
    now = time.monotonic()
    with _lock:
        if not fresh and _cache["at"] and now - _cache["at"] < _cache_s():
            return _cache["value"]
    try:
        value = _read()
    except Exception:  # noqa: BLE001 - presence never breaks a caller
        log.debug("Presence read failed", exc_info=True)
        value = None
    with _lock:
        _cache.update(at=now, value=value)
    return value


def reset_for_tests() -> None:
    with _lock:
        _cache.update(at=0.0, value=None)


class Change(NamedTuple):
    kind: str  # "left" | "arrived"
    since: float  # when the new state was first seen


class PresenceWatch:
    """Debounced home/away transitions from periodic samples."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.confirmed: str | None = None
        self._candidate: tuple[str, float] | None = None

    def step(self, sample: str | None) -> Change | None:
        if sample is None:
            return None  # unknown says nothing either way
        now = self._clock()
        if self.confirmed is None:
            self.confirmed = sample  # first reading after start: no event
            return None
        if sample == self.confirmed:
            self._candidate = None
            return None
        if self._candidate is None or self._candidate[0] != sample:
            self._candidate = (sample, now)
        try:
            confirm = float(os.getenv("PRESENCE_CONFIRM_S", "") or 180)
        except ValueError:
            confirm = 180.0
        if now - self._candidate[1] < confirm:
            return None
        self.confirmed, since = sample, self._candidate[1]
        self._candidate = None
        return Change("left" if sample == "away" else "arrived", since)


def lights_left_on() -> list[dict[str, str]]:
    """Room lights (the app's dashboard lights) that are on: [{entity_id, name}]."""
    from app import status_hub
    listing = status_hub.home_entities()
    if listing.get("error"):
        return []
    return [{"entity_id": e["entity_id"], "name": e.get("friendly_name") or e["entity_id"]}
            for e in listing.get("entities", []) if e.get("domain") == "light" and e.get("state") == "on"]


def turn_off_left_on() -> tuple[int, list[str]]:
    """The push's Turn them off button: lights that are still on go off.
    (How many went off, names that refused.)"""
    from integrations import home_assistant
    done, failed = 0, []
    for light in lights_left_on():
        ok, _ = home_assistant.call_service("light", "turn_off", light["entity_id"])
        if ok:
            done += 1
        else:
            failed.append(light["name"])
    return done, failed
