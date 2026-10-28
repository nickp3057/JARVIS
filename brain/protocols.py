"""Barn Door Protocol: Nick's room goes to red alert, and back.

Engage: Home Assistant snapshots the room's lights (``scene.create``), every
colour light in ``BARN_DOOR_LIGHTS`` goes full red, the plain ones in
``BARN_DOOR_OFF`` go dark, the shared theme switches to the red ``barnDoor``
palette (phone and TV follow; brain/theme.py remembers the old one) and the
TV shows a red banner. Stand down: the snapshot and the old theme come back.
"""
from __future__ import annotations

import os

from brain import event_bus, theme
from integrations import home_assistant

_SCENE = "barn_door_before"
_COLOUR = "light.nick_strip_lights,light.strip_lights,light.desk_lights,light.desk_lights_2,light.nicks_fan"
_OFF = "light.bedroom_lights"


def _ids(key: str, default: str) -> list[str]:
    return [e.strip() for e in os.getenv(key, default).split(",") if e.strip()]


def engage() -> str:
    colour, off = _ids("BARN_DOOR_LIGHTS", _COLOUR), _ids("BARN_DOOR_OFF", _OFF)
    if theme.current()["theme"] != "barnDoor":  # engaging twice must not snapshot the red
        home_assistant.call_service("scene", "create", service_data={
            "scene_id": _SCENE, "snapshot_entities": colour + off})
    failed = [e for e in colour if not home_assistant.call_service(
        "light", "turn_on", e, {"rgb_color": [255, 0, 0], "brightness": 255})[0]]
    for entity in off:
        home_assistant.call_service("light", "turn_off", entity)
    theme.choose("barnDoor", remember=True)
    event_bus.publish("barn_door", "BARN DOOR PROTOCOL", "Engaged. Room locked to red.",
                      source="protocols", severity="critical", push=False)
    note = f" {len(failed)} light(s) didn't answer." if failed else ""
    return f"Barn Door Protocol engaged, sir. Everything's red.{note}"


def stand_down() -> str:
    home_assistant.call_service("scene", "turn_on", f"scene.{_SCENE}")
    restored = theme.restore()
    return f"Standing down, sir. Lights are back and the theme is {restored} again."
