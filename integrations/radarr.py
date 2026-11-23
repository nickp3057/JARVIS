"""Radarr integration: look movies up, add them, and change their quality.

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
from integrations import qbittorrent
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


def candidates(title: str) -> tuple[bool, list[dict[str, Any]] | str]:
    """Lookup results for ``title`` (year-aware), or ``(False, reason)``."""
    ok, payload, year = servarr.ranked_lookup(_lookup, title)
    if not ok:
        return False, str(payload)
    results = [m for m in payload if m.get("tmdbId")]
    if year is not None:
        same_year = [m for m in results if m.get("year") == year]
        if len(same_year) == 1:
            # 'Dune 2021' names exactly one title: offer only that one.
            results = same_year
    return True, results


def find_movie(title: str, session_id: str | None = None) -> str:
    """Search Radarr for ``title`` and report candidates without adding anything."""
    title = (title or "").strip()
    if not title:
        return "I need a movie title to look up, sir."
    if not servarr.is_configured(_SERVICE):
        return "Radarr isn't configured, sir."

    ok, results = candidates(title)
    if not ok:
        return str(results)
    media_selections.record(_SERVICE, session_id, results[:_MAX_CANDIDATES], "tmdbId")
    if not results:
        return f"No match on Radarr for '{title}', sir. Try the exact title or add a year."

    if len(results) == 1:
        movie = results[0]
        if _in_library(movie):
            return f"{_describe(movie)}. It's already in your library, sir — nothing to do."
        return (
            f"One match on Radarr: {_describe(movie)}. "
            f"If Nick asked to add it, add it now; no need to ask again."
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

    def post() -> str:
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

    if not media_selections.is_clear_pick(_SERVICE, session_id, tmdb_id):
        # The lookup offered several titles and this pick isn't the obvious
        # top one, so the model may have guessed which one Nick meant. Make
        # him say yes to this exact title.
        from brain import confirm_gate

        year = movie.get("year", "")
        return confirm_gate.act(
            session_id,
            f"add {movie.get('title', 'it')}{f' ({year})' if year else ''} to Radarr on the {profile_detail} profile",
            post,
        )
    return post()


_MAX_BATCH = 15


def _clear_match(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one result a plain title clearly means: the only result, or the
    top-ranked one when no other result shares its title (the same test
    ``media_selections.is_clear_pick`` applies to a single add)."""
    if not results:
        return None
    top = results[0]

    def norm(item: dict[str, Any]) -> str:
        return " ".join(str(item.get("title", "")).lower().split())

    if len(results) == 1 or sum(norm(m) == norm(top) for m in results[:_MAX_CANDIDATES]) == 1:
        return top
    return None


def add_movies(titles: Any, quality: str | None = None, confirmed: bool = False) -> str:
    """Look up and add several movies Nick named in one request.

    Each title is looked up on its own (year-aware, like ``find_movie``); a
    title whose top match is plain is added straight away, one already in the
    library is reported, and an ambiguous or missing one is listed for Nick
    to settle with find_movie/add_movie. Nick naming the titles is the
    confirmation, the same rule as a single add of a plain match.
    """
    if isinstance(titles, str):
        titles = titles.replace(";", "\n").split("\n")
    names: list[str] = []
    for item in titles if isinstance(titles, (list, tuple)) else []:
        name = " ".join(str(item).split()).strip()
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    if not names:
        return "Which movies, sir? Give me the titles."
    if len(names) > _MAX_BATCH:
        return f"That's {len(names)} titles, sir; I'll take {_MAX_BATCH} at a time."
    if not confirmed:
        return "I won't add anything until you confirm the titles, sir."
    if not servarr.is_configured(_SERVICE):
        return "Radarr isn't configured, sir."

    profile_id, profile_detail = servarr.resolve_quality(_SERVICE, quality)
    if profile_id is None:
        return profile_detail
    root_path, root_detail = servarr.resolve_root_folder(_SERVICE, None)
    if root_path is None:
        return root_detail

    added: list[str] = []
    have: list[str] = []
    problems: list[str] = []
    for name in names:
        ok, results = candidates(name)
        if not ok:
            problems.append(f"{name}: lookup failed ({results})")
            continue
        if not results:
            problems.append(f"{name}: no match on Radarr")
            continue
        movie = _clear_match(results)
        if movie is None:
            options = "; ".join(_describe(m) for m in results[:3])
            problems.append(f"{name}: several matches, not added ({options})")
            continue
        year = movie.get("year")
        label = f"{movie.get('title', name)} ({year})" if year else str(movie.get("title", name))
        if _in_library(movie):
            have.append(label)
            continue
        result = _post_movie(movie, profile_id, root_path)
        if result:
            problems.append(f"{label}: {result}")
        else:
            added.append(label)
    log.info("Batch movie add: %d added, %d already in, %d unresolved", len(added), len(have), len(problems))

    parts = []
    if added:
        parts.append(f"Added {len(added)} on the {profile_detail} profile, searches underway: " + ", ".join(added) + ".")
    if have:
        parts.append("Already in the library: " + ", ".join(have) + ".")
    if problems:
        parts.append("Not added:\n" + "\n".join(f"- {p}" for p in problems)
                     + "\nFor these, use find_movie and add_movie (ask Nick which one when several match).")
    return "\n".join(parts)


def _post_movie(movie: dict[str, Any], profile_id: int, root_path: str) -> str:
    """POST one looked-up movie to Radarr. "" on success, else the problem."""
    tmdb_id = movie.get("tmdbId")
    body = dict(movie)
    body.update({"qualityProfileId": profile_id, "rootFolderPath": root_path, "monitored": True,
                 "minimumAvailability": "released", "addOptions": {"searchForMovie": True}})
    outcome = servarr.call(_SERVICE, "POST", "/api/v3/movie", json=body)
    if outcome.ok:
        return ""
    if outcome.kind != "timeout":
        return str(outcome.payload)
    # The add may have landed before the answer timed out; never re-POST.
    ok, check = _lookup(f"tmdb:{tmdb_id}")
    if ok and isinstance(check, list) and any(_in_library(m) for m in check if m.get("tmdbId") == tmdb_id):
        return ""
    return f"{outcome.payload} I couldn't confirm it was added; check before retrying"


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


# --- Changing a movie's quality profile -------------------------------

def _allowed_quality_ids(profile: dict[str, Any]) -> set[int]:
    """Quality ids a profile accepts, flattening grouped items."""
    allowed: set[int] = set()
    for item in profile.get("items", []):
        if not item.get("allowed"):
            continue
        members = item.get("items") or [item]
        for member in members:
            quality = member.get("quality") or {}
            if quality.get("id") is not None:
                allowed.add(int(quality["id"]))
    return allowed


def _library_matches(title: str) -> tuple[bool, list[dict[str, Any]] | str]:
    ok, results = candidates(title)
    if not ok:
        return False, results
    return True, [m for m in results if m.get("id")]


def _source_torrent_hashes(movie_id: int, file_path: str) -> list[str]:
    """qBittorrent hashes of the download(s) that produced the current file.

    Prefers the import whose destination is this exact file; falls back to
    the most recent import, since the file on disk is whatever landed last.
    """
    ok, events = servarr.request(_SERVICE, "GET", "/api/v3/history/movie",
                                 params={"movieId": movie_id})
    if not ok or not isinstance(events, list):
        return []
    imports = [e for e in events if e.get("eventType") == "downloadFolderImported" and e.get("downloadId")]
    if not imports:
        return []
    exact = [e for e in imports if (e.get("data") or {}).get("importedPath") == file_path]
    chosen = exact or [max(imports, key=lambda e: str(e.get("date") or ""))]
    return sorted({str(e["downloadId"]).lower() for e in chosen})


def change_movie_quality(title: str, quality: str, session_id: str | None = None) -> str:
    """Move a movie in the library to another quality profile.

    Radarr never downgrades on its own: a 4K file outranks every 1080p
    release, so just switching profiles leaves the big file in place. When
    the current file isn't allowed by the new profile, this deletes it in
    Radarr, removes its source torrent and data in qBittorrent (imports are
    hardlinked, so the space stays used otherwise), and searches for a copy
    that fits. When the file still fits (an upgrade), it only switches and
    searches.
    """
    title = (title or "").strip()
    if not title:
        return "Which movie, sir?"
    if not (quality or "").strip():
        return "Which quality profile, sir?"
    if not servarr.is_configured(_SERVICE):
        return "Radarr isn't configured, sir."

    ok, matches = _library_matches(title)
    if not ok:
        return str(matches)
    if not matches:
        return f"'{title}' isn't in your Radarr library, sir."
    if len(matches) > 1:
        listing = "; ".join(_describe(m) for m in matches[:_MAX_CANDIDATES])
        return f"Several library movies match '{title}': {listing}. Which one? Say it with the year."

    profile_id, profile_name = servarr.resolve_quality(_SERVICE, quality)
    if profile_id is None:
        return profile_name
    ok, profiles = servarr.get_quality_profiles(_SERVICE)
    if not ok:
        return str(profiles)
    profile = next((p for p in profiles if p.get("id") == profile_id), {})

    ok, movie = servarr.request(_SERVICE, "GET", f"/api/v3/movie/{matches[0]['id']}")
    if not ok or not isinstance(movie, dict):
        return str(movie)
    name = f"{movie.get('title', title)} ({movie.get('year', '?')})"
    if movie.get("qualityProfileId") == profile_id:
        return f"{name} is already on the {profile_name} profile, sir."

    movie_file = movie.get("movieFile") if movie.get("hasFile") else None
    file_quality = ((movie_file or {}).get("quality") or {}).get("quality") or {}
    replace = bool(movie_file) and file_quality.get("id") not in _allowed_quality_ids(profile)

    def run() -> str:
        body = dict(movie)
        body["qualityProfileId"] = profile_id
        body.pop("movieFile", None)
        outcome = servarr.call(_SERVICE, "PUT", f"/api/v3/movie/{movie['id']}", json=body)
        if not outcome.ok:
            return f"I couldn't switch {name} to {profile_name}: {outcome.payload}"
        parts = [f"Switched {name} to {profile_name}"]

        if replace:
            hashes = _source_torrent_hashes(movie["id"], movie_file.get("path", ""))
            outcome = servarr.call(_SERVICE, "DELETE", f"/api/v3/moviefile/{movie_file['id']}")
            if not outcome.ok:
                return f"{parts[0]}, but deleting the {file_quality.get('name', 'old')} file failed: {outcome.payload}"
            parts.append(f"deleted the {file_quality.get('name', 'old')} file (Radarr's recycle bin holds it for a week)")
            if hashes and qbittorrent.is_configured():
                removed = []
                for torrent_hash in hashes:
                    reply = qbittorrent.manage_torrent("delete", torrent_hash, confirmed=True, delete_files=True)
                    if reply.startswith("Delete accepted"):
                        removed.append(torrent_hash)
                if removed:
                    parts.append("removed its torrent and data from qBittorrent")
            else:
                log.info("No seeding torrent found for %s", name)

        search = servarr.call(_SERVICE, "POST", "/api/v3/command",
                              json={"name": "MoviesSearch", "movieIds": [movie["id"]]})
        parts.append("searching for a new copy" if search.ok else f"but the search didn't start ({search.payload})")
        return ", ".join(parts) + ", sir."

    if replace:
        from brain import confirm_gate
        return confirm_gate.act(
            session_id,
            f"switch {name} to {profile_name} and delete its {file_quality.get('name', 'current')} file",
            run,
        )
    return run()
