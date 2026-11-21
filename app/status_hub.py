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


# The apps' room screens follow this instead of polling: Core's HA live feed
# calls ``home_changed`` for every dashboard entity or scene change, and
# ``GET /api/home/stream`` sends a ``home`` event when the number moves, so
# the phone, iPad and TV refetch within a fraction of a second.
_home_seq = 0


def home_changed(scenes: bool = False) -> None:
    global _home_seq
    from brain import traffic
    traffic.record("nas", "core", "home assistant")  # HA's websocket push
    invalidate("entities")
    if scenes:
        invalidate("scenes")
    with _cache_lock:
        _home_seq += 1


def home_seq() -> int:
    return _home_seq


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
    shared_look = shared_theme.current()
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
        **{key: shared_look[key] for key in ("theme", "core", "lab")},  # lab: Experimenting switches showing (TV)
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
        item = {
            "entity_id": entity_id,
            "friendly_name": str(attributes.get("friendly_name", entity_id)),
            "domain": entity_id.split(".", 1)[0] if "." in entity_id else "",
            "state": str(entity.get("state", "unknown")),
        }
        if entity_id.startswith("light."):
            item["light"] = _light_details(attributes, entity_id)
            target = rainbows().get(entity_id, ("", ""))[0]
            if target.startswith("scene.") and target not in by_id:
                item["light"]["rainbow"] = False  # its Tap-to-Run isn't in HA yet
        elif entity_id.startswith("fan."):
            item["fan"] = _fan_details(attributes)
        entities.append(item)
    # The iPad's night screen dims only after dark (sun.sun), lights all off.
    sun = by_id.get("sun.sun")
    return {"error": "", "entities": entities, "sun": str(sun.get("state", "")) if sun else ""}


# Nick's rainbows are Govee DIY scenes picked through each light's dropdown
# (the Govee app's own pick never shows in HA). LIGHT_RAINBOWS overrides:
# "light.x=select.x_diy_scene:Name,..." or "light.x=scene.y" (a scene that
# puts the light in its rainbow: the fan ring's multi-colour mode is only
# reachable through a Smart Life Tap-to-Run, which HA's Tuya integration
# shows as a scene; ops/core/room_rainbows.py names it).
_DEFAULT_RAINBOWS = ("light.nick_strip_lights=select.nick_strip_lights_diy_scene:Real Rainbow,"
                     "light.desk_lights_2=select.desk_lights_diy_scene:New Rainbow,"
                     # the Govee lamp's own rotating rainbow (a built-in scene, not DIY)
                     "light.lamp=select.lamp_scene:Rainbow,"
                     "light.nicks_fan=scene.fan_light_rainbow")


def rainbows() -> dict[str, tuple[str, str]]:
    """light entity -> (DIY-scene select, option) or (scene, "") for the Rainbow button."""
    found = {}
    for pair in os.getenv("LIGHT_RAINBOWS", _DEFAULT_RAINBOWS).split(","):
        light, _, target = pair.partition("=")
        select, _, option = target.partition(":")
        if light.strip() and select.strip().startswith("select.") and option.strip():
            found[light.strip().lower()] = (select.strip(), option.strip())
        elif light.strip() and select.strip().startswith("scene."):
            found[light.strip().lower()] = (select.strip().lower(), "")
    return found


# Lights that are only ever colour: the fan's light is one Tuya light whose
# white mode is the LED Nick hates and whose colour mode is its RGB ring, so
# from the apps it only ever gets a colour (RING_LIGHTS overrides).
_RING_DEFAULT_RGB = [0, 200, 255]
_ring_rgb: dict[str, list[int]] = {}


def ring_lights() -> set[str]:
    return {e.strip().lower() for e in os.getenv("RING_LIGHTS", "light.nicks_fan").split(",") if e.strip()}


def _light_details(attributes: dict[str, Any], entity_id: str = "") -> dict[str, Any]:
    """What a light can do and where it is now, for the brightness/colour sheet."""
    modes = [str(m) for m in attributes.get("supported_color_modes") or []]
    if entity_id in ring_lights():
        modes = [m for m in modes if m != "color_temp"]  # no white: the sheet hides it
    brightness = attributes.get("brightness")
    rgb = attributes.get("rgb_color")
    return {
        "dimmable": any(m != "onoff" for m in modes),
        "color": any(m in ("hs", "rgb", "rgbw", "rgbww", "xy") for m in modes),
        "temperature": "color_temp" in modes,
        "brightness_pct": round(brightness * 100 / 255) if isinstance(brightness, (int, float)) else None,
        "rgb": list(rgb)[:3] if isinstance(rgb, (list, tuple)) else None,
        "kelvin": attributes.get("color_temp_kelvin"),
        "min_kelvin": attributes.get("min_color_temp_kelvin"),
        "max_kelvin": attributes.get("max_color_temp_kelvin"),
        "rainbow": entity_id in rainbows(),
    }


def _fan_details(attributes: dict[str, Any]) -> dict[str, Any]:
    """Speed and modes for the iPad's fan tile (hold and drag) and fan sheet."""
    percentage = attributes.get("percentage")
    step = attributes.get("percentage_step")
    return {
        "percentage": int(percentage) if isinstance(percentage, (int, float)) else None,
        "step": float(step) if isinstance(step, (int, float)) and step > 0 else 1.0,
        "preset_modes": [str(m) for m in attributes.get("preset_modes") or []],
        "preset_mode": attributes.get("preset_mode"),
    }


# --- Quick actions (guarded, allowlist-bound) -------------------------------
#
# Both integrations' guarded control paths return human-readable strings, not
# status tuples. Their success replies are stable and distinctive, so we
# detect success by marker; anything else is surfaced verbatim in ``error``.
_HA_SUCCESS_MARKER = "command sent"       # call_service success reply
_QBT_SUCCESS_MARKER = "accepted for"      # manage_torrent success reply


def home_control(entity_id: str, action: str, settle: bool = True) -> dict[str, Any]:
    """Turn one allowlisted Home Assistant entity on or off.

    ``settle`` waits (up to 2 s) for HA to report the new state, which the
    apps' switches need; the room voice skips it so its jingle isn't held up.

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
    if action == "on" and entity_id in ring_lights():  # colour, never the white LED
        return {**set_light(entity_id, rgb=_ring_rgb.get(entity_id, _RING_DEFAULT_RGB)),
                "state": "on"}
    message = call_discovered_service(domain, f"turn_{action}", entity_id=entity_id)
    if _HA_SUCCESS_MARKER not in message.lower():
        return {"error": message, "entity_id": entity_id, "state": None}

    invalidate("entities")
    state = _settled_state(entity_id, action) if settle else None
    return {"error": "", "entity_id": entity_id, "state": state}


def set_light(entity_id: str, brightness_pct: int | None = None,
              rgb: list[int] | None = None, kelvin: int | None = None,
              rainbow: bool = False) -> dict[str, Any]:
    """Brightness and/or colour (or Nick's rainbow) for one allowlisted light,
    turning it on. ValueError (a 400) for anything off the allowlist or not a light."""
    entity_id = (entity_id or "").strip().lower()
    if not entity_id.startswith("light.") or entity_id not in _dashboard_allowlist():
        raise ValueError("entity_id is not an allowlisted light")
    if rainbow:
        if entity_id not in rainbows():
            raise ValueError("this light has no rainbow set (LIGHT_RAINBOWS)")
        select, option = rainbows()[entity_id]
        if select.startswith("scene."):  # the scene turns the light on in its rainbow
            message = call_discovered_service("scene", "turn_on", entity_id=select)
            invalidate("entities")
            ok = _HA_SUCCESS_MARKER in message.lower()
            return {"error": "" if ok else message, "entity_id": entity_id}
        call_discovered_service("light", "turn_on", entity_id=entity_id,
                                service_data={"brightness_pct": brightness_pct or 100})
        message = call_discovered_service("select", "select_option", entity_id=select,
                                          service_data={"option": option})
        invalidate("entities")
        ok = _HA_SUCCESS_MARKER in message.lower()
        return {"error": "" if ok else message, "entity_id": entity_id}
    data: dict[str, Any] = {}
    if entity_id in ring_lights():
        if kelvin is not None and rgb is None:
            raise ValueError("this light is colour-only (RING_LIGHTS): no white")
        rgb = rgb or _ring_rgb.get(entity_id, _RING_DEFAULT_RGB)  # brightness alone mustn't wake white
        _ring_rgb[entity_id] = [max(0, min(255, int(c))) for c in rgb[:3]]
    if brightness_pct is not None:
        data["brightness_pct"] = max(1, min(100, int(brightness_pct)))
    if rgb is not None:
        data["rgb_color"] = [max(0, min(255, int(c))) for c in rgb[:3]]
    elif kelvin is not None:
        data["color_temp_kelvin"] = max(1500, min(9000, int(kelvin)))
    message = call_discovered_service("light", "turn_on", entity_id=entity_id, service_data=data)
    invalidate("entities")
    if _HA_SUCCESS_MARKER not in message.lower():
        return {"error": message, "entity_id": entity_id}
    return {"error": "", "entity_id": entity_id}


def set_fan(entity_id: str, percentage: int | None = None, preset_mode: str | None = None) -> dict[str, Any]:
    """Speed (0 turns it off) and/or preset mode for one allowlisted fan.
    ValueError (a 400) for anything off the allowlist or not a fan."""
    entity_id = (entity_id or "").strip().lower()
    if not entity_id.startswith("fan.") or entity_id not in _dashboard_allowlist():
        raise ValueError("entity_id is not an allowlisted fan")
    if percentage is None and not preset_mode:
        raise ValueError("give a percentage or a preset_mode")
    message = "command sent"
    if preset_mode:
        message = call_discovered_service("fan", "set_preset_mode", entity_id=entity_id,
                                          service_data={"preset_mode": preset_mode})
    if percentage is not None and _HA_SUCCESS_MARKER in message.lower():
        data = {"percentage": max(0, min(100, int(percentage)))}
        message = call_discovered_service("fan", "set_percentage", entity_id=entity_id, service_data=data)
    invalidate("entities")
    if _HA_SUCCESS_MARKER not in message.lower():
        return {"error": message, "entity_id": entity_id}
    return {"error": "", "entity_id": entity_id}


def home_scenes() -> dict[str, Any]:
    """Every saved Home Assistant scene, for the app's scene picker.

    Saved scenes carry an ``id`` attribute; snapshots JARVIS takes with
    ``scene.create`` (Barn Door's ``barn_door_before``) don't, so they stay
    hidden. A scene's state is when it last ran: ``last`` is the most recent.
    ``current`` is the scene the room is "in": ``last``, unless that was a
    resting scene (``RESTING_SCENES``: Normal, Peace, Goodnight), which
    means no scene at all.
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
    current = last if last and last not in _resting_scenes() else None
    if current == "scene.movie":
        # Exit Movie (brain/movie_exit.py) put the room back; HA still says Movie ran last.
        from brain import movie_exit
        movie = next(s for s in scenes if s["entity_id"] == current)
        exited, previous = movie_exit.current_override(movie["last_activated"])
        if exited:
            current = previous if previous in {s["entity_id"] for s in scenes} else None
    return {"error": "", "scenes": scenes, "last": last, "current": current}


_NORMAL_SCENE = "scene.normal"


def _resting_scenes() -> set[str]:
    raw = os.getenv("RESTING_SCENES", f"{_NORMAL_SCENE},scene.peace,scene.goodnight")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def room_away() -> bool:
    """Nick is out, so the room keeps quiet (alerts, reminders and
    announcements still reach the phone), and nobody home alone gets a voice
    out of the dark.

    Since 2026-11-20 his iPhone's GPS decides (``brain/presence.py``,
    ``person.nick``): away is quiet whatever the scene, home talks again.
    Only when GPS can't be read does the old guess apply: the last scene run
    is an away scene (``AWAY_SCENES``, default ``scene.peace``) and every room
    light is still off. Fails open: when HA can't be read, it's False."""
    from brain import presence
    where = presence.location()
    if where is not None:
        return where == "away"
    raw = os.getenv("AWAY_SCENES", "scene.peace")
    away = {e.strip().lower() for e in raw.split(",") if e.strip()}
    try:
        scenes = home_scenes()
        if scenes.get("error") or str(scenes.get("last") or "").lower() not in away:
            return False
        listing = home_entities()
    except Exception:  # noqa: BLE001 - a broken read never mutes JARVIS
        log.debug("room_away check failed", exc_info=True)
        return False
    lights = [e for e in listing.get("entities", []) if e.get("domain") == "light"]
    if listing.get("error") or not lights:
        return False
    return all(e.get("state") != "on" for e in lights)


def _scene_matches(entity_id: str) -> bool:
    """Every entity in the saved scene is on/off (and each dropdown on its
    option) the way the scene sets it. Brightness and colour don't count."""
    from integrations.home_assistant import _request
    ok, config = _request("GET", f"/api/config/scene/config/{entity_id.split('.', 1)[1]}")
    wanted = (config or {}).get("entities") if ok and isinstance(config, dict) else None
    ok, states = get_states()
    if not wanted or not ok or not isinstance(states, list):
        return False
    now = {str(e.get("entity_id")): str(e.get("state")) for e in states}
    for target, spec in wanted.items():
        state = str(spec.get("state") if isinstance(spec, dict) else spec)
        if now.get(target) in (None, "unavailable", "unknown"):
            continue  # a device that's offline can't break the match
        if now.get(target) != state:
            return False
    return True


def _leave_scene(entity_id: str) -> str:
    """Out of a scene to the everyday room: HA's Normal scene when there is
    one, else the bedroom light on and the rest of that scene off."""
    if _NORMAL_SCENE in _scene_ids(home_scenes()):
        return call_discovered_service("scene", "turn_on", entity_id=_NORMAL_SCENE)
    from integrations.home_assistant import _request
    ok, config = _request("GET", f"/api/config/scene/config/{entity_id.split('.', 1)[1]}")
    entities = list(((config or {}).get("entities") or {}).keys()) if ok and isinstance(config, dict) else []
    bedroom = os.getenv("NORMAL_LIGHT", "light.bedroom_lights").strip().lower()
    message = call_discovered_service("light", "turn_on", entity_id=bedroom)
    for target in entities:
        domain = target.split(".", 1)[0]
        if target == bedroom or target == "switch.wii_u" or domain not in ("light", "switch", "fan"):
            continue
        call_discovered_service(domain, "turn_off", entity_id=target)
    return message


def _scene_ids(listing: dict[str, Any]) -> set[str]:
    return {s["entity_id"] for s in listing.get("scenes", [])}


_NEVER_IN_SCENES = {"switch.wii_u"}  # powered only when Nick plays (a Wii burned out on standby)
_FAN_DEFAULTS = {"direction": "forward", "preset_mode": "fresh"}  # HA 2026.10 fan scenes need both


def scene_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def capture_scene() -> dict[str, Any]:
    """The room as it is now, as a Home Assistant scene's ``entities`` block.

    Only the app's allowlisted lights, switches and fans. The Wii U plug is
    never included; ring lights (the fan light) are only ever saved off, so
    a saved scene can't turn on the LED Nick hates. Offline devices are left
    out rather than saved as "unavailable". Govee DIY rainbows can't be read
    back from HA, so a rainbow light is saved at its plain colour.
    """
    ok, payload = get_states()
    if not ok or not isinstance(payload, list):
        raise RuntimeError(str(payload))
    allowed = set(_dashboard_allowlist()) - _NEVER_IN_SCENES
    entities: dict[str, dict[str, Any]] = {}
    for entity in payload:
        entity_id = str(entity.get("entity_id", "")).lower()
        domain = entity_id.split(".", 1)[0]
        state = str(entity.get("state", ""))
        if entity_id not in allowed or domain not in ("light", "switch", "fan") or state not in ("on", "off"):
            continue
        attributes = entity.get("attributes", {}) or {}
        spec: dict[str, Any] = {"state": state}
        if state == "on" and domain == "light":
            if entity_id in ring_lights():
                spec = {"state": "off"}
            else:
                if attributes.get("brightness") is not None:
                    spec["brightness"] = int(attributes["brightness"])
                if attributes.get("color_mode") == "color_temp" and attributes.get("color_temp_kelvin"):
                    spec["color_temp_kelvin"] = int(attributes["color_temp_kelvin"])
                elif attributes.get("rgb_color"):
                    spec["rgb_color"] = [int(c) for c in attributes["rgb_color"][:3]]
        elif state == "on" and domain == "fan":
            if attributes.get("percentage") is not None:
                spec["percentage"] = int(attributes["percentage"])
            for key, default in _FAN_DEFAULTS.items():
                spec[key] = attributes.get(key) or default
        entities[entity_id] = spec
    return entities


def describe_capture(entities: dict[str, dict[str, Any]]) -> str:
    """'3 lights on, 2 off, fan at 35%' for the confirmation prompt."""
    lights_on = sum(1 for e, s in entities.items() if e.startswith("light.") and s["state"] == "on")
    lights_off = sum(1 for e, s in entities.items() if e.startswith("light.") and s["state"] == "off")
    plugs_on = sum(1 for e, s in entities.items() if e.startswith("switch.") and s["state"] == "on")
    parts = [f"{lights_on} light{'s' if lights_on != 1 else ''} on", f"{lights_off} off"]
    if plugs_on:
        parts.append(f"{plugs_on} plug{'s' if plugs_on != 1 else ''} on")
    for entity_id, spec in entities.items():
        if entity_id.startswith("fan."):
            parts.append(f"fan at {spec.get('percentage', 100)}%" if spec["state"] == "on" else "fan off")
    return ", ".join(parts)


def save_scene(name: str, entities: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Write (or replace) a saved HA scene through HA's scene config API,
    which reloads scenes itself. The scene's id is its name's slug, so it
    becomes ``scene.<slug>`` like the scenes made in the HA editor."""
    from integrations.home_assistant import _request
    slug = scene_slug(name)
    if not slug or not entities:
        raise ValueError("a scene needs a name and at least one device")
    ok, result = _request("POST", f"/api/config/scene/config/{slug}",
                          json={"id": slug, "name": name.strip(), "entities": entities})
    invalidate("scenes")
    return {"error": "" if ok else str(result), "entity_id": f"scene.{slug}"}


def activate_scene(entity_id: str, toggle: bool = False) -> dict[str, Any]:
    """Run one saved scene. ValueError (a 400) unless it's in ``home_scenes``.

    ``toggle`` (the apps' scene tiles): pressing the scene the room is in
    while everything is still as it set it leaves the scene (``_leave_scene``,
    ``left`` true); if something changed since, it runs again to put it back.
    """
    entity_id = (entity_id or "").strip().lower()
    if entity_id not in _scene_ids(home_scenes()):  # cached; a scene made since needs a fresh look
        invalidate("scenes")
        if entity_id not in _scene_ids(home_scenes()):
            raise ValueError("not a saved Home Assistant scene")
    invalidate("scenes")
    room_scene = home_scenes().get("current") if toggle or entity_id == "scene.movie" else None
    left = bool(toggle) and room_scene == entity_id and _scene_matches(entity_id)
    if entity_id == "scene.movie" and not left:
        from brain import movie_exit
        movie_exit.before_movie(room_scene)  # so Exit Movie can put the room back
    message = _leave_scene(entity_id) if left else call_discovered_service("scene", "turn_on", entity_id=entity_id)
    invalidate("scenes")
    invalidate("entities")
    if _HA_SUCCESS_MARKER not in message.lower():
        return {"error": message, "entity_id": entity_id, "left": left}
    if not left:
        import threading
        threading.Thread(target=_force_scene_offs, args=(entity_id,), daemon=True, name="scene-offs").start()
    return {"error": "", "entity_id": entity_id, "left": left}


# The same Govee light under two integrations: the cloud one the scenes use,
# and the LAN one ("<cloud>=<local>,...").
_DEFAULT_LIGHT_TWINS = "light.desk_lights_2=light.desk_lights,light.nick_strip_lights=light.strip_lights"


def _light_twins() -> dict[str, str]:
    pairs = {}
    for item in os.getenv("LIGHT_TWINS", _DEFAULT_LIGHT_TWINS).split(","):
        cloud, _, local = item.partition("=")
        if cloud.strip() and local.strip():
            pairs[cloud.strip().lower()] = local.strip().lower()
            pairs[local.strip().lower()] = cloud.strip().lower()
    return pairs


def scene_off_lights(config: dict[str, Any] | None) -> list[str]:
    """The lights a scene turns off, plus each one's twin."""
    twins = _light_twins()
    off: set[str] = set()
    for target, spec in ((config or {}).get("entities") or {}).items():
        state = str(spec.get("state") if isinstance(spec, dict) else spec)
        if target.startswith("light.") and state == "off":
            off.add(target)
            if target in twins:
                off.add(twins[target])
    return sorted(off)


def _force_scene_offs(entity_id: str, delay: float = 1.5) -> None:
    """Send a plain turn_off to every light the scene turns off. HA's scene
    skips a light it already thinks is off, and the Govee cloud integration
    can think so while the light is lit (2026-11-01: Goodnight left the desk
    lights on), so a stale state can't keep a light on at bedtime."""
    from integrations.home_assistant import _request, call_service
    time.sleep(delay)  # after the scene's own commands
    try:
        ok, config = _request("GET", f"/api/config/scene/config/{entity_id.split('.', 1)[1]}")
        lights = scene_off_lights(config if ok and isinstance(config, dict) else None)
        if lights:
            call_service("light", "turn_off", service_data={"entity_id": lights})
    except Exception:  # noqa: BLE001
        log.exception("Scene %s: forcing its lights off failed", entity_id)
    invalidate("entities")


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
