"""Radarr integration: look movies up and (after confirmation) add them.

Thin wrapper over :mod:`integrations.servarr_common`. Two-step by design:
``find_movie`` only searches and reports candidates; ``add_movie`` is the only
call that writes, it takes a TMDB id (so a lookup must have happened first) and
refuses unless ``confirmed`` is True. The agent is instructed to surface
matches and get an explicit yes from Nick before ever calling ``add_movie``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from integrations import servarr_common as servarr
from integrations import media_selections
from jarvis_logger import get_logger

log = get_logger(__name__)

_SERVICE = "radarr"
_MAX_CANDIDATES = 5


def _in_library(movie: dict[str, Any]) -> bool:
    return bool(movie.get("id")) or bool(movie.get("hasFile"))


def _describe(movie: dict[str, Any]) -> str:
    year = movie.get("year") or "year unknown"
    tag = " — already in your library" if _in_library(movie) else ""
    return f"{movie.get('title', 'Unknown')} ({year}), TMDB {movie.get('tmdbId')}{tag}"


def _lookup(term: str) -> tuple[bool, Any]:
    return servarr.request(
        _SERVICE, "GET", "/api/v3/movie/lookup", params={"term": term}
    )


def find_movie(title: str, session_id: str | None = None) -> str:
    """Search Radarr for ``title`` and report candidates without adding anything."""
    title = (title or "").strip()
    if not title:
        return "I need a movie title to look up, sir."
    if not servarr.is_configured(_SERVICE):
        return "Radarr isn't configured, sir."

    ok, payload, year = servarr.ranked_lookup(_lookup, title)
    if not ok:
        return str(payload)

    results = [m for m in payload if m.get("tmdbId")]
    if year is not None:
        same_year = [m for m in results if m.get("year") == year]
        if len(same_year) == 1:
            # 'Dune 2021' names exactly one title: offer only that one.
            results = same_year
    media_selections.record(_SERVICE, session_id, results[:_MAX_CANDIDATES], "tmdbId")
    if not results:
        return f"No match on Radarr for '{title}', sir. Try the exact title or add a year."

    if len(results) == 1:
        movie = results[0]
        if _in_library(movie):
            return f"{_describe(movie)}. It's already in your library, sir — nothing to do."
        return (
            f"One match on Radarr: {_describe(movie)}. "
            f"Say the word and I'll add it — mention a quality profile if you want a specific one."
        )

    lines = [f"{i}. {_describe(m)}" for i, m in enumerate(results[:_MAX_CANDIDATES], 1)]
    more = "" if len(results) <= _MAX_CANDIDATES else f" (plus {len(results) - _MAX_CANDIDATES} more)"
    return (
        f"{len(results)} matches on Radarr for '{title}'{more}, sir:\n"
        + "\n".join(lines)
        + "\nWhich one?"
    )


def add_movie(
    tmdb_id: int | str,
    quality: str | None = None,
    root_folder: str | None = None,
    confirmed: bool = False,
    session_id: str | None = None,
) -> str:
    """Add the movie with ``tmdb_id`` to Radarr and trigger a search.

    Refuses unless ``confirmed`` is True. Resolves the natural-language
    ``quality`` phrase against the configured map and Radarr's live profile
    list; falls back to ``RADARR_DEFAULT_QUALITY`` when no phrase is given.
    """
    if not confirmed:
        return "I won't add anything until you confirm the exact title, sir."
    if not servarr.is_configured(_SERVICE):
        return "Radarr isn't configured, sir."

    try:
        tmdb_id = int(tmdb_id)
    except (TypeError, ValueError):
        return "That doesn't look like a TMDB id, sir. Run a lookup first."

    allowed, reason = media_selections.validate(_SERVICE, session_id, tmdb_id)
    if not allowed:
        return reason

    ok, payload = _lookup(f"tmdb:{tmdb_id}")
    if not ok:
        return str(payload)
    matches = [m for m in payload if m.get("tmdbId") == tmdb_id] if isinstance(payload, list) else []
    if not matches:
        return f"I couldn't find a movie with TMDB id {tmdb_id}, sir."
    movie = matches[0]

    if _in_library(movie):
        return f"{movie.get('title', 'That film')} is already in your library, sir."

    profile_id, profile_detail = servarr.resolve_quality(_SERVICE, quality)
    if profile_id is None:
        return profile_detail

    root_path, root_detail = servarr.resolve_root_folder(_SERVICE, root_folder)
    if root_path is None:
        return root_detail

    body = dict(movie)
    body.update(
        {
            "qualityProfileId": profile_id,
            "rootFolderPath": root_path,
            "monitored": True,
            "minimumAvailability": "released",
            "addOptions": {"searchForMovie": True},
        }
    )

    outcome = servarr.call(_SERVICE, "POST", "/api/v3/movie", json=body)
    if not outcome.ok:
        if outcome.kind != "timeout":
            return str(outcome.payload)
        # The add may have landed before the answer timed out. Never re-POST
        # (that could duplicate it); check the library instead.
        ok, check = _lookup(f"tmdb:{tmdb_id}")
        landed = ok and isinstance(check, list) and any(
            _in_library(entry) for entry in check if entry.get("tmdbId") == tmdb_id
        )
        if not landed:
            return (
                f"{outcome.payload} I couldn't confirm whether {movie.get('title', 'it')} was added; "
                "ask me to check before trying again, sir."
            )

    media_selections.consume(_SERVICE, session_id)

    year = movie.get("year", "")
    return (
        f"Added {movie.get('title', 'the movie')}{f' ({year})' if year else ''} to Radarr "
        f"on the {profile_detail} profile. Search is underway, sir."
    )


def history(limit: int = 10) -> str:
    """Return recent Radarr activity without changing the library."""
    limit = max(1, min(50, int(limit or 10)))
    ok, payload = servarr.request(_SERVICE, "GET", "/api/v3/history", params={
        "page": 1, "pageSize": limit, "sortKey": "date", "sortDirection": "descending",
    })
    if not ok:
        return str(payload)
    records = payload.get("records", []) if isinstance(payload, dict) else []
    if not records:
        return "No recent Radarr history."
    lines = []
    for item in records[:limit]:
        movie = item.get("movie") if isinstance(item.get("movie"), dict) else {}
        title = movie.get("title") or item.get("sourceTitle") or "Unknown movie"
        event = str(item.get("eventType") or "activity").replace("_", " ")
        lines.append(f"{title}: {event} — {item.get('date', 'date unknown')}")
    return "Recent Radarr history:\n" + "\n".join(lines)


def missing(limit: int = 10) -> str:
    """Return monitored movies currently missing files."""
    limit = max(1, min(50, int(limit or 10)))
    ok, payload = servarr.request(_SERVICE, "GET", "/api/v3/wanted/missing", params={
        "page": 1, "pageSize": limit, "sortKey": "releaseDate", "sortDirection": "descending",
    })
    if not ok:
        return str(payload)
    records = payload.get("records", []) if isinstance(payload, dict) else []
    if not records:
        return "No monitored movies are missing."
    return "Missing movies:\n" + "\n".join(
        f"{item.get('title', 'Unknown')} ({item.get('year', 'year unknown')})"
        for item in records[:limit]
    )


def upcoming(days: int = 14) -> str:
    """Return the bounded upcoming Radarr calendar."""
    days = max(1, min(90, int(days or 14)))
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=days)
    ok, payload = servarr.request(_SERVICE, "GET", "/api/v3/calendar", params={
        "start": start.date().isoformat(), "end": end.date().isoformat(), "unmonitored": "false",
    })
    if not ok:
        return str(payload)
    records = payload if isinstance(payload, list) else []
    if not records:
        return f"No monitored movie releases in the next {days} days."
    return "Upcoming movies:\n" + "\n".join(
        f"{item.get('title', 'Unknown')}: {item.get('digitalRelease') or item.get('physicalRelease') or item.get('inCinemas') or 'date unknown'}"
        for item in records[:30]
    )
