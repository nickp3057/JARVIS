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
from typing import Any, Callable

from dotenv import load_dotenv

from jarvis_logger import get_logger

# Load .env from project root so this module works no matter the cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

log = get_logger(__name__)

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
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

    preferred = os.getenv("SPOTIFY_PREFERRED_DEVICE", "").strip()  # read live, like every setting
    if preferred:
        match = _match_device(devices, preferred)
        if match:
            return match
        log.info("Preferred device '%s' not currently available; falling back.", preferred)

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

_NOT_CONFIGURED = "Spotify isn't configured, sir."


def _run(action: str, call: Callable[[Any], str]) -> str:
    """The shared shape of every command: no client -> say so; an API error
    -> "Spotify error: ..." (logged), never an exception."""
    sp = _get_client()
    if sp is None:
        return _NOT_CONFIGURED
    try:
        return call(sp)
    except Exception as exc:  # noqa: BLE001
        log.exception("%s failed", action)
        return f"Spotify error: {exc}"


def is_available() -> bool:
    return _get_client() is not None


def play_music(query: str) -> str:
    """Search Spotify for ``query`` (a track, else a playlist) and play it."""
    query = (query or "").strip()

    def play(sp: Any) -> str:
        if not query:
            return "I need something to play, sir."
        target = _pick_target_device(sp)
        if target is None:
            return _no_device_message()
        device_id, device_name = target.get("id"), target.get("name", "your device")
        tracks = sp.search(q=query, type="track", limit=1).get("tracks", {}).get("items", [])
        if tracks:
            sp.start_playback(device_id=device_id, uris=[tracks[0]["uri"]])
            return f"Playing {_format_track(tracks[0])} on {device_name}."
        playlists = sp.search(q=query, type="playlist", limit=1).get("playlists", {}).get("items", [])
        if playlists:
            sp.start_playback(device_id=device_id, context_uri=playlists[0]["uri"])
            return f"Playing the playlist '{playlists[0].get('name', query)}' on {device_name}."
        return f"I couldn't find anything for '{query}' on Spotify."

    return _run("play_music", play)


def pause_music() -> str:
    def pause(sp: Any) -> str:
        sp.pause_playback()
        return "Paused."
    return _run("pause_music", pause)


def resume_music() -> str:
    def resume(sp: Any) -> str:
        target = _pick_target_device(sp)
        if target is None:
            return _no_device_message()
        sp.start_playback(device_id=target.get("id"))
        return "Playing."
    return _run("resume_music", resume)


def skip_track() -> str:
    def skip(sp: Any) -> str:
        sp.next_track()
        return "Skipped."
    return _run("skip_track", skip)


def previous_track() -> str:
    def back(sp: Any) -> str:
        sp.previous_track()
        return "Going back."
    return _run("previous_track", back)


def current_track() -> str:
    def now(sp: Any) -> str:
        playing = sp.current_playback()
        if not playing or not playing.get("item"):
            return "Nothing is playing, sir."
        return f"{_format_track(playing['item'])}."
    return _run("current_track", now)


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
    def volume(sp: Any) -> str:
        clamped = max(0, min(100, int(percent)))
        sp.volume(clamped)
        return f"Volume {clamped}."
    return _run("set_volume", volume)


def list_speakers() -> str:
    """Return a spoken summary of available Spotify Connect devices."""
    sp = _get_client()
    if sp is None:
        return _NOT_CONFIGURED

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
    device_name = (device_name or "").strip()

    def transfer(sp: Any) -> str:
        if not device_name:
            return "Which device should I move playback to, sir?"
        devices = _list_devices_raw(sp)
        if not devices:
            return _no_device_message()
        target = _match_device(devices, device_name)
        if target is None:
            available = ", ".join(d.get("name", "Unknown") for d in devices)
            return f"I don't see a device matching '{device_name}', sir. Available: {available}."
        sp.transfer_playback(target["id"], force_play=True)
        return f"Playback moved to {target.get('name', 'that device')}."

    return _run("transfer_playback", transfer)
