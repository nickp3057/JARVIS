"""Minimal Plex Media Server client: is a title on Plex yet, and rescan.

The media doctor uses this, and the temporary-4K monitor asks what's
playing before it deletes a 4K copy (``playing_movies``). ``PLEX_URL`` and ``PLEX_TOKEN`` are read
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


def playing_movies() -> tuple[bool, list[dict[str, Any]] | str]:
    """Movies playing (or paused) on Plex right now: title, year, guids
    ("tmdb://671", ...). ``(False, reason)`` when Plex can't be asked."""
    ok, payload = _get("/status/sessions", {"includeGuids": 1})
    if not ok:
        return False, str(payload)
    playing: list[dict[str, Any]] = []
    for item in ((payload or {}).get("MediaContainer") or {}).get("Metadata") or []:
        if not isinstance(item, dict) or item.get("type") != "movie":
            continue
        guids = [str(g.get("id", "")) for g in item.get("Guid") or [] if isinstance(g, dict)]
        playing.append({"title": item.get("title"), "year": item.get("year"), "guids": guids})
    return True, playing


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


def play_from_start(rating_key: str) -> tuple[bool, str]:
    """Clear an item's resume point (marks it watched, which Plex does by
    dropping viewOffset) so the next Watch starts at 0:00."""
    ok, detail = _get("/:/scrobble", {"key": str(rating_key), "identifier": "com.plexapp.plugins.library"})
    return ok, "" if ok else str(detail)


def server_id() -> str | None:
    """This Plex server's machineIdentifier (for the TV's plex:// deep link)."""
    ok, ident = _get("/identity")
    if not ok:
        return None
    return ((ident or {}).get("MediaContainer") or {}).get("machineIdentifier") or None


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


# --- Light sync (brain/light_tracks.py, brain/light_sync.py) -----------------


def _movie_record(item: dict[str, Any]) -> dict[str, Any]:
    """ratingKey, title, year, duration and a media Part's key: the
    smallest version's (a 1080p copy over a 4K one), since the light track
    is built at 64x27 and a 4K decode on Core's CPU takes several times longer."""
    def pixels(media: dict[str, Any]) -> int:
        try:
            return int(media.get("width") or 0) * int(media.get("height") or 0) or 1 << 40
        except (TypeError, ValueError):
            return 1 << 40
    part_key = ""
    selected_media, selected_part = {}, {}
    for media in sorted((m for m in item.get("Media") or [] if isinstance(m, dict)), key=pixels):
        for part in media.get("Part") or []:
            if part.get("key"):
                selected_media, selected_part = media, part
                part_key = str(part["key"])
                break
        if part_key:
            break
    return {"rating_key": str(item.get("ratingKey") or ""), "title": str(item.get("title") or ""),
            "year": item.get("year"), "duration_ms": int(item.get("duration") or 0), "part_key": part_key, "media_id": str(selected_media.get("id") or ""),
            "part_id": str(selected_part.get("id") or "")}


def _title_key(text: Any) -> str:
    """"The Hunger Games" and "hunger games" match; so do "&" and "and"."""
    words = _norm(text).replace(":", " ").replace("-", " ").split()
    if words and words[0] in ("the", "a", "an"):
        words = words[1:]
    return " ".join(words)


def movie(rating_key: str) -> tuple[bool, dict[str, Any] | str]:
    """One movie's metadata with its media Part key (to read the file over HTTP)."""
    ok, payload = _get(f"/library/metadata/{rating_key}")
    if not ok:
        return False, str(payload)
    items = ((payload or {}).get("MediaContainer") or {}).get("Metadata") or []
    if not items or items[0].get("type") != "movie":
        return False, "That isn't a movie on Plex."
    record = _movie_record(items[0])
    if not record["part_key"]:
        return False, f"Plex has no file for {record['title'] or 'that film'}."
    return True, record


def find_movie(title: str, year: int | None = None) -> tuple[bool, dict[str, Any] | str]:
    """The Plex movie ``title`` names, best match first: the exact title
    (ignoring a leading "The"), else one that starts with it, else Plex's
    first movie hit. ``(False, reason)`` when there is none."""
    ok, payload = _get("/hubs/search", {"query": title, "limit": 10})
    if not ok:
        return False, str(payload)
    found = [m for hub in ((payload or {}).get("MediaContainer") or {}).get("Hub") or []
             for m in hub.get("Metadata") or [] if m.get("type") == "movie"]
    if year:
        found = [m for m in found if not m.get("year") or abs(int(m["year"]) - int(year)) <= 1] or found
    if not found:
        return False, f"I can't find a film called {title} on Plex, sir."
    wanted = _title_key(title)
    match = (next((m for m in found if _title_key(m.get("title")) == wanted), None)
             or next((m for m in found if _title_key(m.get("title")).startswith(wanted)), None)
             or found[0])
    return movie(str(match.get("ratingKey")))


def search_movies(query: str) -> tuple[bool, list[dict[str, Any]] | str]:
    """Every Plex movie whose title contains ``query`` ("Hunger Games" -> the
    whole series), oldest first, each a ``movie`` record. One search, then
    one metadata read per film."""
    ok, payload = _get("/hubs/search", {"query": query, "limit": 20})
    if not ok:
        return False, str(payload)
    wanted = _title_key(query)
    hits = [m for hub in ((payload or {}).get("MediaContainer") or {}).get("Hub") or []
            for m in hub.get("Metadata") or []
            if m.get("type") == "movie" and wanted and wanted in _title_key(m.get("title"))]
    seen: set[str] = set()
    films: list[dict[str, Any]] = []
    for hit in sorted(hits, key=lambda m: (int(m.get("year") or 9999), str(m.get("title") or ""))):
        key = str(hit.get("ratingKey") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        found, record = movie(key)
        if found:
            films.append(record)
    if not films:
        return False, f"I can't find any films matching {query} on Plex, sir."
    return True, films


def part_url(part_key: str) -> str:
    """The media file's URL on Plex, token included. Never log it."""
    url, token = _config()
    return f"{url}{part_key}?X-Plex-Token={token}"


def sessions() -> tuple[bool, list[dict[str, Any]] | str]:
    """Everything playing on Plex right now with its player: ratingKey,
    title, type, viewOffset (ms), state (playing / paused / buffering) and
    the player's title, product and machineIdentifier."""
    ok, payload = _get("/status/sessions")
    if not ok:
        return False, str(payload)
    out: list[dict[str, Any]] = []
    for item in ((payload or {}).get("MediaContainer") or {}).get("Metadata") or []:
        if not isinstance(item, dict):
            continue
        player = item.get("Player") or {}
        media_items = item.get("Media") or []
        media = next((m for m in media_items if str(m.get("selected")).lower() in ("1", "true")), media_items[0] if media_items else {})
        parts = media.get("Part") or []
        part = next((p for p in parts if str(p.get("selected")).lower() in ("1", "true")), parts[0] if parts else {})
        streams = part.get("Stream") or []
        video = next((v for v in streams if str(v.get("streamType")) == "1"), {})
        transcode = item.get("TranscodeSession") or {}
        hdr = "dv" if str(video.get("DOVIPresent")).lower() in ("1", "true") else str(video.get("colorTrc") or "sdr").lower()
        mode = str(transcode.get("videoDecision") or part.get("decision") or "directplay").lower()
        fmt = "/".join(str(v).lower() for v in (media.get("videoResolution") or media.get("height") or "unknown",
                                              hdr, media.get("videoCodec") or video.get("codec") or "unknown", mode,
                                              media.get("audioCodec") or "unknown"))
        out.append({
            "media_id": str(media.get("id") or ""), "part_id": str(part.get("id") or ""),
            "part_key": str(part.get("key") or ""), "format_key": fmt,
            "rating_key": str(item.get("ratingKey") or ""), "title": str(item.get("title") or ""),
            "year": item.get("year"), "type": str(item.get("type") or ""),
            "view_offset_ms": int(item.get("viewOffset") or 0), "duration_ms": int(item.get("duration") or 0),
            "state": str(player.get("state") or ""),
            "player": {"title": str(player.get("title") or ""), "product": str(player.get("product") or ""),
                       "machine_id": str(player.get("machineIdentifier") or "")},
        })
    return True, out


# --- Movie picker (the iPad's movie mode, GET /api/plex/movies) -------------

_PICKER_SORTS = {"recent": "addedAt:desc", "title": "titleSort", "unwatched": "addedAt:desc", "year": "year:desc"}


def _movie_sections() -> tuple[bool, list[str] | str]:
    ok, payload = _get("/library/sections")
    if not ok:
        return False, str(payload)
    sections = ((payload or {}).get("MediaContainer") or {}).get("Directory") or []
    return True, [str(s["key"]) for s in sections if s.get("type") == "movie" and s.get("key")]


def _picker_card(item: dict[str, Any]) -> dict[str, Any]:
    duration = int(item.get("duration") or 0)
    offset = int(item.get("viewOffset") or 0)
    return {"rating_key": str(item.get("ratingKey") or ""), "title": str(item.get("title") or ""),
            "year": item.get("year"), "thumb": str(item.get("thumb") or ""),
            "duration_ms": duration, "view_offset_ms": offset if 0 < offset < duration else 0,
            "watched": int(item.get("viewCount") or 0) > 0, "added_at": int(item.get("addedAt") or 0),
            "summary": " ".join(str(item.get("summary") or "").split())[:400],
            "rating": item.get("audienceRating") or item.get("rating"),
            "content_rating": str(item.get("contentRating") or "")}


def library_movies(query: str = "", sort: str = "recent", limit: int = 300) -> tuple[bool, list[dict[str, Any]] | str]:
    """Every movie for the picker: poster path, year, resume point. ``query``
    filters by title (Plex's own substring match); ``sort`` is recent, title,
    year or unwatched (recent, minus anything watched). Films in more than one
    library (a 4K copy) appear once, from the first library."""
    ok, sections = _movie_sections()
    if not ok:
        return False, str(sections)
    params: dict[str, Any] = {"type": 1, "sort": _PICKER_SORTS.get(sort, "addedAt:desc"),
                              "X-Plex-Container-Start": 0, "X-Plex-Container-Size": max(1, min(limit, 1000))}
    if query.strip():
        params["title"] = query.strip()
    if sort == "unwatched":
        params["unwatched"] = 1
    seen: set[tuple[str, Any]] = set()
    films: list[dict[str, Any]] = []
    for section in sections:
        ok, payload = _get(f"/library/sections/{section}/all", params)
        if not ok:
            continue
        for item in ((payload or {}).get("MediaContainer") or {}).get("Metadata") or []:
            if not isinstance(item, dict) or item.get("type") != "movie":
                continue
            card = _picker_card(item)
            key = (_title_key(card["title"]), card["year"])
            if not card["rating_key"] or key in seen:
                continue
            seen.add(key)
            films.append(card)
    if len(sections) > 1:  # merged libraries: restore the requested order
        if sort == "title":
            films.sort(key=lambda f: _title_key(f["title"]))
        elif sort == "year":
            films.sort(key=lambda f: -(f["year"] or 0))
        else:
            films.sort(key=lambda f: -f["added_at"])
    return True, films[:limit]


def poster(path: str, width: int = 300) -> tuple[bool, bytes | str, str]:
    """A poster image through Plex's transcoder (so the token never leaves
    Core). Only library artwork paths are allowed."""
    url, token = _config()
    if not (url and token):
        return False, "Plex is not configured.", ""
    if not path.startswith("/library/metadata/") or ".." in path or "?" in path:
        return False, "Not a Plex artwork path.", ""
    width = max(80, min(int(width), 800))
    try:
        response = requests.get(f"{url}/photo/:/transcode",
                                params={"url": path, "width": width, "height": int(width * 1.5), "minSize": 1},
                                headers={"X-Plex-Token": token}, timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return False, f"Plex unreachable: {exc.__class__.__name__}", ""
    if response.status_code != 200:
        return False, f"Plex error {response.status_code}.", ""
    return True, response.content, response.headers.get("Content-Type", "image/jpeg")


# --- TV shows in the picker ----------------------------------------------------

def _show_sections() -> tuple[bool, list[str] | str]:
    ok, payload = _get("/library/sections")
    if not ok:
        return False, str(payload)
    sections = ((payload or {}).get("MediaContainer") or {}).get("Directory") or []
    return True, [str(s["key"]) for s in sections if s.get("type") == "show" and s.get("key")]


def library_shows(query: str = "", sort: str = "recent", limit: int = 300) -> tuple[bool, list[dict[str, Any]] | str]:
    """Every show for the picker: poster, year, episode counts. ``sort`` as
    for movies; "unwatched" keeps shows with episodes left to watch."""
    ok, sections = _show_sections()
    if not ok:
        return False, str(sections)
    order = {"recent": "lastViewedAt:desc", "title": "titleSort", "year": "year:desc", "unwatched": "addedAt:desc"}
    params: dict[str, Any] = {"type": 2, "sort": order.get(sort, "addedAt:desc"),
                              "X-Plex-Container-Start": 0, "X-Plex-Container-Size": max(1, min(limit, 1000))}
    if query.strip():
        params["title"] = query.strip()
    shows: list[dict[str, Any]] = []
    for section in sections:
        ok, payload = _get(f"/library/sections/{section}/all", params)
        if not ok:
            continue
        for item in ((payload or {}).get("MediaContainer") or {}).get("Metadata") or []:
            if not isinstance(item, dict) or item.get("type") != "show" or not item.get("ratingKey"):
                continue
            total, seen = int(item.get("leafCount") or 0), int(item.get("viewedLeafCount") or 0)
            if sort == "unwatched" and total and seen >= total:
                continue
            shows.append({"rating_key": str(item["ratingKey"]), "title": str(item.get("title") or ""),
                          "year": item.get("year"), "thumb": str(item.get("thumb") or ""),
                          "episodes": total, "watched_episodes": seen,
                          "seasons": int(item.get("childCount") or 0),
                          "summary": " ".join(str(item.get("summary") or "").split())[:400],
                          "content_rating": str(item.get("contentRating") or ""),
                          "last_viewed_at": int(item.get("lastViewedAt") or 0)})
    if len(sections) > 1:
        if sort == "title":
            shows.sort(key=lambda s: _title_key(s["title"]))
        elif sort == "recent":
            shows.sort(key=lambda s: -s["last_viewed_at"])
    return True, shows[:limit]


def _episode_card(item: dict[str, Any]) -> dict[str, Any]:
    duration = int(item.get("duration") or 0)
    offset = int(item.get("viewOffset") or 0)
    return {"rating_key": str(item.get("ratingKey") or ""), "title": str(item.get("title") or ""),
            "season": int(item.get("parentIndex") or 0), "episode": int(item.get("index") or 0),
            "thumb": str(item.get("thumb") or ""), "duration_ms": duration,
            "view_offset_ms": offset if 0 < offset < duration else 0,
            "watched": int(item.get("viewCount") or 0) > 0,
            "summary": " ".join(str(item.get("summary") or "").split())[:300]}


def show_episodes(show_key: str) -> tuple[bool, dict[str, Any] | str]:
    """A show's episodes by season, plus the one "Continue" would play
    (On Deck, else the first unwatched, else the first)."""
    if not str(show_key).isdigit():
        return False, "Not a Plex show."
    ok, leaves = _get(f"/library/metadata/{show_key}/allLeaves")
    if not ok:
        return False, str(leaves)
    episodes = [_episode_card(e) for e in ((leaves or {}).get("MediaContainer") or {}).get("Metadata") or []
                if isinstance(e, dict) and e.get("type") == "episode"]
    if not episodes:
        return False, "Plex has no episodes of that show."
    seasons: dict[int, list[dict[str, Any]]] = {}
    for episode in episodes:
        seasons.setdefault(episode["season"], []).append(episode)
    next_key = None
    ok, deck = _get("/library/onDeck")
    for item in (((deck or {}).get("MediaContainer") or {}).get("Metadata") or []) if ok else []:
        if str(item.get("grandparentRatingKey")) == str(show_key):
            next_key = str(item.get("ratingKey"))
            break
    if next_key is None:
        # In story order, and not a Special (season 0) unless that's all there is.
        ordered = sorted(episodes, key=lambda e: (e["season"], e["episode"]))
        regular = [e for e in ordered if e["season"] > 0] or ordered
        next_key = next((e["rating_key"] for e in regular if not e["watched"]), regular[0]["rating_key"])
    return True, {"next_up": next_key,
                  "seasons": [{"season": number, "episodes": sorted(items, key=lambda e: e["episode"])}
                              for number, items in sorted(seasons.items())]}


def playable(rating_key: str) -> tuple[bool, str]:
    """A movie or episode Plex can play: (True, label) or (False, reason)."""
    ok, payload = _get(f"/library/metadata/{rating_key}")
    if not ok:
        return False, str(payload)
    items = ((payload or {}).get("MediaContainer") or {}).get("Metadata") or []
    if not items or items[0].get("type") not in {"movie", "episode"}:
        return False, "That isn't a movie or an episode on Plex."
    item = items[0]
    return True, _episode_label(item) if item.get("type") == "episode" else str(item.get("title") or "it")
