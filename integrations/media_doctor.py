"""Media doctor: trace one request through the arr pipeline, name the stall.

Pipeline, in order::

    Radarr/Sonarr reachable -> in library -> monitored -> released ->
    searched/grabbed (indexers via Prowlarr) -> qBittorrent download ->
    import -> Plex

``diagnose(title)`` walks those stages for one movie or show and stops at the
first one that is stuck, returning a plain-English verdict plus the steps it
checked. ``diagnose()`` with no title sweeps every queue item instead.

Read-only by design. Each verdict may carry suggested fixes (re-search,
replace a dead release, rescan Plex, restart the arr stack); applying one goes
through ``brain.confirm_gate`` (or the arr restart gate) via
``propose_fix``, never directly from here.
"""
from __future__ import annotations

import difflib
import os
import threading
import time
from typing import Any, Callable

from integrations import nas_stack, plex, qbittorrent
from integrations import servarr_common as servarr
from jarvis_logger import get_logger

log = get_logger(__name__)

# A torrent with no progress for this long counts as stalled.
_DEFAULT_STALL_MINUTES = 30
_SERVICES = ("radarr", "sonarr")
_NOUN = {"radarr": "movie", "sonarr": "show"}

# Last diagnosis fixes per session, so "apply fix X" acts on exactly what the
# doctor reported (ids are only meaningful against that report).
_last_fixes: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_fixes_lock = threading.Lock()
_FIX_TTL_S = 30 * 60


def _stall_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("MEDIA_DOCTOR_STALL_MINUTES", _DEFAULT_STALL_MINUTES))) * 60
    except ValueError:
        return _DEFAULT_STALL_MINUTES * 60


def _step(stage: str, status: str, detail: str) -> dict[str, str]:
    return {"stage": stage, "status": status, "detail": detail}


def _fix(fix_id: str, description: str, **params: Any) -> dict[str, Any]:
    return {"id": fix_id, "label": description, "params": params}


def _report(steps: list[dict[str, str]], summary: str, *, stage: str, stalled: bool,
            item: dict[str, Any] | None = None, fixes: list[dict[str, Any]] | None = None,
            candidates: list[str] | None = None) -> dict[str, Any]:
    return {
        "stage": stage,
        "stalled": stalled,
        "summary": summary,
        "item": item,
        "steps": steps,
        "fixes": fixes or [],
        "candidates": candidates or [],
    }


# --- Library matching -----------------------------------------------------

def _match_library(service: str, title: str) -> tuple[bool, list[dict[str, Any]] | str]:
    """Library entries matching ``title`` (exact > contains > close)."""
    path = "/api/v3/movie" if service == "radarr" else "/api/v3/series"
    ok, payload = servarr.request(service, "GET", path)
    if not ok:
        return False, str(payload)
    library = payload if isinstance(payload, list) else []
    term, year = servarr.split_trailing_year(title)
    wanted = servarr._normalize(term)
    if not wanted:
        return True, []

    def ranked(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if year is None:
            return items
        same = [i for i in items if i.get("year") == year]
        return same or items

    exact = [i for i in library if servarr._normalize(i.get("title", "")) == wanted]
    if exact:
        return True, ranked(exact)
    contains = [i for i in library if wanted in servarr._normalize(i.get("title", ""))]
    if contains:
        return True, ranked(contains)[:5]
    names = {servarr._normalize(i.get("title", "")): i for i in library}
    close = difflib.get_close_matches(wanted, list(names), n=3, cutoff=0.8)
    return True, ranked([names[c] for c in close])


def _label(entry: dict[str, Any]) -> str:
    year = entry.get("year")
    return f"{entry.get('title', 'Unknown')}{f' ({year})' if year else ''}"


# --- Download-client inspection ------------------------------------------

def _torrent_verdict(torrent: dict[str, Any], now: float) -> tuple[bool, str]:
    """(stalled, plain-English description) for one qBittorrent record."""
    state = str(torrent.get("state", "unknown"))
    progress = round(float(torrent.get("progress", 0) or 0) * 100, 1)
    seeds = int(torrent.get("num_seeds", 0) or 0)
    peers = int(torrent.get("num_leechs", 0) or 0)
    idle_s = now - float(torrent.get("last_activity", now) or now)
    active_s = float(torrent.get("time_active", 0) or 0)
    idle = _plain_duration(idle_s)
    stall = _stall_seconds()
    if state in {"error", "missingFiles"}:
        return True, f"qBittorrent reports '{state}' at {progress}% (disk full, moved files or a permissions problem)."
    if state in {"pausedDL", "stoppedDL"}:
        return True, f"the torrent is paused in qBittorrent at {progress}%."
    if state in {"metaDL", "forcedMetaDL"} and active_s > stall:
        return True, f"qBittorrent has spent {_plain_duration(active_s)} fetching metadata with no peers; the release is probably dead."
    if state == "stalledDL" and idle_s > stall:
        return True, f"stalled at {progress}% with {seeds} seed(s) and {peers} peer(s); no data for {idle}."
    if state in {"queuedDL"}:
        return False, f"queued in qBittorrent behind other downloads at {progress}%."
    if state in {"checkingDL", "checkingResumeData", "allocating", "moving"}:
        return False, f"qBittorrent is {state} at {progress}%."
    if state == "stalledDL":
        return False, f"waiting for peers at {progress}% ({seeds} seed(s)); idle {idle}, not yet counted as stuck."
    speed = int(torrent.get("dlspeed", 0) or 0)
    eta = int(torrent.get("eta", 0) or 0)
    eta_text = _plain_duration(eta) if 0 < eta < 8640000 else "unknown"
    return False, f"downloading at {progress}%, {speed / 1_048_576:.1f} MB/s, ETA {eta_text}."


def no_progress_stall(torrent: dict[str, Any], now: float) -> str | None:
    """Description if a torrent is stuck for lack of peers/data, else None.

    Narrower than ``_torrent_verdict``: paused torrents (Nick's choice) and
    error states (alerted separately) don't count, so the download monitor
    can alert on exactly the "it silently stopped moving" case.
    """
    if str(torrent.get("state", "")) not in {"stalledDL", "metaDL", "forcedMetaDL"}:
        return None
    if not torrent.get("last_activity") and not torrent.get("time_active"):
        return None  # old snapshot without timing fields: can't judge
    stalled, description = _torrent_verdict(torrent, now)
    return description if stalled else None


def _plain_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds // 86400} days"


def _queue_messages(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in record.get("statusMessages") or []:
        parts.extend(str(m) for m in block.get("messages") or [] if m)
        if not block.get("messages") and block.get("title"):
            parts.append(str(block["title"]))
    if record.get("errorMessage"):
        parts.append(str(record["errorMessage"]))
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return "; ".join(seen)[:400]


def _inspect_queue_record(service: str, record: dict[str, Any], label: str,
                          steps: list[dict[str, str]], now: float) -> dict[str, Any]:
    """Classify one Radarr/Sonarr queue record (import or download stage)."""
    queue_id = record.get("id")
    replace = _fix("replace_release", f"remove '{record.get('title', 'this release')}' from {service.title()}, "
                   "blocklist it and search for another", service=service, queue_id=queue_id, label=label)
    tracked_state = str(record.get("trackedDownloadState") or "")
    tracked_status = str(record.get("trackedDownloadStatus") or "ok")
    messages = _queue_messages(record)

    if tracked_state in {"importPending", "importBlocked", "importFailed", "failedPending"} or (
            tracked_status in {"warning", "error"} and float(record.get("sizeleft", 1) or 0) == 0):
        why = messages or "no reason given"
        steps.append(_step("download", "ok", "Download finished."))
        steps.append(_step("import", "fail", f"{service.title()} can't import it: {why}"))
        return _report(steps, f"{label} downloaded but {service.title()} can't import it: {why}.",
                       stage="import", stalled=True, fixes=[replace])

    download_id = str(record.get("downloadId") or "")
    if str(record.get("protocol", "torrent")) != "torrent" or not download_id:
        steps.append(_step("download", "pending", f"In {service.title()}'s queue ({record.get('status', 'unknown')})."))
        return _report(steps, f"{label} is in {service.title()}'s queue: {record.get('status', 'status unknown')}.",
                       stage="download", stalled=False)

    ok, torrent = qbittorrent.torrent_by_hash(download_id)
    if not ok:
        steps.append(_step("download", "fail", str(torrent)))
        return _report(steps, f"{label} was sent to qBittorrent, but {torrent}",
                       stage="download", stalled=True,
                       fixes=[_fix("restart_arr_stack", "restart the arr stack (gluetun, qBittorrent and the arr apps)")])
    if torrent is None:
        steps.append(_step("download", "fail", "qBittorrent no longer has this torrent."))
        return _report(steps, f"{service.title()} thinks {label} is downloading, but qBittorrent doesn't have the torrent.",
                       stage="download", stalled=True, fixes=[replace])

    stalled, description = _torrent_verdict(torrent, now)
    if messages and tracked_status != "ok":
        description += f" {service.title()} also says: {messages}."
    steps.append(_step("download", "fail" if stalled else "pending", description[0].upper() + description[1:]))
    if stalled:
        return _report(steps, f"{label} is stuck downloading: {description}",
                       stage="download", stalled=True, fixes=[replace])
    return _report(steps, f"{label} is on its way: {description}", stage="download", stalled=False)


# --- Search / indexer stage --------------------------------------------

def _indexer_problem() -> str | None:
    base = os.getenv("PROWLARR_URL", "").strip().rstrip("/")
    key = os.getenv("PROWLARR_API_KEY", "").strip()
    if not (base and key):
        return None
    outages = nas_stack.indexer_outages(base, key)
    if outages is None:
        return "I couldn't read Prowlarr's indexer status."
    if outages:
        return "; ".join(sorted(outages.values()))
    return None


def _last_history_event(service: str, entry_id: int) -> dict[str, Any] | None:
    if service == "radarr":
        ok, payload = servarr.request(service, "GET", "/api/v3/history/movie", params={"movieId": entry_id})
    else:
        ok, payload = servarr.request(service, "GET", "/api/v3/history/series", params={"seriesId": entry_id})
    if not ok or not isinstance(payload, list) or not payload:
        return None
    return max(payload, key=lambda e: str(e.get("date", "")))


def _search_stage(service: str, entry: dict[str, Any], label: str,
                  steps: list[dict[str, str]]) -> dict[str, Any]:
    search = _fix("search", f"tell {service.title()} to search for {label} now",
                  service=service, entry_id=entry.get("id"), label=label)
    if service == "radarr" and entry.get("isAvailable") is False:
        when = entry.get("digitalRelease") or entry.get("physicalRelease") or entry.get("inCinemas") or "an unknown date"
        steps.append(_step("released", "pending", f"Not available yet (minimum availability not reached; release {when})."))
        return _report(steps, f"{label} isn't released for download yet ({when}). Radarr will grab it automatically once it is.",
                       stage="released", stalled=False)

    event = _last_history_event(service, int(entry.get("id") or 0))
    kind = str((event or {}).get("eventType", ""))
    if kind == "downloadFailed":
        reason = str((event.get("data") or {}).get("message") or "no reason given")
        steps.append(_step("download", "fail", f"The last download failed: {reason}"))
        return _report(steps, f"{label}'s last download failed ({reason}) and nothing new has been grabbed.",
                       stage="download", stalled=True, fixes=[search])
    if kind == "grabbed":
        steps.append(_step("download", "fail", "A release was grabbed but is no longer in the queue or the library."))
        return _report(steps, f"{label} was grabbed but vanished before import (removed from qBittorrent?).",
                       stage="download", stalled=True, fixes=[search])

    problem = _indexer_problem()
    if problem:
        steps.append(_step("indexer", "fail", problem))
        return _report(steps, f"Nothing has been grabbed for {label} because indexers are down: {problem}",
                       stage="indexer", stalled=True,
                       fixes=[_fix("restart_arr_stack", "restart the arr stack (gluetun, qBittorrent and the arr apps)"), search])
    steps.append(_step("search", "fail", "Indexers are up, but no release matching the quality profile has been found yet."))
    return _report(steps, f"{label} is wanted but no release matching its quality profile has turned up yet. "
                   "That's usually availability, not a fault.", stage="search", stalled=True, fixes=[search])


# --- Public API --------------------------------------------------------

def _service_up(service: str, steps: list[dict[str, str]]) -> dict[str, Any] | None:
    result = servarr.call(service, "GET", "/api/v3/system/status")
    if result.ok:
        steps.append(_step(service, "ok", f"{service.title()} is reachable."))
        return None
    steps.append(_step(service, "fail", str(result.payload)))
    fixes = [] if result.kind in {"auth", "unconfigured"} else [
        _fix("restart_arr_stack", "restart the arr stack (gluetun, qBittorrent and the arr apps)")]
    return _report(steps, str(result.payload), stage=service, stalled=True, fixes=fixes)


def diagnose(title: str | None = None, kind: str | None = None) -> dict[str, Any]:
    """Trace one title (or sweep every queue item when ``title`` is empty)."""
    title = (title or "").strip()
    if not title:
        return _sweep()
    kind = (kind or "").strip().lower()
    services = ["radarr"] if kind in {"movie", "radarr"} else ["sonarr"] if kind in {"show", "series", "tv", "sonarr"} else list(_SERVICES)
    services = [s for s in services if servarr.is_configured(s)]
    if not services:
        return _report([], "Radarr and Sonarr aren't configured.", stage="config", stalled=True)

    steps: list[dict[str, str]] = []
    found: list[tuple[str, dict[str, Any]]] = []
    for service in services:
        down = _service_up(service, steps)
        if down is not None:
            if len(services) == 1:
                return down
            continue
        ok, matches = _match_library(service, title)
        if not ok:
            steps.append(_step("library", "fail", str(matches)))
            continue
        found.extend((service, m) for m in matches)

    if any(s["status"] == "fail" for s in steps) and not found:
        failed = next(s for s in steps if s["status"] == "fail")
        return _report(steps, failed["detail"], stage=failed["stage"], stalled=True,
                       fixes=[_fix("restart_arr_stack", "restart the arr stack (gluetun, qBittorrent and the arr apps)")])
    if not found:
        steps.append(_step("library", "fail", f"'{title}' isn't in Radarr or Sonarr."))
        return _report(steps, f"'{title}' was never added (or was removed). Ask me to find it and I'll add it.",
                       stage="request", stalled=True)
    if len(found) > 1:
        names = [f"{_label(e)} [{_NOUN[s]}]" for s, e in found[:5]]
        return _report(steps, f"'{title}' matches several titles: {', '.join(names)}. Which one?",
                       stage="library", stalled=False, candidates=names)

    service, entry = found[0]
    return _trace(service, entry, steps)


def _trace(service: str, entry: dict[str, Any], steps: list[dict[str, str]]) -> dict[str, Any]:
    label = _label(entry)
    now = time.time()
    item = {"service": service, "id": entry.get("id"), "title": entry.get("title"), "year": entry.get("year")}
    steps.append(_step("library", "ok", f"{label} is in {service.title()}."))

    if not entry.get("monitored", True):
        steps.append(_step("monitored", "fail", "Not monitored, so it will never be searched."))
        return _report(steps, f"{label} is in {service.title()} but unmonitored, so nothing will ever download.",
                       stage="monitored", stalled=True, item=item,
                       fixes=[_fix("monitor_and_search", f"monitor {label} and search for it",
                                   service=service, entry_id=entry.get("id"), label=label)])

    has_file, file_detail = _has_files(service, entry)
    if not has_file:
        ok, queue = servarr.get_queue(service, page_size=200)
        id_key = "movieId" if service == "radarr" else "seriesId"
        records = [r for r in (queue.get("records", []) if ok and isinstance(queue, dict) else [])
                   if r.get(id_key) == entry.get("id")]
        if records:
            stuck = [r for r in records if str(r.get("trackedDownloadStatus") or "ok") != "ok"] or records
            report = _inspect_queue_record(service, stuck[0], label, steps, now)
            if len(records) > 1:
                report["summary"] += f" ({len(records)} items queued for it.)"
            report["item"] = item
            return report
        report = _search_stage(service, entry, label, steps)
        report["item"] = item
        return report

    steps.append(_step("import", "ok", file_detail))
    if not plex.is_configured():
        steps.append(_step("plex", "skip", "PLEX_TOKEN not set; Plex wasn't checked."))
        return _report(steps, f"{label} is downloaded and imported ({file_detail}). Plex wasn't checked.",
                       stage="imported", stalled=False, item=item)
    ok, hits = plex.find_title(str(entry.get("title", "")), entry.get("year"))
    if not ok:
        steps.append(_step("plex", "fail", str(hits)))
        return _report(steps, f"{label} is imported, but {hits}", stage="plex", stalled=True, item=item)
    if not hits:
        steps.append(_step("plex", "fail", "Imported, but Plex doesn't list it yet."))
        return _report(steps, f"{label} is imported but Plex hasn't picked it up yet.", stage="plex", stalled=True,
                       item=item, fixes=[_fix("refresh_plex", "rescan the Plex movie and TV libraries")])
    steps.append(_step("plex", "ok", "On Plex."))
    return _report(steps, f"{label} is on Plex and ready to watch.", stage="done", stalled=False, item=item)


def _has_files(service: str, entry: dict[str, Any]) -> tuple[bool, str]:
    if service == "radarr":
        if entry.get("hasFile"):
            path = (entry.get("movieFile") or {}).get("relativePath") or entry.get("path") or "library"
            return True, f"Imported to {path}."
        return False, ""
    stats = entry.get("statistics") or {}
    files = int(stats.get("episodeFileCount", 0) or 0)
    wanted = int(stats.get("episodeCount", 0) or 0)
    if files and files >= wanted:
        return True, f"All {files} monitored episode(s) imported."
    if files:
        # Partly there: the missing episodes are what's stuck.
        return False, ""
    return False, ""


def _sweep() -> dict[str, Any]:
    """Whole-pipeline check: services, indexers, every queue item."""
    steps: list[dict[str, str]] = []
    stuck: list[str] = []
    fixes: list[dict[str, Any]] = []
    now = time.time()
    for service in _SERVICES:
        if not servarr.is_configured(service):
            continue
        down = _service_up(service, steps)
        if down is not None:
            stuck.append(down["summary"])
            fixes.extend(f for f in down["fixes"] if f not in fixes)
            continue
        ok, queue = servarr.get_queue(service, page_size=200)
        for record in (queue.get("records", []) if ok and isinstance(queue, dict) else []):
            label = servarr._queue_title(service, record)
            report = _inspect_queue_record(service, record, label, [], now)
            if report["stalled"]:
                stuck.append(report["summary"])
                fixes.extend(f for f in report["fixes"] if f not in fixes)
    problem = _indexer_problem()
    if problem:
        steps.append(_step("indexer", "fail", problem))
        stuck.append(f"Indexers: {problem}")
    if not stuck:
        return _report(steps, "Nothing in the media pipeline is stuck, sir.", stage="done", stalled=False)
    summary = f"{len(stuck)} problem(s): " + " | ".join(stuck[:8])
    return _report(steps, summary, stage="sweep", stalled=True, fixes=fixes[:8])


# --- Rendering and fixes ---------------------------------------------------

def remember_fixes(session_id: str | None, report: dict[str, Any]) -> None:
    if not session_id:
        return
    with _fixes_lock:
        _last_fixes[session_id] = (time.monotonic(), {f"fix{i}": f for i, f in enumerate(report["fixes"], 1)})


def render(report: dict[str, Any]) -> str:
    lines = [report["summary"]]
    for step in report["steps"]:
        mark = {"ok": "✓", "fail": "✗", "pending": "…", "skip": "–"}.get(step["status"], "?")
        lines.append(f"{mark} {step['stage']}: {step['detail']}")
    for i, fix in enumerate(report["fixes"], 1):
        lines.append(f"Suggested fix{i}: {fix['label']}")
    if report["fixes"]:
        lines.append("Fixes need Nick's approval; call apply_media_fix with the fix id to propose one.")
    return "\n".join(lines)


def _run_fix(fix: dict[str, Any]) -> Callable[[], str]:
    params = fix["params"]
    fix_id = fix["id"]

    def run() -> str:
        if fix_id == "search":
            body = ({"name": "MoviesSearch", "movieIds": [params["entry_id"]]} if params["service"] == "radarr"
                    else {"name": "SeriesSearch", "seriesId": params["entry_id"]})
            ok, payload = servarr.request(params["service"], "POST", "/api/v3/command", json=body)
            return f"Search started for {params['label']}, sir." if ok else str(payload)
        if fix_id == "monitor_and_search":
            path = f"/api/v3/{'movie' if params['service'] == 'radarr' else 'series'}/{params['entry_id']}"
            ok, entry = servarr.request(params["service"], "GET", path)
            if not ok:
                return str(entry)
            entry["monitored"] = True
            ok, payload = servarr.request(params["service"], "PUT", path, json=entry)
            if not ok:
                return str(payload)
            return _run_fix(_fix("search", "", **params))().replace("Search started", "Monitoring and search started")
        if fix_id == "replace_release":
            ok, payload = servarr.request(
                params["service"], "DELETE", f"/api/v3/queue/{params['queue_id']}",
                params={"removeFromClient": "true", "blocklist": "true", "skipRedownload": "false"},
            )
            return (f"Removed and blocklisted that release; {params['service'].title()} is searching for another "
                    f"copy of {params['label']}, sir." if ok else str(payload))
        if fix_id == "refresh_plex":
            return plex.refresh_libraries()
        return f"Unknown fix '{fix_id}'."

    return run


def propose_fix(session_id: str | None, fix_id: str) -> str:
    """Hand a fix from this session's last diagnosis to the right confirm gate."""
    from brain import arr_stack, confirm_gate

    with _fixes_lock:
        stored = _last_fixes.get(session_id or "")
    if not stored or time.monotonic() - stored[0] > _FIX_TTL_S:
        return "Run diagnose_media first so I know exactly what to fix, sir."
    fix = stored[1].get((fix_id or "").strip().lower())
    if fix is None:
        return f"No fix '{fix_id}' in the last diagnosis; options were {', '.join(stored[1]) or 'none'}."
    if fix["id"] == "restart_arr_stack":
        return arr_stack.prepare_restart(session_id)
    return confirm_gate.propose(session_id, fix["label"], _run_fix(fix))
