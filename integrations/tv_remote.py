"""Nick's room TV through Home Assistant's Android TV Remote integration.

``TV_REMOTE_ENTITY`` (``remote.nick_s_room_tv``) speaks the TV's own remote
protocol: it can wake the TV from standby and press any button, whatever app
is on screen. Home Assistant also lists the TV as several media players; the
Google Cast ones open Home Assistant's cast screen when "turned on", so
``home_assistant.call_service`` sends on/off for any of ``TV_MEDIA_PLAYERS``
here instead. The companion's remote (``POST /api/tv/remote``) and the
``tv_power`` tool both land here.
"""
from __future__ import annotations

import os

from integrations import home_assistant

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


def power(on: bool) -> tuple[bool, str]:
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
        return home_assistant.call_service("remote", "turn_on", entity(), {"activity": APPS[key]})
    if key.startswith("hdmi_") and key[5:].isdigit():
        link = input_link(int(key[5:]))
        if link is None:
            raise ValueError(f"The TV has no {key.replace('_', ' ').upper()}.")
        return home_assistant.call_service("remote", "turn_on", entity(), {"activity": link})
    code = KEYS.get(key)
    if code is None:
        raise ValueError(f"Unknown button {key!r}.")
    return home_assistant.call_service("remote", "send_command", entity(), {"command": code})


def play_plex(rating_key: str | int, server: str | None) -> tuple[bool, str]:
    """Wake the TV and start a Plex item in the Plex app (a plex:// deep
    link sent as the remote's turn_on activity; Plex resumes where Nick
    left off)."""
    from urllib.parse import quote
    link = f"plex://play/?metadataKey={quote(f'/library/metadata/{rating_key}', safe='')}"
    if server:
        link += f"&server={server}"
    return home_assistant.call_service("remote", "turn_on", entity(), {"activity": link})
