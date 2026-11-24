"""Nick's room TV through Home Assistant's Android TV Remote integration.

``TV_REMOTE_ENTITY`` (``remote.nick_s_room_tv``) speaks the TV's own remote
protocol: it can wake the TV from standby and press any button, whatever app
is on screen. Home Assistant also lists the TV as several media players; the
Google Cast ones open Home Assistant's cast screen when "turned on", so
``home_assistant.call_service`` sends on/off for any of ``TV_MEDIA_PLAYERS``
here instead. The companion's remote (``POST /api/tv/remote``) and the
``tv_power`` tool both land here.

Each command tries Core's own connection to the TV first
(``integrations/tv_direct.py``, once paired) so a busy NAS can't slow the
remote, and falls back to Home Assistant when that can't get through.
"""
from __future__ import annotations

import os

from integrations import home_assistant, tv_direct
from jarvis_logger import get_logger

log = get_logger(__name__)

# Companion button -> Android TV Remote key name (remote.send_command).
KEYS = {
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
    "ok": "DPAD_CENTER", "back": "BACK", "home": "HOME",
    "play_pause": "MEDIA_PLAY_PAUSE", "rewind": "MEDIA_REWIND", "fast_forward": "MEDIA_FAST_FORWARD",
    "volume_up": "VOLUME_UP", "volume_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE",
}
# HDMI inputs. The TV_INPUT* keys do nothing on the TCL (its stock home
# screen handles them, and JARVIS TV replaced it), so hdmi_N opens the
# input's passthrough channel, which the TV's own TV app (com.tcl.tv) plays.
# Input ids come from `adb shell dumpsys tv_input`; on Nick's TCL HDMI 1-3
# are HW15 (PS5), HW16 (Switch), HW17 (Wii U).
_DEFAULT_HDMI = ",".join(f"com.tcl.tvinput/.TvPassThroughService/HW{n}" for n in (15, 16, 17))


def hdmi_inputs() -> list[str]:
    return [i.strip() for i in os.getenv("TV_HDMI_INPUTS", _DEFAULT_HDMI).split(",") if i.strip()]


def input_link(port: int) -> str | None:
    """The deep link that switches the TV to HDMI ``port`` (1-based)."""
    from urllib.parse import quote
    inputs = hdmi_inputs()
    if not 1 <= port <= len(inputs):
        return None
    return "content://android.media.tv/passthrough/" + quote(inputs[port - 1], safe="")
# Companion quick-launch button -> Android TV package (remote.turn_on activity).
APPS = {
    "app_plex": "com.plexapp.android",
    "app_youtube": "com.google.android.youtube.tv",
    "app_spotify": "com.spotify.tv.android",
    "app_settings": "com.android.tv.settings",
}
_DEFAULT_PLAYERS = ("media_player.nicks_room_tv,media_player.nicks_room_tv_2,media_player.nick_s_room_tv,"
                    "media_player.nicks_room_group,media_player.nick_s_room_display")


def entity() -> str:
    return os.getenv("TV_REMOTE_ENTITY", "remote.nick_s_room_tv").strip()


def is_tv_player(entity_id: str | None) -> bool:
    players = os.getenv("TV_MEDIA_PLAYERS", _DEFAULT_PLAYERS)
    return bool(entity_id) and entity_id.strip().lower() in {p.strip().lower() for p in players.split(",")}


def _direct(what: str, attempt) -> bool:
    """Try the direct connection; True when it got through."""
    if not tv_direct.configured():
        return False
    ok, detail = attempt()
    if not ok:
        log.warning("TV direct remote couldn't %s (%s); using Home Assistant", what, detail)
    return ok


def _send_key(code: str) -> tuple[bool, str]:
    if _direct(f"press {code}", lambda: tv_direct.press_key(code)):
        return True, "ok"
    return home_assistant.call_service("remote", "send_command", entity(), {"command": code})


def _launch(activity: str) -> tuple[bool, str]:
    if _direct(f"open {activity}", lambda: tv_direct.launch(activity)):
        return True, "ok"
    return home_assistant.call_service("remote", "turn_on", entity(), {"activity": activity})


def power(on: bool) -> tuple[bool, str]:
    if _direct("switch power", lambda: tv_direct.power(on)):
        return True, f"TV {'on' if on else 'off'}, sir."
    ok, detail = home_assistant.call_service("remote", "turn_on" if on else "turn_off", entity())
    if ok:
        return True, f"TV {'on' if on else 'off'}, sir."
    return False, f"The TV didn't answer: {detail}"


def press(key: str) -> tuple[bool, str]:
    """One companion-remote button; power_on / power_off switch the TV and
    app_* (``APPS``) open Plex, YouTube, Spotify or Settings."""
    if key in ("power_on", "power_off"):
        return power(key == "power_on")
    if key in APPS:  # wakes the TV if needed and opens the app
        return _launch(APPS[key])
    if key.startswith("hdmi_") and key[5:].isdigit():
        link = input_link(int(key[5:]))
        if link is None:
            raise ValueError(f"The TV has no {key.replace('_', ' ').upper()}.")
        return _launch(link)
    code = KEYS.get(key)
    if code is None:
        raise ValueError(f"Unknown button {key!r}.")
    return _send_key(code)


def stop_playback() -> tuple[bool, str]:
    """Stop whatever is playing (the remote's MEDIA_STOP; Plex closes the player)."""
    return _send_key("MEDIA_STOP")


def play_plex(rating_key: str | int, server: str | None) -> tuple[bool, str]:
    """Wake the TV and start a Plex item in the Plex app (a plex:// deep
    link sent as the remote's turn_on activity; Plex resumes where Nick
    left off)."""
    from urllib.parse import quote
    link = f"plex://play/?metadataKey={quote(f'/library/metadata/{rating_key}', safe='')}"
    if server:
        link += f"&server={server}"
    return _launch(link)


def play_item(rating_key: str, kind: str, title: str, *, verify_s: float | None = None,
              sleep=None) -> tuple[bool, str]:
    """Start a Plex item on the room TV the way that works (2026-11-20).

    The HA ``plex://`` deep link (``play_plex``) is ignored while Plex is
    already open, so the TV turned on and showed the wrong thing (the picker,
    "play X on my TV"). JARVIS TV's ``PlexWatch.playFresh`` closes Plex, opens
    the item's library page and presses Watch; Core asks it with a
    ``tv_play`` card, as the light sync calibration already does. ``kind``:
    movie, episode, or show (the show page's Watch plays its next-up episode).
    The TV is woken first (a sleeping TV's event stream reconnects on screen-on
    and replays the card), and if Plex isn't playing it within ``verify_s``
    (``TV_PLAY_VERIFY_S``, 35) the old deep link is tried once."""
    import os
    import threading
    import time as _time
    from brain import event_bus
    power(True)
    event_bus.publish_card("tv_play", title, {"rating_key": str(rating_key), "type": kind, "title": title},
                           source="tv", ttl_s=45)
    wait = verify_s if verify_s is not None else float(os.getenv("TV_PLAY_VERIFY_S", "35") or 35)
    nap = sleep or _time.sleep

    def verify() -> None:
        from integrations import plex
        nap(wait)
        ok, sessions = plex.sessions()
        if ok and isinstance(sessions, list) and any(
                str(s.get("rating_key") or s.get("ratingKey") or "") == str(rating_key)
                or str(s.get("grandparent_rating_key") or s.get("grandparentRatingKey") or "") == str(rating_key)
                for s in sessions):
            return
        log.info("JARVIS TV didn't start %s within %.0f s; trying the Plex deep link", title, wait)
        play_plex(rating_key, plex.server_id())

    if wait > 0:
        threading.Thread(target=verify, daemon=True, name="tv-play-verify").start()
    return True, f"Starting {title} on the TV."
