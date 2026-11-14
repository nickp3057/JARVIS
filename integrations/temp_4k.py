"""Temporary 4K copies of movies Nick already owns ("4K Harry Potter for tonight").

A second Radarr instance (service ``radarr4k``: ``RADARR4K_URL`` /
``RADARR4K_API_KEY``) downloads a 4K web release into its own root folder,
``/volume2/data/media/temp-4k-movies`` on the NAS. Plex reads that folder as part
of the Movies library and shows the 4K file as a second version of the same
movie. The 1080p copy in the main Radarr is never touched.

The clock starts when the 4K file lands: ``TEMP_4K_HOURS`` (default 48) later,
the runtime's temp-4K monitor deletes the movie and its file from the 4K Radarr
(``tick``). A request that hasn't downloaded after ``TEMP_4K_GIVE_UP_HOURS``
(default 72) is dropped. State lives in ``memory/temp_4k.json``.

Safety: a film that was already in the 4K Radarr before JARVIS asked is
``adopted``; when its time is up JARVIS only stops tracking it and never
deletes its files (an entry without the field is one JARVIS added). Before an
expiry delete the monitor asks Plex what's playing (``integrations/plex.py``)
and waits while that film is on; if Plex can't be asked it waits too, but
never more than ``_PLEX_DEFER_MAX_S`` past the expiry.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from integrations import servarr_common as servarr
from jarvis_logger import get_logger

log = get_logger(__name__)

SERVICE = "radarr4k"
MAIN = "radarr"
_STATE_FILE = Path(__file__).resolve().parent.parent / "memory" / "temp_4k.json"
_lock = threading.RLock()
# A film still playing (or a Plex that can't be asked) holds an expiry delete
# back, but never longer than this past the expiry: a paused-forever session
# or a dead Plex can't keep a 4K copy around for good.
_PLEX_DEFER_MAX_S = 12 * 3600


def _hours(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def keep_hours() -> float:
    return _hours("TEMP_4K_HOURS", 48)


def give_up_hours() -> float:
    return _hours("TEMP_4K_GIVE_UP_HOURS", 72)


def configured() -> bool:
    return servarr.is_configured(SERVICE)


# --- State ----------------------------------------------------------------------

def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def _save(entries: list[dict[str, Any]]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    tmp.replace(_STATE_FILE)


def entries() -> list[dict[str, Any]]:
    with _lock:
        return _load()


def _label(entry: dict[str, Any]) -> str:
    year = entry.get("year")
    return f"{entry.get('title', 'That film')}{f' ({year})' if year else ''}"


def _when(ts: float) -> str:
    moment = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    day = ("today" if moment.date() == today
           else "tomorrow" if (moment.date() - today).days == 1
           else moment.strftime("%A"))
    return f"{day} at {moment.strftime('%-I:%M %p')}"


# --- Finding the film in the main library ----------------------------------------

def _match_library(title: str, movies: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """The one owned movie ``title`` names, or (None, a reply explaining why not)."""
    name, year = servarr.split_trailing_year(title)
    wanted = servarr._normalize(name)
    if not wanted:
        return None, "Which film, sir?"
    pool = [m for m in movies if year is None or m.get("year") == year]

    def names(movie: dict[str, Any]) -> list[str]:
        alternates = [a.get("title", "") for a in movie.get("alternateTitles") or [] if isinstance(a, dict)]
        return [servarr._normalize(t) for t in [movie.get("title", ""), *alternates] if t]

    exact = [m for m in pool if wanted in names(m)]
    found = exact or [m for m in pool if any(wanted in n for n in names(m))]
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, (f"I can't find {title} in your movie library, sir. Temporary 4K is for films "
                      "you already have; add it first if you'd like it.")
    listing = "; ".join(f"{m.get('title')} ({m.get('year')})" for m in sorted(found, key=lambda m: m.get("year") or 0)[:6])
    return None, f"Several films match {title}: {listing}. Which one, sir?"


def _resolution(movie: dict[str, Any]) -> int:
    quality = ((movie.get("movieFile") or {}).get("quality") or {}).get("quality") or {}
    try:
        return int(quality.get("resolution") or 0)
    except (TypeError, ValueError):
        return 0


# --- Tool actions ---------------------------------------------------------------------

def request(title: str, hours: float | None = None) -> str:
    """Fetch a temporary 4K copy of an owned film, or extend one already here."""
    if not configured():
        return ("The 4K Radarr isn't set up yet, sir: RADARR4K_URL and RADARR4K_API_KEY "
                "are missing from .env.")
    keep = float(hours) if hours and float(hours) > 0 else keep_hours()

    ok, library = servarr.request(MAIN, "GET", "/api/v3/movie")
    if not ok:
        return str(library)
    movie, reason = _match_library(title or "", library if isinstance(library, list) else [])
    if movie is None:
        return reason
    tmdb_id = movie.get("tmdbId")

    with _lock:
        state = _load()
        existing = next((e for e in state if e.get("tmdb_id") == tmdb_id), None)
        if existing:
            existing["hours"] = keep
            if existing.get("ready_at"):
                existing["expires_at"] = max(existing.get("expires_at") or 0, time.time() + keep * 3600)
                _save(state)
                return (f"{_label(existing)} is already here in 4K, sir. I'll keep it until "
                        f"{_when(existing['expires_at'])}.")
            _save(state)
            return (f"{_label(existing)} is already on its way in 4K, sir; I'll keep it "
                    f"{_span(keep)} once it lands.")

    if _resolution(movie) >= 2160:
        return f"{_label(movie)} is already 4K in your library, sir. Nothing to fetch."

    added, detail, adopted = _add_to_4k(tmdb_id)
    if added is None:
        return detail
    entry = {
        "tmdb_id": tmdb_id,
        "title": movie.get("title"),
        "year": movie.get("year"),
        "radarr4k_id": added,
        "requested_at": time.time(),
        "hours": keep,
        "ready_at": None,
        "expires_at": None,
        # Already in the 4K Radarr before this request: not JARVIS's to delete.
        "adopted": adopted,
    }
    with _lock:
        state = [e for e in _load() if e.get("tmdb_id") != tmdb_id] + [entry]
        _save(state)
    return (f"Searching for a 4K copy of {_label(entry)}, sir. I'll tell you when it's ready, "
            f"and remove it {_span(keep)} after that so the 1080p copy is all that's left.")


def _span(hours: float) -> str:
    if hours % 24 == 0:
        days = int(hours // 24)
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours:g} hours"


def _add_to_4k(tmdb_id: Any) -> tuple[int | None, str, bool]:
    """Put the film in the 4K Radarr and start a search: (its 4K Radarr id,
    why not, adopted). ``adopted`` is True when the film was already there
    before this request, so expiry must never delete its files."""
    ok, found = servarr.request(SERVICE, "GET", "/api/v3/movie", params={"tmdbId": tmdb_id})
    if ok and isinstance(found, list) and found:
        movie_id = int(found[0]["id"])
        servarr.call(SERVICE, "POST", "/api/v3/command", json={"name": "MoviesSearch", "movieIds": [movie_id]})
        return movie_id, "", True

    ok, lookup = servarr.request(SERVICE, "GET", "/api/v3/movie/lookup", params={"term": f"tmdb:{tmdb_id}"})
    if not ok:
        return None, str(lookup), False
    matches = [m for m in lookup if m.get("tmdbId") == tmdb_id] if isinstance(lookup, list) else []
    if not matches:
        return None, "The 4K Radarr couldn't find that film by its TMDB id, sir.", False
    profile = os.getenv("RADARR4K_QUALITY", "").strip() or "Temp 4K"
    profile_id, profile_detail = servarr.resolve_quality(SERVICE, profile)
    if profile_id is None:
        return None, profile_detail, False
    root, root_detail = servarr.resolve_root_folder(SERVICE)
    if root is None:
        return None, root_detail, False
    body = dict(matches[0])
    body.update({"qualityProfileId": profile_id, "rootFolderPath": root, "monitored": True,
                 "minimumAvailability": "released", "addOptions": {"searchForMovie": True}})
    outcome = servarr.call(SERVICE, "POST", "/api/v3/movie", json=body)
    if outcome.ok and isinstance(outcome.payload, dict) and outcome.payload.get("id"):
        return int(outcome.payload["id"]), "", False
    # A timed-out add may still have landed: look before giving up (never re-POST).
    ok, found = servarr.request(SERVICE, "GET", "/api/v3/movie", params={"tmdbId": tmdb_id})
    if ok and isinstance(found, list) and found:
        return int(found[0]["id"]), "", False
    return None, str(outcome.payload), False


def status() -> str:
    """What's temporarily in 4K, downloading or due to go."""
    state = entries()
    if not state:
        return "No temporary 4K films at the moment, sir."
    progress = _queue_progress()
    lines = []
    for entry in state:
        if entry.get("ready_at"):
            lines.append(f"{_label(entry)}: ready in 4K, removed {_when(entry['expires_at'])}")
        else:
            pct = progress.get(entry.get("radarr4k_id"))
            lines.append(f"{_label(entry)}: " + (f"downloading, {pct:.0f}%" if pct is not None
                                                  else "searching for a 4K release"))
    return "Temporary 4K: " + "; ".join(lines) + "."


def _queue_progress() -> dict[int, float]:
    ok, queue = servarr.request(SERVICE, "GET", "/api/v3/queue", params={"pageSize": 50})
    records = queue.get("records", []) if ok and isinstance(queue, dict) else []
    out: dict[int, float] = {}
    for record in records:
        size, left = record.get("size") or 0, record.get("sizeleft") or 0
        if record.get("movieId") and size:
            out[int(record["movieId"])] = 100.0 * (size - left) / size
    return out


def remove(title: str) -> str:
    """Drop a temporary 4K copy now (the 1080p one stays)."""
    wanted = servarr._normalize(servarr.split_trailing_year(title or "")[0])
    state = entries()
    found = [e for e in state if wanted and wanted in servarr._normalize(e.get("title", ""))]
    if not found:
        return "There's no temporary 4K copy by that name, sir."
    if len(found) > 1:
        return "Which one, sir: " + "; ".join(_label(e) for e in found) + "?"
    done, detail = _delete(found[0])
    if done and found[0].get("adopted"):
        return (f"{_label(found[0])} was in the 4K library before I fetched it, sir, so I've stopped "
                "tracking it and left its files alone.")
    return f"Removed the 4K copy of {_label(found[0])}, sir; the regular one is untouched." if done else detail


def _delete(entry: dict[str, Any]) -> tuple[bool, str]:
    """Drop the entry; delete the 4K movie and its files only if JARVIS added it."""
    if not entry.get("adopted"):  # missing field (older entries): JARVIS added it
        movie_id = entry.get("radarr4k_id")
        outcome = servarr.call(SERVICE, "DELETE", f"/api/v3/movie/{movie_id}",
                               params={"deleteFiles": "true", "addImportExclusion": "false"})
        if not outcome.ok and outcome.kind != "not_found":
            return False, str(outcome.payload)
    with _lock:
        _save([e for e in _load() if e.get("tmdb_id") != entry.get("tmdb_id")])
    return True, ""


# --- Monitor --------------------------------------------------------------------------

Alert = Callable[..., None]


def tick(alert: Alert, now: float | None = None) -> None:
    """One monitor pass: announce finished downloads, remove expired copies.

    ``alert(kind, title, detail, severity=..., push=...)`` raises a runtime alert."""
    if not configured():
        return
    now = time.time() if now is None else now
    for entry in entries():
        try:
            _tick_entry(entry, alert, now)
        except Exception:  # noqa: BLE001 - one odd entry must not stop the rest
            log.exception("Temp 4K pass failed for %s", _label(entry))


def _same_film(entry: dict[str, Any], playing: dict[str, Any]) -> bool:
    if entry.get("tmdb_id") and f"tmdb://{entry['tmdb_id']}" in (playing.get("guids") or []):
        return True
    same_name = servarr._normalize(str(playing.get("title") or "")) == servarr._normalize(str(entry.get("title") or ""))
    return same_name and (not playing.get("year") or not entry.get("year") or playing["year"] == entry["year"])


def _plex_holds(entry: dict[str, Any], now: float) -> bool:
    """True while the expiry delete should wait: the film is playing on Plex,
    or Plex can't be asked. Never more than _PLEX_DEFER_MAX_S past expiry."""
    from integrations import plex

    if not plex.is_configured():
        return False
    if now - float(entry.get("expires_at") or now) > _PLEX_DEFER_MAX_S:
        return False
    try:
        ok, playing = plex.playing_movies()
    except Exception as exc:  # noqa: BLE001 - treat as "can't be asked"
        ok, playing = False, str(exc)
    if not ok:
        log.warning("Temp 4K: Plex can't say what's playing (%s); holding %s", playing, _label(entry))
        return True
    if any(_same_film(entry, item) for item in playing if isinstance(item, dict)):
        log.info("Temp 4K: %s is playing; removal waits", _label(entry))
        return True
    return False


def _tick_entry(entry: dict[str, Any], alert: Alert, now: float) -> None:
    if not entry.get("ready_at"):
        ok, movie = servarr.request(SERVICE, "GET", f"/api/v3/movie/{entry.get('radarr4k_id')}")
        if ok and isinstance(movie, dict) and movie.get("hasFile"):
            ready = None
            with _lock:
                state = _load()
                for item in state:
                    if item.get("tmdb_id") == entry.get("tmdb_id"):
                        item["ready_at"] = now
                        item["expires_at"] = now + float(item.get("hours") or keep_hours()) * 3600
                        ready = item
                if ready is not None:
                    _save(state)
            if ready is None:  # removed meanwhile (Nick dropped it): nothing to announce
                return
            alert("temp_4k_ready", f"{ready.get('title')} is ready in 4K",
                  f"{_label(ready)} is in Plex in 4K; pick the 4K version when you play it. "
                  f"It goes back to 1080p {_when(ready['expires_at'])}.", severity="info", push=True)
        elif now - float(entry.get("requested_at") or now) > give_up_hours() * 3600:
            done, detail = _delete(entry)
            if done:
                alert("temp_4k_failed", f"No 4K copy of {entry.get('title')}",
                      f"The 4K Radarr found no 4K release of {_label(entry)} in "
                      f"{give_up_hours():g} hours, so I've dropped the request.",
                      severity="warning", push=True)
            else:
                log.warning("Temp 4K give-up delete failed for %s: %s", _label(entry), detail)
        return
    if now < float(entry.get("expires_at") or 0):
        return
    if not entry.get("adopted") and _plex_holds(entry, now):
        return  # playing (or Plex unreachable): try again next pass
    done, detail = _delete(entry)
    if done:
        what = ("Stopped tracking" if entry.get("adopted") else "Removed") + f" the temporary 4K copy of {_label(entry)}."
        alert("temp_4k_removed", f"{entry.get('title')} back to 1080p", what, severity="info", push=False)
    else:
        log.warning("Temp 4K expiry delete failed for %s: %s", _label(entry), detail)
