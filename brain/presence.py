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

**The phone's geofence (2026-11-24, companion 6.128).** Nick: "I don't even
want to think about it." The iPhone app watches a circle around home
(``home_zone()``, from HA's ``zone.home``, at least 150 m) with iOS region
monitoring, which wakes the app on leaving or arriving even when it isn't
running, and it posts ``POST /api/presence/phone`` → ``report()``. That is
the main signal; the address sightings below are the backup.

**The phone's own requests (2026-11-24).** Home Assistant has no external
URL, so away from home Wi-Fi the HA app can't post its location and
``person.nick`` stays "home" from the last report (it never left "home"
from 11-19 to 11-24, school days included), and the room kept talking.
The companion now reaches Core through the Lightsail proxy when away, and
Core sees its real address, so ``note_phone`` (called by the server for
every successful iPhone request on the admin token) records a sighting:
a public address means away, a home LAN address means home, a tailnet one
says nothing. ``location()`` takes whichever is newer, HA's last report
(``last_updated``) or the phone's last sighting. Sightings survive a
restart (``memory/presence.json``). Caveats: the phone has to have talked
to JARVIS since leaving (opening the app, a widget or a Shortcut does it);
the app on cellular *at home* would read as away until HA reports again;
and an address in ``PRESENCE_HOME_IPS`` (the house's public IP, if the app
ever uses the domain at home) counts as home.
"""
from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, NamedTuple

from jarvis_logger import get_logger

log = get_logger(__name__)

_cache: dict[str, Any] = {"at": 0.0, "value": None}
_lock = threading.Lock()
_PHONE_FILE = Path(__file__).resolve().parents[1] / "memory" / "presence.json"
_phone: dict[str, Any] = {"loaded": False, "where": None, "at": 0.0, "saved_at": 0.0}
_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_ha_updated: dict[str, float] = {"at": 0.0}  # when person.nick last changed in HA (epoch s)


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
    _ha_updated["at"] = _parse_time(entity.get("last_updated"))
    state = str(entity.get("state", "")).strip().lower()
    if state in {"", "unknown", "unavailable"}:
        return None
    return "home" if state == "home" else "away"  # "not_home" or another zone's name



def classify(ip: str | None) -> str | None:
    """Where a request from this address puts the phone: 'home' (LAN or a
    PRESENCE_HOME_IPS address), 'away' (any other public address, i.e. through
    the Lightsail proxy), None (tailnet, loopback, unparseable)."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    home = {h.strip() for h in os.getenv("PRESENCE_HOME_IPS", "").split(",") if h.strip()}
    if str(addr) in home:
        return "home"
    if addr.is_loopback or addr.is_link_local or (addr.version == 4 and addr in _TAILNET):
        return None
    if addr.is_private:
        return "home"
    return "away" if addr.is_global else None


def _load_phone() -> None:
    if _phone["loaded"]:
        return
    _phone["loaded"] = True
    try:
        data = json.loads(_PHONE_FILE.read_text())
        if data.get("where") in ("home", "away"):
            _phone.update(where=data["where"], at=float(data.get("at") or 0), saved_at=float(data.get("at") or 0))
    except (OSError, ValueError, TypeError):
        pass


def note_phone(ip: str | None, *, now: float | None = None) -> None:
    """The iPhone just reached Core from ``ip``. Never raises."""
    report(classify(ip), now=now, source="address")


def report(where: str | None, *, now: float | None = None, source: str = "geofence") -> None:
    """The phone's word on where it is: its geofence (iOS wakes the app on
    crossing the home zone, app open or not) or the address it called from."""
    if where not in ("home", "away"):
        return
    now = time.time() if now is None else now
    with _lock:
        _load_phone()
        changed = where != _phone["where"]
        _phone.update(where=where, at=now)
        if changed:
            _cache.update(at=0.0)  # the room should hear about it on the next ask
        # The file only needs to be roughly current: write on a change, else every 10 min.
        if changed or now - _phone["saved_at"] > 600:
            _phone["saved_at"] = now
            try:
                _PHONE_FILE.parent.mkdir(parents=True, exist_ok=True)
                _PHONE_FILE.write_text(json.dumps({"where": where, "at": now}))
            except OSError:
                log.debug("Couldn't save the phone sighting", exc_info=True)
    if changed:
        log.info("Presence: the iPhone says %s (%s)", where, source)


def home_zone() -> dict[str, float] | None:
    """HA's zone.home as {"latitude", "longitude", "radius"}: the circle the
    phone's geofence watches. None when HA can't say."""
    from integrations import home_assistant
    ok, payload = home_assistant.get_states()
    if not ok or not isinstance(payload, list):
        return None
    zone = next((e for e in payload if isinstance(e, dict) and e.get("entity_id") == "zone.home"), None)
    attrs = (zone or {}).get("attributes") or {}
    try:
        return {"latitude": float(attrs["latitude"]), "longitude": float(attrs["longitude"]),
                "radius": float(attrs.get("radius") or 100)}
    except (KeyError, TypeError, ValueError):
        return None


def phone_sighting() -> tuple[str | None, float]:
    with _lock:
        _load_phone()
        return _phone["where"], _phone["at"]


def _parse_time(text: Any) -> float:
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _combined() -> str | None:
    """The newer of HA's last report and the phone's last sighting."""
    try:
        ha = _read()
    except Exception:  # noqa: BLE001 - HA down still leaves the phone's word
        log.debug("Presence read failed", exc_info=True)
        ha = None
    where, at = phone_sighting()
    if where is not None and (ha is None or at > _ha_updated["at"]):
        return where
    return ha


def location(*, fresh: bool = False) -> str | None:
    """'home', 'away', or None when it can't be told. Never raises."""
    if os.getenv("PRESENCE_ENABLED", "true").strip().lower() in {"0", "false", "off", "no"}:
        return None
    now = time.monotonic()
    with _lock:
        if not fresh and _cache["at"] and now - _cache["at"] < _cache_s():
            return _cache["value"]
    try:
        value = _combined()
    except Exception:  # noqa: BLE001 - presence never breaks a caller
        log.debug("Presence read failed", exc_info=True)
        value = None
    with _lock:
        _cache.update(at=now, value=value)
    return value


def reset_for_tests() -> None:
    with _lock:
        _cache.update(at=0.0, value=None)
        _phone.update(loaded=True, where=None, at=0.0, saved_at=0.0)
        _ha_updated["at"] = 0.0


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
