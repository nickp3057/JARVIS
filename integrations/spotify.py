"""Spotify integration for the JARVIS assistant.

Wraps Spotipy with one-time OAuth that caches a refresh token to disk
(``.spotify_cache``), so after the first browser auth, every subsequent
launch is silent.

Returns plain English strings so they can be spoken directly.

NOTE: Spotify Premium is required for playback control via the Web API.
The Web API does not stream audio itself - it controls whichever Spotify
app is currently active (phone, computer, speaker, etc.). If
``SPOTIFY_PREFERRED_DEVICE`` is set in the environment, we'll prefer a
device whose name contains that string (case-insensitive). Otherwise we
fall back to whichever device is currently active, or the first available.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from jarvis_logger import get_logger

# Load .env from project root so this module works no matter the cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

log = get_logger(__name__)

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
PREFERRED_DEVICE = os.getenv("SPOTIFY_PREFERRED_DEVICE", "").strip()
CACHE_PATH = str(_PROJECT_ROOT / ".spotify_cache")

_SCOPES = " ".join(
    [
        "user-modify-playback-state",
        "user-read-playback-state",
        "user-read-currently-playing",
    ]
)

_client: Any | None = None
_client_init_attempted = False


# --- Client setup -----------------------------------------------------------

def _is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _get_client() -> Any | None:
    """Return a memoized Spotipy client, or None if anything fails."""
    global _client, _client_init_attempted

    if _client is not None:
        return _client
    if _client_init_attempted:
        return None

    _client_init_attempted = True

    if not _is_configured():
        log.warning("Spotify not configured (missing SPOTIFY_CLIENT_ID/SECRET).")
        return None

    try:
        import spotipy  # type: ignore
        from spotipy.oauth2 import SpotifyOAuth  # type: ignore
    except ImportError:
        log.warning("spotipy not installed (run: pip install -r requirements.txt).")
        return None

    try:
        auth_manager = SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope=_SCOPES,
            cache_path=CACHE_PATH,
            open_browser=True,
        )
        _client = spotipy.Spotify(auth_manager=auth_manager)
        _client.current_user()
        log.info("Spotify client ready.")
        return _client
    except Exception as exc:  # noqa: BLE001
        log.warning("Spotify client init failed: %s", exc)
        _client = None
        return None


# --- Device selection -------------------------------------------------------

def _list_devices_raw(sp: Any) -> list[dict[str, Any]]:
    try:
        return sp.devices().get("devices", []) or []
    except Exception:  # noqa: BLE001
        log.exception("Failed to list Spotify devices")
        return []


def _match_device(devices: list[dict[str, Any]], name_substr: str) -> dict[str, Any] | None:
    if not name_substr:
        return None
    needle = name_substr.lower()
    for d in devices:
        if needle in (d.get("name", "") or "").lower():
            return d
    return None


def _pick_target_device(sp: Any) -> dict[str, Any] | None:
    """Return the best device to play on, in priority order."""
    devices = _list_devices_raw(sp)
    if not devices:
        return None

    if PREFERRED_DEVICE:
        match = _match_device(devices, PREFERRED_DEVICE)
        if match:
            return match
        log.info(
            "Preferred device '%s' not currently available; falling back.",
            PREFERRED_DEVICE,
        )

    for d in devices:
        if d.get("is_active"):
            return d

    return devices[0]


def _format_track(track: dict[str, Any]) -> str:
    name = track.get("name", "Unknown")
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
    return f"{name} by {artists}" if artists else name


def _no_device_message() -> str:
    return (
        "No Spotify device is available, sir. Open the Spotify app on your "
        "phone or computer first."
    )


# --- Public API (each returns a spoken-friendly string) ---------------------

def is_available() -> bool:
    return _get_client() is not None


def play_music(query: str) -> str:
    """Search Spotify for ``query`` and start playback on the chosen device."""
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."

    query = (query or "").strip()
    if not query:
        return "I need something to play, sir."

    target = _pick_target_device(sp)
    if target is None:
        return _no_device_message()
    device_id = target.get("id")

    try:
        track_results = (
            sp.search(q=query, type="track", limit=1)
            .get("tracks", {})
            .get("items", [])
        )
        if track_results:
            track = track_results[0]
            sp.start_playback(device_id=device_id, uris=[track["uri"]])
            return f"Playing {_format_track(track)} on {target.get('name', 'your device')}."

        playlist_results = (
            sp.search(q=query, type="playlist", limit=1)
            .get("playlists", {})
            .get("items", [])
        )
        if playlist_results:
            playlist = playlist_results[0]
            sp.start_playback(device_id=device_id, context_uri=playlist["uri"])
            return (
                f"Playing the playlist '{playlist.get('name', query)}' on "
                f"{target.get('name', 'your device')}."
            )

        return f"I couldn't find anything for '{query}' on Spotify."
    except Exception as exc:  # noqa: BLE001
        log.exception("play_music failed")
        return f"Spotify error: {exc}"


def pause_music() -> str:
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."
    try:
        sp.pause_playback()
        return "Paused."
    except Exception as exc:  # noqa: BLE001
        log.exception("pause_music failed")
        return f"Spotify error: {exc}"


def resume_music() -> str:
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."
    target = _pick_target_device(sp)
    if target is None:
        return _no_device_message()
    try:
        sp.start_playback(device_id=target.get("id"))
        return "Playing."
    except Exception as exc:  # noqa: BLE001
        log.exception("resume_music failed")
        return f"Spotify error: {exc}"


def skip_track() -> str:
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."
    try:
        sp.next_track()
        return "Skipped."
    except Exception as exc:  # noqa: BLE001
        log.exception("skip_track failed")
        return f"Spotify error: {exc}"


def previous_track() -> str:
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."
    try:
        sp.previous_track()
        return "Going back."
    except Exception as exc:  # noqa: BLE001
        log.exception("previous_track failed")
        return f"Spotify error: {exc}"


def current_track() -> str:
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."
    try:
        playing = sp.current_playback()
        if not playing or not playing.get("item"):
            return "Nothing is playing, sir."
        return f"{_format_track(playing['item'])}."
    except Exception as exc:  # noqa: BLE001
        log.exception("current_track failed")
        return f"Spotify error: {exc}"


def current_track_structured() -> dict | None:
    """Return structured info about the current track, or None if nothing is playing."""
    sp = _get_client()
    if sp is None:
        return None
    try:
        playing = sp.current_playback()
        if not playing or not playing.get("item"):
            return None
        item = playing["item"]
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        album = item.get("album", {}).get("name", "")
        album_art = None
        images = item.get("album", {}).get("images", [])
        if images:
            album_art = images[0].get("url")
        return {
            "track": item.get("name", "Unknown"),
            "artist": artists,
            "album": album,
            "album_art_url": album_art,
            "is_playing": playing.get("is_playing", False),
        }
    except Exception:  # noqa: BLE001
        return None


def set_volume(percent: int) -> str:
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."
    try:
        clamped = max(0, min(100, int(percent)))
        sp.volume(clamped)
        return f"Volume {clamped}."
    except Exception as exc:  # noqa: BLE001
        log.exception("set_volume failed")
        return f"Spotify error: {exc}"


def list_speakers() -> str:
    """Return a spoken summary of available Spotify Connect devices."""
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."

    devices = _list_devices_raw(sp)
    if not devices:
        return "No Spotify devices are visible right now, sir."

    parts: list[str] = []
    for d in devices:
        name = d.get("name", "Unknown")
        type_ = d.get("type", "")
        active = " (active)" if d.get("is_active") else ""
        label = f"{name}{active}"
        if type_:
            label += f", a {type_.lower()}"
        parts.append(label)

    return "Available Spotify devices: " + "; ".join(parts) + "."


def transfer_playback(device_name: str) -> str:
    """Move active playback to the device whose name contains ``device_name``."""
    sp = _get_client()
    if sp is None:
        return "Spotify isn't configured, sir."

    device_name = (device_name or "").strip()
    if not device_name:
        return "Which device should I move playback to, sir?"

    devices = _list_devices_raw(sp)
    if not devices:
        return _no_device_message()

    target = _match_device(devices, device_name)
    if target is None:
        available = ", ".join(d.get("name", "Unknown") for d in devices)
        return f"I don't see a device matching '{device_name}', sir. Available: {available}."

    try:
        sp.transfer_playback(target["id"], force_play=True)
        return f"Playback moved to {target.get('name', 'that device')}."
    except Exception as exc:  # noqa: BLE001
        log.exception("transfer_playback failed")
        return f"Spotify error: {exc}"
