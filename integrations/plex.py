"""Minimal Plex Media Server client: is a title on Plex yet, and rescan.

Only the media doctor uses this. ``PLEX_URL`` and ``PLEX_TOKEN`` are read
live from ``.env``; without a token every call reports "not configured" and
the doctor skips the Plex stage instead of failing.
"""
from __future__ import annotations

import os
from typing import Any

import requests

from jarvis_logger import get_logger

log = get_logger(__name__)

_TIMEOUT_SECONDS = (4, 10)


def _config() -> tuple[str, str]:
    return (
        os.getenv("PLEX_URL", "").strip().rstrip("/"),
        os.getenv("PLEX_TOKEN", "").strip(),
    )


def is_configured() -> bool:
    url, token = _config()
    return bool(url and token)


def _get(path: str, params: dict[str, Any] | None = None) -> tuple[bool, Any]:
    url, token = _config()
    if not (url and token):
        return False, "Plex is not configured (PLEX_URL / PLEX_TOKEN)."
    try:
        response = requests.get(
            f"{url}{path}",
            params=params,
            headers={"X-Plex-Token": token, "Accept": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        log.warning("Plex request failed: %s -> %s", path, exc)
        return False, f"I can't reach Plex at {url}."
    if response.status_code == 401:
        return False, "Plex rejected the token; check PLEX_TOKEN in .env."
    if response.status_code != 200:
        return False, f"Plex error {response.status_code}."
    if not response.content:
        return True, {}
    try:
        return True, response.json()
    except ValueError:
        return False, "Plex returned something other than JSON."


def find_title(title: str, year: int | None = None) -> tuple[bool, list[dict[str, Any]] | str]:
    """Movies/shows on Plex whose title matches; ``(False, reason)`` on error."""
    ok, payload = _get("/hubs/search", {"query": title, "limit": 10})
    if not ok:
        return False, str(payload)
    wanted = " ".join(title.lower().split())
    hits: list[dict[str, Any]] = []
    hubs = ((payload or {}).get("MediaContainer") or {}).get("Hub") or []
    for hub in hubs:
        for item in hub.get("Metadata") or []:
            if item.get("type") not in {"movie", "show"}:
                continue
            if " ".join(str(item.get("title", "")).lower().split()) != wanted:
                continue
            if year and item.get("year") and abs(int(item["year"]) - year) > 1:
                continue
            hits.append({"title": item.get("title"), "year": item.get("year"), "type": item.get("type")})
    return True, hits


def refresh_libraries() -> str:
    """Ask Plex to rescan every movie and TV library section."""
    ok, payload = _get("/library/sections")
    if not ok:
        return str(payload)
    sections = ((payload or {}).get("MediaContainer") or {}).get("Directory") or []
    keys = [s.get("key") for s in sections if s.get("type") in {"movie", "show"} and s.get("key")]
    if not keys:
        return "Plex has no movie or TV libraries to rescan."
    failed = [key for key in keys if not _get(f"/library/sections/{key}/refresh")[0]]
    if failed:
        return f"Plex rescan started for {len(keys) - len(failed)} of {len(keys)} libraries."
    return f"Plex is rescanning {len(keys)} librar{'y' if len(keys) == 1 else 'ies'}, sir."
