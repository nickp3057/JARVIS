"""Sonarr integration: look series up and (after confirmation) add them.

Mirror of :mod:`integrations.radarr` for TV. ``find_series`` only searches;
``add_series`` is the only write, takes a TVDB id, and refuses unless
``confirmed`` is True. Monitor strategy defaults to ``future`` (new episodes
only) unless Nick asks for something else.
"""

from __future__ import annotations

import os
from typing import Any

from integrations import servarr_common as servarr
from integrations import media_selections
from jarvis_logger import get_logger

log = get_logger(__name__)

_SERVICE = "sonarr"
_MAX_CANDIDATES = 5

# Natural-language monitor phrasings -> Sonarr addOptions.monitor values.
_MONITOR_ALIASES: dict[str, str] = {
    "future": "future",
    "future episodes": "future",
    "new": "future",
    "new episodes": "future",
    "going forward": "future",
    "all": "all",
    "all episodes": "all",
    "everything": "all",
    "whole series": "all",
    "entire series": "all",
    "back catalogue": "all",
    "first season": "firstSeason",
    "season one": "firstSeason",
    "pilot": "pilot",
    "pilot only": "pilot",
    "latest season": "latestSeason",
    "last season": "lastSeason",
    "none": "none",
    "no episodes": "none",
    "don't monitor": "none",
    "do not monitor": "none",
}

_VALID_MONITOR = {
    "future", "all", "missing", "existing", "firstSeason", "lastSeason",
    "latestSeason", "pilot", "recent", "none",
}


def _default_monitor() -> str:
    val = os.getenv("SONARR_MONITOR_STRATEGY", "future").strip() or "future"
    return val if val in _VALID_MONITOR else "future"


def _normalize_monitor(value: str | None) -> str:
    if not value:
        return _default_monitor()
    v = value.strip()
    if v in _VALID_MONITOR:
        return v
    return _MONITOR_ALIASES.get(v.lower(), _default_monitor())


def _in_library(series: dict[str, Any]) -> bool:
    return bool(series.get("id"))


def _describe(series: dict[str, Any]) -> str:
    year = series.get("year") or "year unknown"
    tag = " — already in your library" if _in_library(series) else ""
    return f"{series.get('title', 'Unknown')} ({year}), TVDB {series.get('tvdbId')}{tag}"


def _lookup(term: str) -> tuple[bool, Any]:
    return servarr.request(
        _SERVICE, "GET", "/api/v3/series/lookup", params={"term": term}
    )


def find_series(title: str, session_id: str | None = None) -> str:
    """Search Sonarr for ``title`` and report candidates without adding anything."""
    title = (title or "").strip()
    if not title:
        return "I need a show title to look up, sir."
    if not servarr.is_configured(_SERVICE):
        return "Sonarr isn't configured, sir."

    ok, payload = _lookup(title)
    if not ok:
        return str(payload)

    results = [s for s in payload if s.get("tvdbId")] if isinstance(payload, list) else []
    media_selections.record(_SERVICE, session_id, results[:_MAX_CANDIDATES], "tvdbId")
    if not results:
        return f"No match on Sonarr for '{title}', sir. Try the exact title or add a year."

    if len(results) == 1:
        series = results[0]
        if _in_library(series):
            return f"{_describe(series)}. It's already in your library, sir — nothing to do."
        return (
            f"One match on Sonarr: {_describe(series)}. "
            f"Say the word and I'll add it with your configured defaults."
        )

    lines = [f"{i}. {_describe(s)}" for i, s in enumerate(results[:_MAX_CANDIDATES], 1)]
    more = "" if len(results) <= _MAX_CANDIDATES else f" (plus {len(results) - _MAX_CANDIDATES} more)"
    return (
        f"{len(results)} matches on Sonarr for '{title}'{more}, sir:\n"
        + "\n".join(lines)
        + "\nWhich one?"
    )


def add_series(
    tvdb_id: int | str,
    quality: str | None = None,
    root_folder: str | None = None,
    monitor: str | None = None,
    season_folder: bool = True,
    confirmed: bool = False,
    session_id: str | None = None,
) -> str:
    """Add the series with ``tvdb_id`` to Sonarr and trigger a search.

    Refuses unless ``confirmed`` is True. ``monitor`` accepts Sonarr strategy
    values or natural phrasings ('future episodes', 'whole series', 'pilot');
    defaults to ``SONARR_MONITOR_STRATEGY`` (``future``).
    """
    if not confirmed:
        return "I won't add anything until you confirm the exact title, sir."
    if not servarr.is_configured(_SERVICE):
        return "Sonarr isn't configured, sir."

    try:
        tvdb_id = int(tvdb_id)
    except (TypeError, ValueError):
        return "That doesn't look like a TVDB id, sir. Run a lookup first."

    allowed, reason = media_selections.validate(_SERVICE, session_id, tvdb_id)
    if not allowed:
        return reason

    ok, payload = _lookup(f"tvdb:{tvdb_id}")
    if not ok:
        return str(payload)
    matches = [s for s in payload if s.get("tvdbId") == tvdb_id] if isinstance(payload, list) else []
    if not matches:
        return f"I couldn't find a show with TVDB id {tvdb_id}, sir."
    series = matches[0]

    if _in_library(series):
        return f"{series.get('title', 'That show')} is already in your library, sir."

    profile_id, profile_detail = servarr.resolve_quality(_SERVICE, quality)
    if profile_id is None:
        return profile_detail

    root_path, root_detail = servarr.resolve_root_folder(_SERVICE, root_folder)
    if root_path is None:
        return root_detail

    monitor_strategy = _normalize_monitor(monitor)

    body = dict(series)
    body.update(
        {
            "qualityProfileId": profile_id,
            "rootFolderPath": root_path,
            "monitored": True,
            "seasonFolder": bool(season_folder),
            "addOptions": {
                "searchForMissingEpisodes": True,
                "monitor": monitor_strategy,
            },
        }
    )

    ok, result = servarr.request(_SERVICE, "POST", "/api/v3/series", json=body)
    if not ok:
        return str(result)

    media_selections.consume(_SERVICE, session_id)

    year = series.get("year", "")
    monitor_phrase = "future episodes" if monitor_strategy == "future" else f"'{monitor_strategy}'"
    return (
        f"Added {series.get('title', 'the show')}{f' ({year})' if year else ''} to Sonarr "
        f"on the {profile_detail} profile, monitoring {monitor_phrase}. Search is underway, sir."
    )
