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
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

from urllib.parse import urlsplit, urlunsplit

import requests

from brain import maintenance, study_brief
from brain.reminders import active_reminders
from brain.timers import active_timers
from integrations import google_calendar
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
    get_root_folders,
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
# The "recently added" lists come from the full /movie and /series library
# dumps (no server-side sort/limit exists), which can be megabytes on a big
# library. Additions are rare, so they refresh far less often than the queue.
_RECENT_TTL_S = 120.0
_WEATHER_TTL_S = 600.0

# Strip an API path suffix if one leaked into a base URL (e.g. ".../api/v2").
_API_SUFFIX_RE = re.compile(r"/api(/v\d+)?/?$", re.IGNORECASE)


# --- TTL cache -------------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = Lock()
# One compute lock per key (single-flight): the app fires several sections in
# parallel and FastAPI serves them on worker threads, so without this two
# simultaneous misses both hit the NAS for the same data.
_compute_locks: dict[str, Lock] = {}


def _cached(key: str, ttl: float, compute: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        compute_lock = _compute_locks.setdefault(key, Lock())
    with compute_lock:
        # Another thread may have filled it while we waited.
        with _cache_lock:
            hit = _cache.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        value = compute()
        with _cache_lock:
            _cache[key] = (time.monotonic() + ttl, value)
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
        events = _calendar_events()
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
                    "kind": str(academic.get("kind") or "assessment"),
                })

    try:
        maintenance_due = maintenance.due_within()
    except Exception as exc:  # noqa: BLE001 - the snapshot must still render
        log.warning("Maintenance feed unavailable: %s", exc)
        maintenance_due = []

    return {
        "generated_at": now,
        "timers": timers,
        "reminders": reminders,
        "next_event": next_event,
        "calendar_error": calendar_error,
        "academic_events": academic_events,
        "maintenance": maintenance_due,
    }



# --- Glance (ambient screens: the TV launcher) -----------------------------

def weather_status() -> dict[str, Any]:
    """Current weather for ambient clients; Open-Meteo is asked every 10 min."""
    return _cached("weather", _WEATHER_TTL_S, _weather_uncached)


def _weather_uncached() -> dict[str, Any]:
    from dataclasses import asdict
    from brain.weather import get_weather_snapshot
    return asdict(get_weather_snapshot())


_GLANCE_EVENT_DAYS = 14
_GLANCE_EVENT_LIMIT = 20


def calendar_category(name: str) -> str:
    """work / school / family / personal for a calendar's display name.
    ``CALENDAR_CATEGORIES`` ("Oil/Boil Outs=work;Howard Family=family")
    wins; otherwise the name decides, and anything else is personal."""
    for pair in os.getenv("CALENDAR_CATEGORIES", "").split(";"):
        key, _, value = pair.partition("=")
        if key.strip() and key.strip().lower() == name.strip().lower() and value.strip():
            return value.strip().lower()
    lowered = name.lower()
    for word in ("work", "school", "family"):
        if word in lowered:
            return word
    return "personal"


def _glance_events() -> list[dict[str, Any]]:
    """The next two weeks of calendar, tagged by category, minus the
    categories in ``GLANCE_HIDDEN_CATEGORIES`` (default ``work``)."""
    hidden = {c.strip().lower() for c in os.getenv("GLANCE_HIDDEN_CATEGORIES", "work").split(",") if c.strip()}
    events = _calendar_events() if calendar_configured() else None
    out: list[dict[str, Any]] = []
    horizon = time.time() + _GLANCE_EVENT_DAYS * 86400
    for event in events or []:
        start = event.get("start")
        if start is None or start.timestamp() > horizon:
            continue
        category = calendar_category(str(event.get("calendar") or ""))
        if category in hidden:
            continue
        out.append({
            "title": str(event.get("summary") or "Untitled event"),
            "start_at": start.timestamp(),
            "all_day": bool(event.get("all_day", False)),
            "calendar": str(event.get("calendar") or ""),
            "category": category,
        })
        if len(out) >= _GLANCE_EVENT_LIMIT:
            break
    return out


def _wake_status() -> dict[str, Any] | None:
    from brain import wake_alarm
    try:
        return wake_alarm.public()
    except Exception as exc:  # noqa: BLE001 - glance must still render
        log.warning("Wake alarm unreadable: %s", exc)
        return None


def _portfolio_glance() -> dict[str, Any] | None:
    """Value and today's change for the TV, or None when Schwab isn't linked."""
    from integrations import schwab
    try:
        snap = schwab.snapshot()
    except Exception as exc:  # noqa: BLE001 - glance must still render
        log.warning("Portfolio unavailable: %s", exc)
        return None
    if "value" not in snap:
        return None
    return {key: snap.get(key) for key in ("value", "today_change", "today_pct", "linked", "streaming")}


def glance_status() -> dict[str, Any]:
    """One snapshot for a screen across the room: who to greet and how to look,
    weather, the Now feed, downloads in flight and alerts from the last day.
    Every section degrades on its own; the call itself never fails."""
    from brain import event_bus
    from brain import theme as shared_theme
    with ThreadPoolExecutor(max_workers=4) as pool:
        now = pool.submit(now_status)
        weather = pool.submit(weather_status)
        torrents = pool.submit(torrents_status)
        events = pool.submit(_glance_events)
        sections = {}
        for key, future in (("now", now), ("weather", weather), ("torrents", torrents), ("events", events)):
            try:
                sections[key] = future.result()
            except Exception as exc:  # noqa: BLE001 - glance must still render
                log.warning("Glance section %s unavailable: %s", key, exc)
                sections[key] = {"error": str(exc)}
    downloads = [
        {"name": t["name"], "progress": t["progress"], "eta": t["eta"]}
        for t in sections["torrents"].get("torrents", [])
        if t.get("progress", 1) < 1
        and not str(t.get("state", "")).startswith(("paused", "stopped", "error", "missing"))
    ]
    cutoff = time.time() - 24 * 3600
    alerts = [a.to_dict() for a in reversed(event_bus.since(0, limit=200)) if a.created_at >= cutoff]
    return {
        "generated_at": time.time(),
        "address": (os.getenv("ADDRESS") or "sir").strip() or "sir",
        "theme": shared_theme.current()["theme"],
        "weather": sections["weather"],
        "now": sections["now"],
        "events": sections["events"] if isinstance(sections["events"], list) else [],
        "wake": _wake_status(),
        "portfolio": _portfolio_glance(),
        "downloads": downloads[:5],
        "alerts": alerts[:5],
    }

_CALENDAR_TTL_S = 300.0
_CALENDAR_PARTIAL_TTL_S = 30.0


def _calendar_events() -> list[dict[str, Any]] | None:
    """Sixty days of calendar, shared by the Now snapshot and the study brief.

    A fetch that missed some calendars is kept only briefly so the next poll
    retries instead of showing a half-empty agenda for five minutes."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get("calendar-events")
        if hit and hit[0] > now:
            return hit[1]
    events = upcoming_events(max_results=2500, hours_ahead=60 * 24, allow_interactive=False)
    ttl = _CALENDAR_PARTIAL_TTL_S if google_calendar.last_fetch_partial else _CALENDAR_TTL_S
    with _cache_lock:
        _cache["calendar-events"] = (time.monotonic() + ttl, events)
    return events


# --- Study brief -----------------------------------------------------------

def study_brief_status() -> dict[str, Any]:
    """Settings plus the composed briefs the phone should schedule as local
    notifications. Composition is memoised in ``brain.study_brief``; here we
    only add the calendar's availability."""
    if not calendar_configured():
        return {**study_brief.compose([]), "error": "Calendar is not configured."}
    events = _calendar_events()
    if events is None:
        return {**study_brief.compose([]), "error": "Calendar could not be reached."}
    return {**study_brief.compose_cached(events), "error": ""}


# --- Media storage ---------------------------------------------------------

def media_storage_status() -> dict[str, Any]:
    """Disk usage for the one media filesystem, never all drives.

    Two sources, in priority order:

    1. ``MEDIA_STORAGE_PATH`` — a filesystem mounted on the JARVIS host,
       measured locally.
    2. Radarr's (then Sonarr's) ``/api/v3/diskspace`` report, so a library
       that lives on the NAS is measured without mounting anything on the
       JARVIS host. The entry chosen is the mount that holds the service's
       root folder (``RADARR_ROOT_FOLDER`` / ``SONARR_ROOT_FOLDER`` when set,
       otherwise the service's first root folder).
    """
    return _cached("media-storage", _STORAGE_TTL_S, _media_storage_uncached)


def _storage_payload(label: str, total: int, free: int, used: int | None = None) -> dict[str, Any]:
    if used is None:
        used = max(total - free, 0)
    return {
        "error": "",
        "label": label,
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_fraction": round(used / total, 4) if total else 0.0,
    }


def _media_storage_uncached() -> dict[str, Any]:
    path = os.getenv("MEDIA_STORAGE_PATH", "").strip()
    label = os.getenv("MEDIA_STORAGE_LABEL", "Media storage").strip() or "Media storage"
    empty = {
        "label": label,
        "total_bytes": 0,
        "used_bytes": 0,
        "free_bytes": 0,
        "used_fraction": 0.0,
    }
    if path:
        try:
            usage = shutil.disk_usage(os.path.expanduser(path))
        except OSError as exc:
            log.warning("Media storage check failed for %s: %s", path, exc)
            return {"error": "Media storage is not mounted or reachable.", **empty}
        return _storage_payload(label, usage.total, usage.free, usage.used)

    remote_error = ""
    for service in ("radarr", "sonarr"):
        if not servarr_configured(service):
            continue
        result = _servarr_disk_space(service)
        if isinstance(result, str):
            remote_error = remote_error or result
            continue
        total, free = result
        return _storage_payload(label, total, free)

    if remote_error:
        return {"error": remote_error, **empty}
    return {"error": "Media storage path is not configured.", **empty}


def _servarr_disk_space(service: str) -> tuple[int, int] | str:
    """``(total_bytes, free_bytes)`` for the mount holding ``service``'s
    library, or an error string. Paths are as the service sees them (inside
    its container, typically), which is exactly what its diskspace report
    uses too."""
    ok, disks = servarr_request(service, "GET", "/api/v3/diskspace")
    if not ok:
        return f"Media storage could not be read from {service.title()}."
    if not isinstance(disks, list) or not disks:
        return f"{service.title()} reported no disk space information."

    root = os.getenv(f"{service.upper()}_ROOT_FOLDER", "").strip()
    if not root:
        ok, folders = get_root_folders(service)
        if ok and isinstance(folders, list) and folders:
            root = str(folders[0].get("path", "") or "")

    best: dict[str, Any] | None = None
    if root:
        root_norm = root.rstrip("/") or "/"
        for disk in disks:
            mount = str(disk.get("path", "") or "").rstrip("/") or "/"
            if mount == "/" or root_norm == mount or root_norm.startswith(mount + "/"):
                if best is None or len(mount) > len(str(best.get("path", "")).rstrip("/") or "/"):
                    best = disk
    if best is None:
        best = max(disks, key=lambda disk: int(disk.get("totalSpace", 0) or 0))

    total = int(best.get("totalSpace", 0) or 0)
    free = int(best.get("freeSpace", 0) or 0)
    if total <= 0:
        return f"{service.title()} reported an empty disk space entry."
    return total, free


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
    with ThreadPoolExecutor(max_workers=2) as pool:
        radarr = pool.submit(_servarr_section, "radarr")
        sonarr = pool.submit(_servarr_section, "sonarr")
        return {"radarr": radarr.result(), "sonarr": sonarr.result()}


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

    ok, recent = _cached(f"recent:{service}", _RECENT_TTL_S, lambda: _recent_additions(service))
    if not ok:
        section["error"] = section["error"] or str(recent)
        # Don't pin a failure for the long recent-list TTL.
        invalidate(f"recent:{service}")
    else:
        section["recent"] = recent
    return section


def _recent_additions(service: str) -> tuple[bool, Any]:
    """The library's five newest additions, or ``(False, error)``."""
    library_path = "/api/v3/movie" if service == "radarr" else "/api/v3/series"
    ok, items = servarr_request(service, "GET", library_path)
    if not ok:
        return False, items
    if not isinstance(items, list):
        return True, []
    newest = sorted(items, key=lambda item: str(item.get("added") or ""), reverse=True)
    return True, [
        {
            "title": str(item.get("title", "Unknown")),
            "year": int(item.get("year", 0) or 0),
            "added": str(item.get("added") or ""),
        }
        for item in newest[:5]
    ]


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


def _remote_hosts() -> dict[str, str]:
    """``LINK_REMOTE_HOSTS``: ``lan-host=tailscale-host,...`` (e.g.
    ``10.0.0.147=100.97.179.53``), so links also work away from home."""
    pairs = (item.split("=", 1) for item in os.getenv("LINK_REMOTE_HOSTS", "").split(",") if "=" in item)
    return {lan.strip().lower(): remote.strip() for lan, remote in pairs if lan.strip() and remote.strip()}


def _remote_url(url: str, hosts: dict[str, str]) -> str:
    parts = urlsplit(url)
    remote = hosts.get((parts.hostname or "").lower())
    if not remote:
        return ""
    netloc = remote + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _link_targets() -> list[dict[str, str]]:
    """Configured services only; entries with an unset var are omitted.

    ``remote_url`` (Tailscale) is "" when no mapping covers the host; the app
    uses ``url`` when the home network answers and ``remote_url`` otherwise."""
    hosts = _remote_hosts()
    out = []
    for name, url, icon in _service_candidates():
        if not url:
            continue
        url = _API_SUFFIX_RE.sub("", url)
        out.append({"name": name, "url": url, "remote_url": _remote_url(url, hosts), "icon_hint": icon})
    return out


def services_status() -> dict[str, Any]:
    """Reachability + latency for every known service (down when unset)."""
    return _cached("services", _SERVICES_TTL_S, _services_uncached)


def _ping_service(name: str, url: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "up": False, "latency_ms": None, "error": ""}
    if not url:
        entry["error"] = f"{name} is not configured."
        return entry
    started = time.monotonic()
    try:
        # Any HTTP response (401/403 included) proves the service is up;
        # auth'd checks aren't needed for reachability.
        requests.get(url, timeout=_PING_TIMEOUT_S, allow_redirects=False)
    except requests.RequestException as exc:
        entry["error"] = str(exc)
        return entry
    entry["up"] = True
    entry["latency_ms"] = int((time.monotonic() - started) * 1000)
    return entry


def _services_uncached() -> dict[str, Any]:
    candidates = _service_candidates()
    # In parallel: one down service used to add its full 5 s timeout to the
    # whole response. Order is preserved.
    with ThreadPoolExecutor(max_workers=max(1, len(candidates))) as pool:
        services = list(pool.map(lambda item: _ping_service(item[0], item[1]), candidates))
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
    return {"error": "", "entity_id": entity_id, "state": _settled_state(entity_id, action)}


def home_scenes() -> dict[str, Any]:
    """Every saved Home Assistant scene, for the app's scene picker.

    Saved scenes carry an ``id`` attribute; snapshots JARVIS takes with
    ``scene.create`` (Barn Door's ``barn_door_before``) don't, so they stay
    hidden. A scene's state is when it last ran: ``last`` is the most recent.
    """
    return _cached("scenes", _ENTITIES_TTL_S, _home_scenes_uncached)


def _home_scenes_uncached() -> dict[str, Any]:
    ok, payload = get_states()
    if not ok or not isinstance(payload, list):
        return {"error": str(payload), "scenes": [], "last": None}
    scenes = []
    for entity in payload:
        entity_id = str(entity.get("entity_id", ""))
        attributes = entity.get("attributes", {}) or {}
        if not entity_id.startswith("scene.") or "id" not in attributes:
            continue
        state = str(entity.get("state", ""))
        scenes.append({
            "entity_id": entity_id,
            "name": str(attributes.get("friendly_name", entity_id)),
            "icon": str(attributes.get("icon", "")),
            "last_activated": state if state[:2] == "20" else None,  # ISO time, else unknown
        })
    scenes.sort(key=lambda s: s["name"].lower())
    ran = [s for s in scenes if s["last_activated"]]
    last = max(ran, key=lambda s: s["last_activated"])["entity_id"] if ran else None
    return {"error": "", "scenes": scenes, "last": last}


def activate_scene(entity_id: str) -> dict[str, Any]:
    """Run one saved scene. ValueError (a 400) unless it's in ``home_scenes``."""
    entity_id = (entity_id or "").strip().lower()
    known = {s["entity_id"] for s in _home_scenes_uncached()["scenes"]}
    if entity_id not in known:
        raise ValueError("not a saved Home Assistant scene")
    message = call_discovered_service("scene", "turn_on", entity_id=entity_id)
    invalidate("scenes")
    invalidate("entities")
    if _HA_SUCCESS_MARKER not in message.lower():
        return {"error": message, "entity_id": entity_id}
    return {"error": "", "entity_id": entity_id}


# Home Assistant acknowledges a service call before the device reports back,
# so an immediate read still shows the old state (the app's switch then
# bounced back). Poll briefly for the requested state before answering.
_CONTROL_SETTLE_SECONDS = 2.0
_CONTROL_POLL_SECONDS = 0.25


def _settled_state(entity_id: str, action: str) -> str | None:
    deadline = time.monotonic() + _CONTROL_SETTLE_SECONDS
    state = None
    while True:
        ok, states = get_states()
        if ok and isinstance(states, list):
            for entity in states:
                if str(entity.get("entity_id", "")) == entity_id:
                    state = str(entity.get("state", "unknown"))
                    break
        if state == action or time.monotonic() >= deadline:
            return state
        time.sleep(_CONTROL_POLL_SECONDS)


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
