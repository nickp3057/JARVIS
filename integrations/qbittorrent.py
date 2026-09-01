"""Read-only qBittorrent WebUI API integration.

Provides current transfer speed, torrent progress, state, and ETA. Credentials
are read live from ``.env`` and authentication cookies are reused per process.
"""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

from jarvis_logger import get_logger

load_dotenv()
log = get_logger(__name__)

_TIMEOUT_SECONDS = 12
_session_cache: tuple[requests.Session, str, str, str] | None = None


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
        return None, f"Could not reach qBittorrent: {exc}"
    if response.status_code != 200 or response.text.strip().lower() != "ok.":
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
        return False, f"Could not reach qBittorrent: {exc}"
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
        }
        for item in payload
    ]


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
        lines.append(
            f"{item.get('name', 'Unknown')}: {progress}% — "
            f"{_bytes(item.get('dlspeed'))}/s — {_duration(item.get('eta'))} — "
            f"{item.get('state', 'unknown')}"
        )
    if len(payload) > 20:
        lines.append(f"Plus {len(payload) - 20} more.")
    return "\n".join(lines)
