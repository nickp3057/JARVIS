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


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().replace("&", "and").split())


def _episode_label(item: dict[str, Any]) -> str:
    if item.get("type") != "episode":
        return str(item.get("title") or "it")
    return (f"{item.get('grandparentTitle') or 'the show'} season {item.get('parentIndex')}, "
            f"episode {item.get('index')}, \"{item.get('title')}\"")


def next_up(title: str) -> tuple[bool, dict[str, Any] | str]:
    """What "play <title>" should start: a show's On Deck episode (else its
    first unwatched one), or the movie itself. Plex resumes partly watched
    items on its own. Returns ``{"key", "label", "server"}``."""
    ok, ident = _get("/identity")
    if not ok:
        return False, str(ident)
    server = ((ident or {}).get("MediaContainer") or {}).get("machineIdentifier")
    ok, payload = _get("/hubs/search", {"query": title, "limit": 10})
    if not ok:
        return False, str(payload)
    wanted = _norm(title)
    found = [m for hub in ((payload or {}).get("MediaContainer") or {}).get("Hub") or []
             for m in hub.get("Metadata") or [] if m.get("type") in {"movie", "show"}]
    if not found:
        return False, f"I can't find {title} on Plex, sir."
    match = next((m for m in found if _norm(m.get("title")) == wanted), found[0])
    if match.get("type") == "movie":
        return True, {"key": match["ratingKey"], "label": match.get("title"), "server": server}
    show_key = str(match.get("ratingKey"))
    ok, deck = _get("/library/onDeck")
    for item in (((deck or {}).get("MediaContainer") or {}).get("Metadata") or []) if ok else []:
        if str(item.get("grandparentRatingKey")) == show_key:
            return True, {"key": item["ratingKey"], "label": _episode_label(item), "server": server}
    ok, leaves = _get(f"/library/metadata/{show_key}/allLeaves")
    episodes = (((leaves or {}).get("MediaContainer") or {}).get("Metadata") or []) if ok else []
    if not episodes:
        return False, f"Plex has no episodes of {match.get('title')}, sir."
    pick = next((e for e in episodes if not e.get("viewCount")), episodes[0])
    return True, {"key": pick["ratingKey"], "label": _episode_label(pick), "server": server}
