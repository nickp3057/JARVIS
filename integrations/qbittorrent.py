"""Read-only qBittorrent WebUI API integration.

Provides current transfer speed, torrent progress, state, and ETA. Credentials
are read live from ``.env`` and authentication cookies are reused per process.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

import requests
from dotenv import load_dotenv

from jarvis_logger import get_logger

load_dotenv()
log = get_logger(__name__)

_TIMEOUT_SECONDS = (4, 12)
_session_cache: tuple[requests.Session, str, str, str] | None = None
# The download monitor and API requests run on different threads; without this
# both can log in at once after a SID expiry, and repeated logins count toward
# qBittorrent's failed-login IP ban.
_login_lock = threading.Lock()


_RELEASE_EXT_RE = re.compile(r"\.(mkv|mp4|avi|m4v|mov|ts|iso)$", re.IGNORECASE)
_RELEASE_TAG_RE = re.compile(r"\[[^\]]*\]")
_EPISODE_RE = re.compile(r"\bS(\d{1,2})(?:E(\d{1,3}))?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\(?\b(19\d{2}|20\d{2})\b\)?")
_JUNK_RE = re.compile(
    r"\b(2160p|1080p|720p|576p|480p|4k|uhd|hdr10?|dv|dolby|atmos|bluray|blu-ray|bdrip|brrip|remux|web-?dl|webrip|web|"
    r"hdtv|dvdrip|x26[45]|h\.?26[45]|hevc|avc|aac|ac3|ddp?5?|dts|10bit|proper|repack|extended|unrated|"
    r"remastered|complete|multi|subs?|imax|amzn|nf|hmax|dsnp)\b", re.IGNORECASE)


def friendly_name(release: str) -> str:
    """A torrent's release name as a person would say it.

    "Iron.Man.2008.1080p.BluRay.x264-SPARKS" -> "Iron Man";
    "The.Office.US.S02E03.720p.WEB-DL" -> "The Office US, season 2, episode 3";
    "Andor.S01.2160p" -> "Andor, season 1". Falls back to the raw name if
    cleaning would leave nothing."""
    raw = (release or "").strip()
    text = _RELEASE_TAG_RE.sub(" ", _RELEASE_EXT_RE.sub("", raw))
    text = re.sub(r"[._]+", " ", text)
    cut = len(text)
    episode = _EPISODE_RE.search(text)
    suffix = ""
    if episode:
        cut = min(cut, episode.start())
        suffix = f", season {int(episode.group(1))}"
        if episode.group(2):
            suffix += f", episode {int(episode.group(2))}"
    junk = _JUNK_RE.search(text)
    if junk and junk.start() > 0:
        cut = min(cut, junk.start())
    # The release year is the last year before the tags ("Blade Runner 2049 2017",
    # "1917 2019"); one at the very start is part of the title.
    years = [m for m in _YEAR_RE.finditer(text[:cut]) if m.start() > 0]
    if years:
        cut = years[-1].start()
    title = " ".join(text[:cut].replace("(", " ").replace(")", " ").split()).strip(" -")
    anime = re.search(r"\s+-\s+(\d{1,4})$", title)  # "Frieren - 12"
    if anime and not suffix:
        title, suffix = title[:anime.start()], f", episode {int(anime.group(1))}"
    return f"{title}{suffix}" if title else raw


def _unreachable(exc: Exception) -> str:
    log.warning("qBittorrent request failed: %s", exc)
    url, _, _ = _config()
    if isinstance(exc, requests.ReadTimeout):
        return "Could not reach qBittorrent: it accepted the connection but didn't answer in time."
    return f"Could not reach qBittorrent at {url}; the NAS, VPN container or WebUI may be down."


def _config() -> tuple[str, str, str]:
    return (
        os.getenv("QBITTORRENT_URL", "").strip().rstrip("/"),
        os.getenv("QBITTORRENT_USERNAME", "").strip(),
        os.getenv("QBITTORRENT_PASSWORD", "").strip(),
    )


def is_configured() -> bool:
    url, username, password = _config()
    return bool(url and username and password)


def _session() -> tuple[requests.Session | None, str]:
    with _login_lock:
        return _login()


def _login() -> tuple[requests.Session | None, str]:
    global _session_cache
    url, username, password = _config()
    if not (url and username and password):
        return None, "qBittorrent is not configured yet."
    if _session_cache and _session_cache[1:] == (url, username, password):
        return _session_cache[0], ""

    session = requests.Session()
    session.headers.update({"Referer": url, "Origin": url})
    try:
        response = session.post(
            f"{url}/api/v2/auth/login",
            data={"username": username, "password": password},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return None, _unreachable(exc)
    # qBittorrent 5.2 may return 204 No Content while still setting a valid SID.
    # Older versions return 200 with the literal body "Ok.".
    login_ok = (
        response.status_code == 204
        or (response.status_code == 200 and response.text.strip().lower() == "ok.")
    )
    if response.status_code == 403:
        return None, "qBittorrent has temporarily banned this machine's IP after failed logins; it lifts on its own (default 1 hour)."
    if response.status_code == 200 and response.text.strip().lower() == "fails.":
        return None, "qBittorrent login failed: wrong username or password; check QBITTORRENT_USERNAME/PASSWORD in .env."
    if not login_ok:
        return None, f"qBittorrent login failed ({response.status_code})."
    _session_cache = (session, url, username, password)
    return session, ""


def _request(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    _retry_auth: bool = True,
) -> tuple[bool, Any]:
    session, error = _session()
    if session is None:
        return False, error
    url, _, _ = _config()
    try:
        response = session.get(
            f"{url}/api/v2{path}",
            params=params,
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return False, _unreachable(exc)
    if response.status_code == 403:
        # The SID may have expired. Reauthenticate once so ordinary status
        # requests recover without making the user repeat themselves.
        global _session_cache
        _session_cache = None
        if _retry_auth:
            return _request(path, params=params, _retry_auth=False)
    if response.status_code != 200:
        return False, f"qBittorrent error {response.status_code}: {response.text[:250]}"
    try:
        return True, response.json()
    except ValueError:
        return True, response.text


def _post(
    path: str,
    *,
    data: dict[str, Any],
    _retry_auth: bool = True,
) -> tuple[bool, str]:
    session, error = _session()
    if session is None:
        return False, error
    url, _, _ = _config()
    try:
        response = session.post(
            f"{url}/api/v2{path}", data=data, timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return False, _unreachable(exc)
    if response.status_code == 403:
        global _session_cache
        _session_cache = None
        if _retry_auth:
            return _post(path, data=data, _retry_auth=False)
    if response.status_code not in {200, 204}:
        return False, f"qBittorrent error {response.status_code}: {response.text[:250]}"
    return True, "Command accepted by qBittorrent."


def test_connection() -> str:
    ok, version = _request("/app/version")
    if not ok:
        return str(version)
    return f"Connected to qBittorrent {str(version).strip()}."


def transfer_info() -> tuple[bool, Any]:
    return _request("/transfer/info")


def torrent_info(filter_name: str = "all") -> tuple[bool, Any]:
    return _request(
        "/torrents/info",
        params={"filter": filter_name, "sort": "added_on", "reverse": "true"},
    )


def torrent_by_hash(torrent_hash: str) -> tuple[bool, dict[str, Any] | None | str]:
    """One torrent's full record, ``(True, None)`` if qBittorrent lacks it."""
    ok, payload = _request("/torrents/info", params={"hashes": (torrent_hash or "").lower()})
    if not ok:
        return False, str(payload)
    if not isinstance(payload, list):
        return False, "Unexpected qBittorrent torrent response."
    return True, (payload[0] if payload else None)


def _bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit in {"B", "KB"} else f"{size:.1f} {unit}"
        size /= 1024
    return "unknown"


def _duration(seconds: Any) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "ETA unknown"
    if seconds < 0 or seconds >= 8_640_000:
        return "ETA unknown"
    if seconds < 60:
        return "under a minute"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}" + (f" {minutes} minutes" if minutes else "")
    days, hours = divmod(hours, 24)
    return f"{days} day{'s' if days != 1 else ''}" + (f" {hours} hours" if hours else "")


def _is_incomplete(torrent: dict[str, Any]) -> bool:
    return float(torrent.get("progress", 0) or 0) < 1.0


# Torrents a gluetun restart would actually interrupt mid-transfer. Stalled,
# queued and paused downloads are not moving data, so they don't count.
_MOVING_DL_STATES = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "allocating"}


def active_downloads() -> tuple[bool, list[str] | str]:
    """Names of incomplete torrents currently transferring, or an error."""
    ok, payload = torrent_info("all")
    if not ok:
        return False, str(payload)
    if not isinstance(payload, list):
        return False, "qBittorrent returned an unexpected torrent list."
    return True, [
        str(item.get("name", "Unknown download"))
        for item in payload
        if _is_incomplete(item)
        and (str(item.get("state", "")) in _MOVING_DL_STATES or int(item.get("dlspeed", 0) or 0) > 0)
    ]


def download_snapshot() -> tuple[bool, list[dict[str, Any]] | str]:
    """Return compact torrent records for runtime completion/error monitoring."""
    ok, payload = torrent_info("all")
    if not ok:
        return False, str(payload)
    if not isinstance(payload, list):
        return False, "Unexpected qBittorrent torrent response."
    return True, [
        {
            "hash": str(item.get("hash", "")),
            "name": str(item.get("name", "Unknown download")),
            "progress": float(item.get("progress", 0) or 0),
            "state": str(item.get("state", "unknown")),
            "eta": int(item.get("eta", -1) or -1),
            # Stall detection (media_doctor.no_progress_stall) needs these.
            "num_seeds": int(item.get("num_seeds", 0) or 0),
            "num_leechs": int(item.get("num_leechs", 0) or 0),
            "last_activity": float(item.get("last_activity", 0) or 0),
            "time_active": float(item.get("time_active", 0) or 0),
        }
        for item in payload
    ]


# States that count as "active or queued" for the companion dashboard.
_TRANSFER_STATES = {
    "downloading", "metaDL", "forcedDL", "allocating", "checkingDL",
    "stalledDL", "queuedDL", "queuedUP", "pausedDL", "pausedUP", "stoppedDL", "stoppedUP",
}


def list_transfers(scope: str = "active") -> tuple[bool, dict[str, Any] | str]:
    """Return structured transfer data for the companion dashboard.

    On success the payload is ``{"dl_speed", "up_speed", "torrents": [...]}``
    with per-torrent name, progress (0-1), state, dl_speed, up_speed, eta
    (seconds, -1 unknown), and size (bytes). ``scope="active"`` (default)
    keeps torrents that are downloading, queued, stalled, or currently
    transferring; ``scope="all"`` returns everything.
    """
    ok, payload = torrent_info("all")
    if not ok:
        return False, str(payload)
    if not isinstance(payload, list):
        return False, "Unexpected qBittorrent torrent response."

    torrents: list[dict[str, Any]] = []
    for item in payload:
        state = str(item.get("state", "unknown"))
        dl_speed = int(item.get("dlspeed", 0) or 0)
        up_speed = int(item.get("upspeed", 0) or 0)
        if scope != "all" and state not in _TRANSFER_STATES and dl_speed <= 0 and up_speed <= 0:
            continue
        torrents.append(
            {
                "hash": str(item.get("hash", "")),
                "name": str(item.get("name", "Unknown download")),
                "progress": float(item.get("progress", 0) or 0),
                "state": state,
                "dl_speed": dl_speed,
                "up_speed": up_speed,
                "eta": int(item.get("eta", -1) or -1),
                "size": int(item.get("size", 0) or 0),
            }
        )

    dl_rate = up_rate = 0
    transfer_ok, transfer = transfer_info()
    if transfer_ok and isinstance(transfer, dict):
        dl_rate = int(transfer.get("dl_info_speed", 0) or 0)
        up_rate = int(transfer.get("up_info_speed", 0) or 0)

    return True, {"dl_speed": dl_rate, "up_speed": up_rate, "torrents": torrents}


def download_status(scope: str = "downloading") -> str:
    """Return a spoken-friendly live download and transfer summary."""
    scope = (scope or "downloading").strip().lower()
    api_filter = scope if scope in {
        "all", "completed", "seeding", "stalled", "errored", "active", "inactive",
    } else "all"
    ok, payload = torrent_info(api_filter)
    if not ok:
        return str(payload)
    if not isinstance(payload, list):
        return "qBittorrent returned an unexpected torrent list."
    if scope in {"downloading", "incomplete"}:
        payload = [item for item in payload if _is_incomplete(item)]

    transfer_ok, transfer = transfer_info()
    if transfer_ok and isinstance(transfer, dict):
        header = (
            f"qBittorrent: {_bytes(transfer.get('dl_info_speed'))}/s down, "
            f"{_bytes(transfer.get('up_info_speed'))}/s up, "
            f"connection {transfer.get('connection_status', 'unknown')}."
        )
    else:
        header = "qBittorrent transfer totals are unavailable."

    if not payload:
        return f"{header} No {scope} torrents."
    lines = [header]
    for item in payload[:20]:
        progress = round(float(item.get("progress", 0) or 0) * 100, 1)
        # size is 0 until a magnet's metadata arrives.
        size = f" of {_bytes(item['size'])}" if isinstance(item.get("size"), (int, float)) and item["size"] > 0 else ""
        # The spoken title, not the release name: the model repeats whatever it
        # is shown ("Good Will Hunting", not "...x264 1080p Atmos-GROUP").
        lines.append(
            f"{friendly_name(str(item.get('name', 'Unknown')))}: {progress}%{size} — "
            f"{_bytes(item.get('dlspeed'))}/s — {_duration(item.get('eta'))} — "
            f"{item.get('state', 'unknown')}"
        )
    if len(payload) > 20:
        lines.append(f"Plus {len(payload) - 20} more.")
    return "\n".join(lines)


def torrent_inventory(limit: int = 20) -> str:
    """List exact torrent hashes for subsequent confirm-gated operations."""
    limit = max(1, min(50, int(limit or 20)))
    ok, payload = torrent_info("all")
    if not ok:
        return str(payload)
    if not isinstance(payload, list):
        return "qBittorrent returned an unexpected torrent list."
    if not payload:
        return "No torrents found."
    return "Torrents (use the exact hash for an operation):\n" + "\n".join(
        f"- {item.get('hash', '')}: {item.get('name', 'Unknown')} — {item.get('state', 'unknown')}"
        for item in payload[:limit]
    )


_TORRENT_ACTIONS = {
    # qBittorrent 5 renamed pause/resume to stop/start (the old paths 404).
    "pause": ("/torrents/stop", {}),
    "resume": ("/torrents/start", {}),
    "recheck": ("/torrents/recheck", {}),
    "priority_top": ("/torrents/topPrio", {}),
    "priority_bottom": ("/torrents/bottomPrio", {}),
    "priority_increase": ("/torrents/increasePrio", {}),
    "priority_decrease": ("/torrents/decreasePrio", {}),
}
_V4_PATHS = {"/torrents/stop": "/torrents/pause", "/torrents/start": "/torrents/resume"}
_TORRENT_HASH_RE = re.compile(r"^[a-f0-9]{40}$", re.IGNORECASE)


def manage_torrent(
    action: str,
    torrent_hash: str,
    *,
    confirmed: bool = False,
    delete_files: bool = False,
) -> str:
    """Perform one exact, live-verified, explicitly confirmed torrent operation."""
    action = (action or "").strip().lower()
    torrent_hash = (torrent_hash or "").strip().lower()
    if action not in {*_TORRENT_ACTIONS, "delete"}:
        return "Unsupported torrent action."
    if not _TORRENT_HASH_RE.fullmatch(torrent_hash):
        return "A full 40-character torrent hash is required. List torrents first."
    if not confirmed:
        return f"Confirmation required before {action.replace('_', ' ')} on torrent {torrent_hash}."

    ok, payload = torrent_info("all")
    if not ok:
        return str(payload)
    if not isinstance(payload, list):
        return "qBittorrent returned an unexpected torrent list."
    torrent = next(
        (item for item in payload if str(item.get("hash") or "").lower() == torrent_hash), None,
    )
    if torrent is None:
        return "That exact torrent hash is not in the current qBittorrent list."

    if action == "delete":
        path = "/torrents/delete"
        extra = {"deleteFiles": "true" if delete_files else "false"}
    else:
        path, extra = _TORRENT_ACTIONS[action]
    ok, message = _post(path, data={"hashes": torrent_hash, **extra})
    if not ok and " 404" in message and path in _V4_PATHS:  # qBittorrent 4.x
        ok, message = _post(_V4_PATHS[path], data={"hashes": torrent_hash, **extra})
    if not ok:
        return message
    file_note = " and its downloaded files" if action == "delete" and delete_files else ""
    return (
        f"{action.replace('_', ' ').title()} accepted for "
        f"{torrent.get('name', torrent_hash)}{file_note}."
    )
