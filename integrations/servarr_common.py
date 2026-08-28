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
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from jarvis_logger import get_logger

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

log = get_logger(__name__)

_TIMEOUT_SECONDS = 15

# Per-service cached (session, url, key) so a runtime key/URL change rebuilds it.
_sessions: dict[str, tuple[requests.Session, str, str]] = {}

# Per-service cache of the quality-profile and root-folder lists (rarely change
# within a run; call the resolver helpers with refresh=True to bust it).
_profile_cache: dict[str, list[dict[str, Any]]] = {}
_rootfolder_cache: dict[str, list[dict[str, Any]]] = {}


# --- Config ---------------------------------------------------------------

def _service_url(service: str) -> str:
    return os.getenv(f"{service.upper()}_URL", "").strip().rstrip("/")


def _service_key(service: str) -> str:
    return os.getenv(f"{service.upper()}_API_KEY", "").strip()


def is_configured(service: str) -> bool:
    """True if this service has both a URL and an API key set."""
    return bool(_service_url(service) and _service_key(service))


def default_quality(service: str) -> str:
    return os.getenv(f"{service.upper()}_DEFAULT_QUALITY", "1080p").strip() or "1080p"


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


def request(service: str, method: str, path: str, **kwargs: Any) -> tuple[bool, Any]:
    """Perform a REST call against a Servarr service.

    Returns ``(success, payload)`` where payload is the decoded JSON on success
    or a human-readable error string on failure.
    """
    if not is_configured(service):
        return False, f"{service.title()} is not configured yet."

    sess = _session(service)
    if sess is None:
        return False, f"{service.title()} is not configured yet."

    url = f"{_service_url(service)}{path}"
    try:
        response = sess.request(method, url, timeout=_TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        log.warning("%s request failed: %s %s -> %s", service, method, path, exc)
        return False, f"Could not reach {service.title()}: {exc}"

    if response.status_code in (200, 201, 202):
        try:
            return True, response.json()
        except ValueError:
            return True, response.text

    log.warning(
        "%s %s %s returned %s: %s",
        service, method, path, response.status_code, response.text[:500],
    )
    return False, f"{service.title()} error {response.status_code}: {response.text[:300]}"


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


# --- Fuzzy helpers ------------------------------------------------------

def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _best_match(needle: str, haystack: list[str]) -> str | None:
    """Return the closest entry in ``haystack`` to ``needle`` or None.

    Tries, in order: exact normalized equality, containment either direction,
    then difflib similarity with a 0.6 cutoff.
    """
    n = _normalize(needle)
    if not n or not haystack:
        return None

    norm_map = {_normalize(h): h for h in haystack}

    if n in norm_map:
        return norm_map[n]

    contains = [
        original for norm, original in norm_map.items()
        if norm and (norm in n or n in norm)
    ]
    if len(contains) == 1:
        return contains[0]
    if len(contains) > 1:
        # Prefer the longest overlap (most specific).
        return max(contains, key=lambda h: len(_normalize(h)))

    close = difflib.get_close_matches(n, list(norm_map.keys()), n=1, cutoff=0.6)
    if close:
        return norm_map[close[0]]
    return None


# --- Quality profiles / root folders ----------------------------------

def get_quality_profiles(service: str, refresh: bool = False) -> tuple[bool, Any]:
    """Return ``(True, [{id, name}, ...])`` or ``(False, error_string)``."""
    if not refresh and service in _profile_cache:
        return True, _profile_cache[service]
    ok, payload = request(service, "GET", "/api/v3/qualityprofile")
    if not ok:
        return False, payload
    if not isinstance(payload, list):
        return False, f"Unexpected quality-profile response from {service.title()}."
    _profile_cache[service] = payload
    return True, payload


def get_root_folders(service: str, refresh: bool = False) -> tuple[bool, Any]:
    """Return ``(True, [{path, ...}, ...])`` or ``(False, error_string)``."""
    if not refresh and service in _rootfolder_cache:
        return True, _rootfolder_cache[service]
    ok, payload = request(service, "GET", "/api/v3/rootfolder")
    if not ok:
        return False, payload
    if not isinstance(payload, list):
        return False, f"Unexpected root-folder response from {service.title()}."
    _rootfolder_cache[service] = payload
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
