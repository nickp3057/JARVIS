"""Shared plumbing for the Radarr and Sonarr integrations.

Radarr and Sonarr are both *Servarr* apps and speak nearly identical v3 REST
APIs, so the HTTP client, quality-profile / root-folder lookups, and the
natural-language quality resolver live here once. ``radarr.py`` and
``sonarr.py`` are thin wrappers over this module that add the movie- vs
series-specific request shapes.

Config is read live from the environment (``.env``) on every call so runtime
edits via ``update_setting`` take effect without a restart. Everything degrades
gracefully: if a service isn't configured, callers get ``is_configured() ==
False`` and return a polite spoken line instead of raising.

Quality-profile mapping (requirement: no hardcoded IDs in code) is defined in
``.env`` as a phrase-to-profile-name map, e.g.::

    RADARR_QUALITY_MAP=4k efficient => UHD Bluray + WEB; 1080p => HD-1080p

At call time we fuzzy-match the user's phrase against the map keys, then
fuzzy-match the resulting profile *name* against the profile list pulled live
from ``/api/v3/qualityprofile`` to get its ID.
"""

from __future__ import annotations

import difflib
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple

import requests
from dotenv import load_dotenv

from jarvis_logger import get_logger

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

log = get_logger(__name__)

# (connect, read): a powered-off NAS fails in 4 s instead of hanging for 15.
_TIMEOUT_SECONDS = (4, 15)
# Reads are retried once after this pause; writes never are (see ``call``).
_RETRY_DELAY_SECONDS = 1.0
# Profiles and root folders are re-fetched after this, so a profile renamed in
# the Radarr UI doesn't break adds until JARVIS restarts.
_CACHE_TTL_SECONDS = 300

# Per-service cached (session, url, key) so a runtime key/URL change rebuilds it.
_sessions: dict[str, tuple[requests.Session, str, str]] = {}

# Per-service cache of the quality-profile and root-folder lists (rarely change
# within a run; call the resolver helpers with refresh=True to bust it).
_profile_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_rootfolder_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class Result(NamedTuple):
    """Outcome of one Servarr call.

    ``kind`` is ``ok``, ``unconfigured``, ``unreachable`` (never connected),
    ``timeout`` (connected, no answer: a write may still have landed),
    ``auth`` (bad API key), ``not_found``, ``rejected`` (other 4xx, usually
    validation) or ``server`` (5xx).
    """

    ok: bool
    payload: Any
    kind: str


# --- Config ---------------------------------------------------------------

def _service_url(service: str) -> str:
    return os.getenv(f"{service.upper()}_URL", "").strip().rstrip("/")


def _service_key(service: str) -> str:
    return os.getenv(f"{service.upper()}_API_KEY", "").strip()


def is_configured(service: str) -> bool:
    """True if this service has both a URL and an API key set."""
    return bool(_service_url(service) and _service_key(service))


# Nick's profiles: movies go to Radarr's "Standard", shows to Sonarr's "TV".
_DEFAULT_PROFILES = {"radarr": "Standard", "sonarr": "TV"}


def default_quality(service: str) -> str:
    fallback = _DEFAULT_PROFILES.get(service.lower(), "1080p")
    return os.getenv(f"{service.upper()}_DEFAULT_QUALITY", fallback).strip() or fallback


def _quality_map(service: str) -> list[tuple[str, str]]:
    """Parse ``{SERVICE}_QUALITY_MAP`` into ordered (phrase, profile_name) pairs.

    Format: ``phrase => Profile Name; other phrase => Other Profile``. The
    ``=>`` separator (not ``=``) and ``;`` delimiter keep profile names that
    contain ``+`` / spaces intact.
    """
    raw = os.getenv(f"{service.upper()}_QUALITY_MAP", "").strip()
    pairs: list[tuple[str, str]] = []
    if not raw:
        return pairs
    for chunk in raw.split(";"):
        if "=>" not in chunk:
            continue
        phrase, name = chunk.split("=>", 1)
        phrase, name = phrase.strip(), name.strip()
        if phrase and name:
            pairs.append((phrase, name))
    return pairs


# --- HTTP ---------------------------------------------------------------

def _session(service: str) -> requests.Session | None:
    url, key = _service_url(service), _service_key(service)
    if not (url and key):
        return None
    cached = _sessions.get(service)
    if cached and cached[1] == url and cached[2] == key:
        return cached[0]
    sess = requests.Session()
    sess.headers.update({"X-Api-Key": key, "Content-Type": "application/json"})
    _sessions[service] = (sess, url, key)
    return sess


def _rejection_detail(response: requests.Response) -> str:
    """Servarr validation errors arrive as ``[{"errorMessage": ...}]``."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200].strip()
    items = body if isinstance(body, list) else [body]
    messages = [
        str(item.get("errorMessage") or item.get("message") or "").strip()
        for item in items if isinstance(item, dict)
    ]
    return "; ".join(m for m in messages if m)[:300] or response.text[:200].strip()


def call(service: str, method: str, path: str, **kwargs: Any) -> Result:
    """Perform a REST call against a Servarr service with a classified outcome.

    GETs are retried once on a connection failure, timeout or 5xx. Writes are
    never retried: a timed-out POST may already have been applied, so callers
    verify instead (see ``radarr.add_movie``).
    """
    name = service.title()
    sess = _session(service)
    if sess is None:
        return Result(False, f"{name} is not configured yet.", "unconfigured")

    url = f"{_service_url(service)}{path}"
    attempts = 2 if method.upper() == "GET" else 1
    result = Result(False, f"{name} did not answer.", "unreachable")
    for attempt in range(attempts):
        if attempt:
            time.sleep(_RETRY_DELAY_SECONDS)
        try:
            response = sess.request(method, url, timeout=_TIMEOUT_SECONDS, **kwargs)
        except requests.ConnectTimeout as exc:
            log.warning("%s connect timeout: %s %s -> %s", service, method, path, exc)
            result = Result(False, f"I can't reach {name} at {_service_url(service)}; the NAS or its container may be down.", "unreachable")
            continue
        except requests.Timeout as exc:
            log.warning("%s read timeout: %s %s -> %s", service, method, path, exc)
            result = Result(False, f"{name} accepted the connection but didn't answer in time; it may be overloaded or still starting.", "timeout")
            continue
        except requests.RequestException as exc:
            log.warning("%s request failed: %s %s -> %s", service, method, path, exc)
            result = Result(False, f"I can't reach {name} at {_service_url(service)}; the NAS or its container may be down.", "unreachable")
            continue

        status = response.status_code
        if status in (200, 201, 202):
            try:
                return Result(True, response.json(), "ok")
            except ValueError:
                return Result(True, response.text, "ok")

        log.warning("%s %s %s returned %s: %s", service, method, path, status, response.text[:500])
        if status in (401, 403):
            return Result(False, f"{name} rejected the API key; check {service.upper()}_API_KEY in .env.", "auth")
        if status == 404:
            return Result(False, f"{name} has no such item ({path}).", "not_found")
        if status >= 500:
            result = Result(False, f"{name} hit an internal error ({status}); its log will say why.", "server")
            continue
        return Result(False, f"{name} refused that ({status}): {_rejection_detail(response)}", "rejected")
    return result


def request(service: str, method: str, path: str, **kwargs: Any) -> tuple[bool, Any]:
    """Perform a REST call against a Servarr service.

    Returns ``(success, payload)`` where payload is the decoded JSON on success
    or a human-readable error string on failure. Use ``call`` when the kind of
    failure matters.
    """
    result = call(service, method, path, **kwargs)
    return result.ok, result.payload


def test_connection(service: str) -> str:
    """Ping a service's system status endpoint; return a spoken-friendly line."""
    if not is_configured(service):
        return f"{service.title()} isn't configured, sir."
    ok, payload = request(service, "GET", "/api/v3/system/status")
    if not ok:
        return str(payload)
    version = ""
    if isinstance(payload, dict):
        version = payload.get("version", "")
    return f"Connected to {service.title()}{f' (version {version})' if version else ''}, sir."


def get_queue(service: str, page_size: int = 50) -> tuple[bool, Any]:
    """Return the current Radarr/Sonarr activity queue."""
    params: dict[str, Any] = {
        "page": 1,
        "pageSize": max(1, min(200, int(page_size))),
        "sortKey": "timeleft",
        "sortDirection": "ascending",
    }
    if service == "radarr":
        params["includeUnknownMovieItems"] = "true"
        params["includeMovie"] = "true"
    elif service == "sonarr":
        params["includeUnknownSeriesItems"] = "true"
        params["includeSeries"] = "true"
        params["includeEpisode"] = "true"
    return request(service, "GET", "/api/v3/queue", params=params)


def get_health(service: str) -> tuple[bool, Any]:
    """Return active health warnings from a Servarr service."""
    return request(service, "GET", "/api/v3/health")


def _format_bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in {"B", "KB"} else f"{size:.1f} {unit}"
        size /= 1024
    return "unknown"


def _queue_title(service: str, record: dict[str, Any]) -> str:
    if service == "radarr":
        return str((record.get("movie") or {}).get("title") or record.get("title") or "Unknown movie")
    series = str((record.get("series") or {}).get("title") or "Unknown series")
    episode = str((record.get("episode") or {}).get("title") or "").strip()
    return f"{series} — {episode}" if episode else series


def queue_status(service: str | None = None) -> str:
    """Render current Radarr/Sonarr queues with progress, ETA, and warnings."""
    requested = (service or "").strip().lower()
    services = [requested] if requested in {"radarr", "sonarr"} else ["radarr", "sonarr"]
    lines: list[str] = []
    for current in services:
        if not is_configured(current):
            lines.append(f"{current.title()} is not configured.")
            continue
        ok, payload = get_queue(current)
        if not ok:
            lines.append(str(payload))
            continue
        records = payload.get("records", []) if isinstance(payload, dict) else []
        if not records:
            lines.append(f"{current.title()} queue is clear.")
            continue
        lines.append(f"{current.title()} queue: {len(records)} item{'s' if len(records) != 1 else ''}.")
        for record in records[:20]:
            size = float(record.get("size", 0) or 0)
            left = float(record.get("sizeleft", 0) or 0)
            progress = round((1 - left / size) * 100, 1) if size > 0 else 0.0
            status = str(record.get("trackedDownloadStatus") or record.get("status") or "unknown")
            eta = str(record.get("timeleft") or "ETA unknown")
            message = str(record.get("errorMessage") or "").strip()
            detail = f" — {message}" if message else ""
            lines.append(
                f"{_queue_title(current, record)}: {progress}% — {_format_bytes(left)} left — "
                f"{eta} — {status}{detail}"
            )
        if len(records) > 20:
            lines.append(f"Plus {len(records) - 20} more in {current.title()}.")
    return "\n".join(lines)


def health_status(service: str | None = None) -> str:
    """Render current Radarr/Sonarr health warnings."""
    requested = (service or "").strip().lower()
    services = [requested] if requested in {"radarr", "sonarr"} else ["radarr", "sonarr"]
    lines: list[str] = []
    for current in services:
        if not is_configured(current):
            lines.append(f"{current.title()} is not configured.")
            continue
        ok, payload = get_health(current)
        if not ok:
            lines.append(str(payload))
            continue
        warnings = payload if isinstance(payload, list) else []
        if not warnings:
            lines.append(f"{current.title()} reports no health warnings.")
            continue
        lines.append(f"{current.title()} health warnings:")
        for warning in warnings[:20]:
            lines.append(
                f"{warning.get('type', 'warning')}: {warning.get('message', 'No detail')}"
            )
    return "\n".join(lines)


# --- Fuzzy helpers ------------------------------------------------------

def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


_RESOLUTION_ALIASES = {"4k": "2160", "uhd": "2160", "2160p": "2160", "1080p": "1080",
                       "720p": "720", "480p": "480", "576p": "576", "sd": "480"}


def _resolutions(text: str) -> set[str]:
    found = set()
    for token in _normalize(text).split():
        token = _RESOLUTION_ALIASES.get(token, token.rstrip("p"))
        if token in {"480", "576", "720", "1080", "2160"}:
            found.add(token)
    return found


def _best_match(needle: str, haystack: list[str]) -> str | None:
    """Return the closest entry in ``haystack`` to ``needle`` or None.

    Tries, in order: exact normalized equality, containment either direction
    (the shortest, i.e. most specific, candidate wins; a tie is ambiguous and
    returns None so the caller can list options), then difflib similarity with
    a 0.6 cutoff. A candidate that names other resolutions than the needle
    ("HD-1080p" for "720p") is never chosen.
    """
    n = _normalize(needle)
    if not n or not haystack:
        return None

    wanted = _resolutions(needle)
    norm_map = {
        _normalize(h): h for h in haystack
        if not wanted or not _resolutions(h) or wanted & _resolutions(h)
    }
    if not norm_map:
        return None

    if n in norm_map:
        return norm_map[n]

    if wanted:
        # '4k' -> the single profile that explicitly names 2160p.
        named = [norm for norm, original in norm_map.items() if _resolutions(original)]
        if len(named) == 1:
            return norm_map[named[0]]

    contains = sorted(
        (norm for norm in norm_map if norm and (norm in n or n in norm)),
        key=len,
    )
    if contains:
        if len(contains) > 1 and len(contains[0]) == len(contains[1]):
            return None
        return norm_map[contains[0]]

    close = difflib.get_close_matches(n, list(norm_map.keys()), n=1, cutoff=0.6)
    if close:
        return norm_map[close[0]]
    return None


# --- Title lookup -----------------------------------------------------

_TRAILING_YEAR = re.compile(r"^(.*\S)\s*[\(\[]?((?:19|20)\d{2})[\)\]]?$")


def split_trailing_year(title: str) -> tuple[str, int | None]:
    """'Dune 2021' / 'Dune (2021)' -> ('Dune', 2021); anything else unchanged."""
    match = _TRAILING_YEAR.match((title or "").strip())
    if not match:
        return title, None
    return match.group(1).strip(" -,:(["), int(match.group(2))


def ranked_lookup(
    lookup: Callable[[str], tuple[bool, Any]], title: str,
) -> tuple[bool, Any, int | None]:
    """Run a Servarr title lookup that understands a trailing year.

    Returns ``(ok, results_or_error, year)``. With a year, the search runs on
    the bare title (TMDB/TVDB search often misses 'Dune 2021') and results from
    that year come first. If none match the year, the literal title is searched
    too, so 'Blade Runner 2049' still works.
    """
    term, year = split_trailing_year(title)
    ok, payload = lookup(term)
    if not ok:
        return False, payload, year
    results = payload if isinstance(payload, list) else []
    if year is None:
        return True, results, None
    if not any(item.get("year") == year for item in results):
        ok, payload = lookup(title)
        if ok and isinstance(payload, list) and payload:
            results = payload
        if not any(item.get("year") == year for item in results):
            return True, results, None
    return True, sorted(results, key=lambda item: item.get("year") != year), year


# --- Quality profiles / root folders ----------------------------------

def get_quality_profiles(service: str, refresh: bool = False) -> tuple[bool, Any]:
    """Return ``(True, [{id, name}, ...])`` or ``(False, error_string)``."""
    cached = _profile_cache.get(service)
    if not refresh and cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return True, cached[1]
    ok, payload = request(service, "GET", "/api/v3/qualityprofile")
    if not ok:
        return False, payload
    if not isinstance(payload, list):
        return False, f"Unexpected quality-profile response from {service.title()}."
    _profile_cache[service] = (time.monotonic(), payload)
    return True, payload


def get_root_folders(service: str, refresh: bool = False) -> tuple[bool, Any]:
    """Return ``(True, [{path, ...}, ...])`` or ``(False, error_string)``."""
    cached = _rootfolder_cache.get(service)
    if not refresh and cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return True, cached[1]
    ok, payload = request(service, "GET", "/api/v3/rootfolder")
    if not ok:
        return False, payload
    if not isinstance(payload, list):
        return False, f"Unexpected root-folder response from {service.title()}."
    _rootfolder_cache[service] = (time.monotonic(), payload)
    return True, payload


def list_profile_names(service: str) -> list[str]:
    ok, payload = get_quality_profiles(service)
    if not ok:
        return []
    return [p.get("name", "") for p in payload if p.get("name")]


def resolve_quality(service: str, phrase: str | None) -> tuple[int | None, str]:
    """Resolve a natural-language quality phrase to a profile ID.

    Returns ``(profile_id, profile_name)`` on success, or ``(None, detail)``
    where ``detail`` is a spoken-friendly explanation (unreachable service, or
    the list of available profile names) on failure.
    """
    phrase = (phrase or "").strip() or default_quality(service)

    ok, profiles = get_quality_profiles(service)
    if not ok:
        return None, str(profiles)

    names = [p.get("name", "") for p in profiles if p.get("name")]
    if not names:
        return None, f"{service.title()} has no quality profiles defined."

    # Step 1: phrase -> configured target profile name (if a map entry matches).
    mapping = _quality_map(service)
    target_name = phrase
    if mapping:
        map_hit = _best_match(phrase, [k for k, _ in mapping])
        if map_hit is not None:
            target_name = dict(mapping)[map_hit]

    # Step 2: target name -> actual profile (fuzzy against the live list).
    profile_name = _best_match(target_name, names)
    if profile_name is None:
        pretty = ", ".join(names)
        return None, (
            f"I couldn't match '{phrase}' to a {service.title()} quality "
            f"profile. Available profiles: {pretty}."
        )

    for p in profiles:
        if p.get("name") == profile_name:
            return int(p["id"]), profile_name
    return None, f"Quality profile '{profile_name}' vanished mid-lookup."


def resolve_root_folder(service: str, override: str | None = None) -> tuple[str | None, str]:
    """Return ``(path, path)`` for the root folder to use, or ``(None, detail)``.

    Uses ``override`` when given, else ``{SERVICE}_ROOT_FOLDER`` from ``.env``,
    else the first root folder the service reports.
    """
    ok, folders = get_root_folders(service)
    if not ok:
        return None, str(folders)

    paths = [f.get("path", "") for f in folders if f.get("path")]
    if not paths:
        return None, f"{service.title()} has no root folders configured."

    wanted = (override or os.getenv(f"{service.upper()}_ROOT_FOLDER", "")).strip()
    if wanted.startswith("#"):  # stray inline comment leaked in from .env
        wanted = ""
    if wanted:
        if wanted in paths:
            return wanted, wanted
        match = _best_match(wanted, paths)
        if match:
            return match, match
        return None, (
            f"Root folder '{wanted}' isn't one of {service.title()}'s: "
            f"{', '.join(paths)}."
        )

    return paths[0], paths[0]
