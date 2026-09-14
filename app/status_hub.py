"""Status aggregation and guarded quick actions for the companion dashboard.

This module backs the ``/api/status/*``, ``/api/links``, ``/api/home/*``, and
``/api/torrents/control`` routes in ``app.server``. It is server-side glue
over the existing integrations — no credentials or service logic live in the
iOS client. The only mutations are the two quick-action functions
(``home_control``, ``torrent_control``), which delegate to the integrations'
existing guarded control paths rather than reimplementing them.

Design points:
- Service base URLs and credentials are read from the SAME config helpers the
  integrations use (deliberately importing their private ``_config`` /
  ``_service_url`` readers) so there is exactly one source of truth in
  ``.env``. The only link var without an integration to borrow from is
  ``PLEX_URL``.
- Every public function degrades gracefully: an unreachable service produces
  an ``error`` string and empty data, never an exception, so the HTTP layer
  can always return 200.
- A small in-process TTL cache lets the app poll without hammering the NAS.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from threading import Lock
from typing import Any, Callable

import requests

from brain.reminders import active_reminders
from brain.timers import active_timers
from integrations.google_calendar import is_configured as calendar_configured
from integrations.google_calendar import academic_events_from, upcoming_events
from integrations.home_assistant import _config as _ha_config
from integrations.home_assistant import call_discovered_service, get_states
from integrations.qbittorrent import _config as _qbt_config
from integrations.qbittorrent import list_transfers, manage_torrent
from integrations.servarr_common import (
    _queue_title,
    _service_url,
    get_queue,
    is_configured as servarr_configured,
    request as servarr_request,
)
from jarvis_logger import get_logger

log = get_logger(__name__)

_PING_TIMEOUT_S = 5
_TORRENTS_TTL_S = 5.0
_SERVICES_TTL_S = 5.0
_ENTITIES_TTL_S = 5.0
_MEDIA_TTL_S = 15.0
_NOW_TTL_S = 5.0
_STORAGE_TTL_S = 30.0

# Strip an API path suffix if one leaked into a base URL (e.g. ".../api/v2").
_API_SUFFIX_RE = re.compile(r"/api(/v\d+)?/?$", re.IGNORECASE)


# --- TTL cache -------------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = Lock()


def _cached(key: str, ttl: float, compute: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = compute()
    with _cache_lock:
        _cache[key] = (now + ttl, value)
    return value


def clear_cache() -> None:
    """Drop all cached entries (used by tests)."""
    with _cache_lock:
        _cache.clear()


def invalidate(key: str) -> None:
    """Drop one cached entry so the next read reflects a just-made change."""
    with _cache_lock:
        _cache.pop(key, None)


# --- Now snapshot ----------------------------------------------------------

def now_status() -> dict[str, Any]:
    """Coherent read-only snapshot for the companion's at-a-glance surface."""
    return _cached("now", _NOW_TTL_S, _now_uncached)


def _now_uncached() -> dict[str, Any]:
    timers = []
    for timer in active_timers():
        timers.append({
            "id": timer.id,
            "label": timer.message,
            "state": "alarming" if timer.alarming else ("paused" if timer.paused else "running"),
            "remaining_seconds": int(timer.remaining_seconds()),
            "expires_at": timer.expires_at,
        })

    reminders = []
    now = time.time()
    for reminder in active_reminders():
        reminders.append({
            "id": reminder.id,
            "text": reminder.text,
            "due_at": reminder.due_at,
            "is_due": reminder.due_at is not None and reminder.due_at <= now,
        })

    next_event = None
    academic_events = []
    calendar_error = ""
    if not calendar_configured():
        calendar_error = "Calendar is not configured."
    else:
        events = _cached("calendar-events", 300.0, lambda: upcoming_events(
            max_results=2500, hours_ahead=60 * 24, allow_interactive=False
        ))
        if events is None:
            calendar_error = "Calendar could not be reached."
        elif events:
            event = events[0]
            start = event.get("start")
            next_event = {
                "id": str(event.get("id") or "next-event"),
                "title": str(event.get("summary") or "Untitled event"),
                "start_at": start.timestamp() if start is not None else None,
                "all_day": bool(event.get("all_day", False)),
                "location": str(event.get("location") or ""),
            }
            for academic in academic_events_from(events):
                start = academic.get("start")
                academic_events.append({
                    "id": str(academic.get("id") or academic.get("summary") or "academic-event"),
                    "title": str(academic.get("summary") or "Untitled assessment"),
                    "start_at": start.timestamp() if start is not None else None,
                    "all_day": bool(academic.get("all_day", False)),
                    "location": str(academic.get("location") or ""),
                    "days_left": int(academic.get("days_left", 0)),
                })

    return {
        "generated_at": now,
        "timers": timers,
        "reminders": reminders,
        "next_event": next_event,
        "calendar_error": calendar_error,
        "academic_events": academic_events,
    }


# --- Media storage ---------------------------------------------------------

def media_storage_status() -> dict[str, Any]:
    """Disk usage for the one configured media filesystem, never all drives."""
    return _cached("media-storage", _STORAGE_TTL_S, _media_storage_uncached)


def _media_storage_uncached() -> dict[str, Any]:
    path = (
        os.getenv("MEDIA_STORAGE_PATH", "").strip()
        or os.getenv("RADARR_ROOT_FOLDER", "").strip()
        or os.getenv("SONARR_ROOT_FOLDER", "").strip()
    )
    label = os.getenv("MEDIA_STORAGE_LABEL", "Media storage").strip() or "Media storage"
    empty = {
        "label": label,
        "total_bytes": 0,
        "used_bytes": 0,
        "free_bytes": 0,
        "used_fraction": 0.0,
    }
    if not path:
        return {"error": "Media storage path is not configured.", **empty}
    try:
        usage = shutil.disk_usage(os.path.expanduser(path))
    except OSError as exc:
        log.warning("Media storage check failed for %s: %s", path, exc)
        return {"error": "Media storage is not mounted or reachable.", **empty}
    return {
        "error": "",
        "label": label,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_fraction": round(usage.used / usage.total, 4) if usage.total else 0.0,
    }


# --- Torrents --------------------------------------------------------------

def torrents_status() -> dict[str, Any]:
    """qBittorrent transfers plus global rates; empty + error when down."""
    return _cached("torrents", _TORRENTS_TTL_S, _torrents_uncached)


def _torrents_uncached() -> dict[str, Any]:
    ok, payload = list_transfers()
    if not ok or not isinstance(payload, dict):
        return {"error": str(payload), "dl_speed": 0, "up_speed": 0, "torrents": []}
    return {"error": "", **payload}


# --- Media (Radarr / Sonarr) ----------------------------------------------

def media_status() -> dict[str, Any]:
    """Both Servarr queues plus each library's 5 most recent additions."""
    return _cached("media", _MEDIA_TTL_S, _media_uncached)


def _media_uncached() -> dict[str, Any]:
    return {
        "radarr": _servarr_section("radarr"),
        "sonarr": _servarr_section("sonarr"),
    }


def _servarr_section(service: str) -> dict[str, Any]:
    if not servarr_configured(service):
        return {"error": f"{service.title()} is not configured.", "queue": [], "recent": []}

    section: dict[str, Any] = {"error": "", "queue": [], "recent": []}
    ok, payload = get_queue(service)
    if not ok:
        section["error"] = str(payload)
    else:
        records = payload.get("records", []) if isinstance(payload, dict) else []
        for record in records:
            size = float(record.get("size", 0) or 0)
            left = float(record.get("sizeleft", 0) or 0)
            section["queue"].append(
                {
                    "id": str(record.get("id") or record.get("downloadId") or _queue_title(service, record)),
                    "title": _queue_title(service, record),
                    "progress": round(1 - left / size, 4) if size > 0 else 0.0,
                    "size": int(size),
                    "size_left": int(left),
                    "time_left": str(record.get("timeleft") or ""),
                    "status": str(
                        record.get("trackedDownloadStatus") or record.get("status") or "unknown"
                    ),
                    "error_message": str(record.get("errorMessage") or ""),
                }
            )

    library_path = "/api/v3/movie" if service == "radarr" else "/api/v3/series"
    ok, items = servarr_request(service, "GET", library_path)
    if not ok:
        section["error"] = section["error"] or str(items)
    elif isinstance(items, list):
        newest = sorted(items, key=lambda item: str(item.get("added") or ""), reverse=True)
        section["recent"] = [
            {
                "title": str(item.get("title", "Unknown")),
                "year": int(item.get("year", 0) or 0),
                "added": str(item.get("added") or ""),
            }
            for item in newest[:5]
        ]
    return section


# --- Service reachability --------------------------------------------------

def _service_candidates() -> list[tuple[str, str, str]]:
    """(name, base_url_or_empty, icon_hint) for every known service.

    URLs come from the same env vars the integrations read — the one source
    of truth in ``.env``. ``PLEX_URL`` is the sole var without an integration
    to borrow from.
    """
    ha_url, _ = _ha_config()
    qbt_url, _, _ = _qbt_config()
    return [
        ("Home Assistant", ha_url, "home"),
        ("Plex", os.getenv("PLEX_URL", "").strip().rstrip("/"), "play"),
        ("Sonarr", _service_url("sonarr"), "tv"),
        ("Radarr", _service_url("radarr"), "movie"),
        ("qBittorrent", qbt_url, "download"),
    ]


def _link_targets() -> list[dict[str, str]]:
    """Configured services only; entries with an unset var are omitted."""
    return [
        {"name": name, "url": _API_SUFFIX_RE.sub("", url), "icon_hint": icon}
        for name, url, icon in _service_candidates()
        if url
    ]


def services_status() -> dict[str, Any]:
    """Reachability + latency for every known service (down when unset)."""
    return _cached("services", _SERVICES_TTL_S, _services_uncached)


def _services_uncached() -> dict[str, Any]:
    services: list[dict[str, Any]] = []
    for name, url, _icon in _service_candidates():
        entry: dict[str, Any] = {"name": name, "up": False, "latency_ms": None, "error": ""}
        services.append(entry)
        if not url:
            entry["error"] = f"{name} is not configured."
            continue
        started = time.monotonic()
        try:
            # Any HTTP response (401/403 included) proves the service is up;
            # auth'd checks aren't needed for reachability.
            requests.get(url, timeout=_PING_TIMEOUT_S, allow_redirects=False)
        except requests.RequestException as exc:
            entry["error"] = str(exc)
            continue
        entry["up"] = True
        entry["latency_ms"] = int((time.monotonic() - started) * 1000)
    return {"services": services}


# --- Links -----------------------------------------------------------------

def links() -> dict[str, Any]:
    """Deep links to each configured service's web UI."""
    return {"links": _link_targets()}


# --- Curated Home Assistant entities ---------------------------------------

def home_entities() -> dict[str, Any]:
    """State of the entities allowlisted in ``HOME_DASHBOARD_ENTITIES``."""
    return _cached("entities", _ENTITIES_TTL_S, _home_entities_uncached)


def _dashboard_allowlist() -> list[str]:
    """The ``HOME_DASHBOARD_ENTITIES`` allowlist — the security boundary for
    the app's home surface (both display and control)."""
    return [
        entity_id.strip().lower()
        for entity_id in os.getenv("HOME_DASHBOARD_ENTITIES", "").split(",")
        if entity_id.strip()
    ]


def _home_entities_uncached() -> dict[str, Any]:
    allowlist = _dashboard_allowlist()
    if not allowlist:
        return {"error": "", "entities": []}

    ok, payload = get_states()
    if not ok or not isinstance(payload, list):
        return {"error": str(payload), "entities": []}

    by_id = {str(entity.get("entity_id", "")): entity for entity in payload}
    entities = []
    for entity_id in allowlist:
        entity = by_id.get(entity_id)
        if entity is None:
            continue
        attributes = entity.get("attributes", {}) or {}
        entities.append(
            {
                "entity_id": entity_id,
                "friendly_name": str(attributes.get("friendly_name", entity_id)),
                "domain": entity_id.split(".", 1)[0] if "." in entity_id else "",
                "state": str(entity.get("state", "unknown")),
            }
        )
    return {"error": "", "entities": entities}


# --- Quick actions (guarded, allowlist-bound) -------------------------------
#
# Both integrations' guarded control paths return human-readable strings, not
# status tuples. Their success replies are stable and distinctive, so we
# detect success by marker; anything else is surfaced verbatim in ``error``.
_HA_SUCCESS_MARKER = "command sent"       # call_service success reply
_QBT_SUCCESS_MARKER = "accepted for"      # manage_torrent success reply


def home_control(entity_id: str, action: str) -> dict[str, Any]:
    """Turn one allowlisted Home Assistant entity on or off.

    Delegates to ``call_discovered_service`` so all its guards (identifier
    validation, blocked admin domains, confirmation-gated domains) stay in
    force. Raises ``ValueError`` for invalid input — the HTTP layer maps that
    to a 400; service failures degrade to an ``error`` field instead.
    """
    entity_id = (entity_id or "").strip().lower()
    action = (action or "").strip().lower()
    if action not in {"on", "off"}:
        raise ValueError("action must be 'on' or 'off'")
    if entity_id not in _dashboard_allowlist():
        raise ValueError("entity_id is not in the HOME_DASHBOARD_ENTITIES allowlist")

    domain = entity_id.split(".", 1)[0]
    message = call_discovered_service(domain, f"turn_{action}", entity_id=entity_id)
    if _HA_SUCCESS_MARKER not in message.lower():
        return {"error": message, "entity_id": entity_id, "state": None}

    invalidate("entities")
    state = None
    ok, states = get_states()
    if ok and isinstance(states, list):
        for entity in states:
            if str(entity.get("entity_id", "")) == entity_id:
                state = str(entity.get("state", "unknown"))
                break
    return {"error": "", "entity_id": entity_id, "state": state}


def torrent_control(torrent_hash: str, action: str, confirmed: bool) -> dict[str, Any]:
    """Pause or resume one torrent via the existing confirm-gated path.

    ``manage_torrent`` keeps its exact-hash validation, live-list check, and
    explicit-confirmation gate; ``delete_files`` is never passed from here.
    Raises ``ValueError`` for an unsupported action (HTTP 400); every guard
    refusal or service failure comes back as an ``error`` field.
    """
    action = (action or "").strip().lower()
    if action not in {"pause", "resume"}:
        raise ValueError("action must be 'pause' or 'resume'")

    message = manage_torrent(action, torrent_hash, confirmed=bool(confirmed))
    if _QBT_SUCCESS_MARKER not in message.lower():
        return {"error": message, "message": ""}
    invalidate("torrents")
    return {"error": "", "message": message}
