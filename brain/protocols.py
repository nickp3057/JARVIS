"""Barn Door Protocol: Nick's room goes to red alert, and back.

Engage: Home Assistant snapshots the room's lights (``scene.create``), every
colour light in ``BARN_DOOR_LIGHTS`` goes full red, the plain ones in
``BARN_DOOR_OFF`` go dark, the shared theme switches to the red ``barnDoor``
palette (phone and TV follow; brain/theme.py remembers the old one) and the
TV shows a red banner. Stand down: the snapshot and the old theme come back.

A scene snapshot restores colour and brightness but not animated modes, and
Govee's app modes (Nick's rainbow) don't show up in Home Assistant at all.
So stand-down also re-applies each light's effect: the one HA reported at
engage time, else ``BARN_DOOR_EFFECTS`` ("light.nick_strip_lights=Rainbow-A,
light.desk_lights_2=Rainbow"). The fan's rotating rainbow isn't exposed by
its integration, so it can't come back from here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from brain import event_bus, theme
from integrations import home_assistant

_SCENE = "barn_door_before"
_COLOUR = "light.nick_strip_lights,light.strip_lights,light.desk_lights,light.desk_lights_2,light.nicks_fan"
_OFF = "light.bedroom_lights"
_EFFECTS_FILE = Path(__file__).resolve().parents[1] / "memory" / "barn_door_effects.json"


def _ids(key: str, default: str) -> list[str]:
    return [e.strip() for e in os.getenv(key, default).split(",") if e.strip()]


def engage() -> str:
    colour, off = _ids("BARN_DOOR_LIGHTS", _COLOUR), _ids("BARN_DOOR_OFF", _OFF)
    if theme.current()["theme"] != "barnDoor":  # engaging twice must not snapshot the red
        home_assistant.call_service("scene", "create", service_data={
            "scene_id": _SCENE, "snapshot_entities": colour + off})
        _save_effects(colour)
    failed = [e for e in colour if not home_assistant.call_service(
        "light", "turn_on", e, {"rgb_color": [255, 0, 0], "brightness": 255})[0]]
    for entity in off:
        home_assistant.call_service("light", "turn_off", entity)
    theme.choose("barnDoor", remember=True)
    event_bus.publish("barn_door", "BARN DOOR PROTOCOL", "Engaged. Room locked to red.",
                      source="protocols", severity="critical", push=False)
    note = f" {len(failed)} light(s) didn't answer." if failed else ""
    return f"Barn Door Protocol engaged, sir. Everything's red.{note}"


def _save_effects(entities: list[str]) -> None:
    """Each light's running effect, which scene snapshots don't bring back."""
    running = {}
    for entity in entities:
        ok, state = home_assistant._request("GET", f"/api/states/{entity}")
        effect = (state.get("attributes") or {}).get("effect") if ok and isinstance(state, dict) else None
        if effect and str(effect).lower() != "none":
            running[entity] = effect
    _EFFECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EFFECTS_FILE.write_text(json.dumps(running))


def _effects_to_restore() -> dict[str, str]:
    effects = {}
    for pair in os.getenv("BARN_DOOR_EFFECTS", "").split(","):
        entity, _, effect = pair.partition("=")
        if entity.strip() and effect.strip():
            effects[entity.strip()] = effect.strip()
    try:
        effects.update(json.loads(_EFFECTS_FILE.read_text()))  # what was actually running wins
    except (OSError, ValueError):
        pass
    return effects


def stand_down() -> str:
    home_assistant.call_service("scene", "turn_on", f"scene.{_SCENE}")
    for entity, effect in _effects_to_restore().items():
        home_assistant.call_service("light", "turn_on", entity, {"effect": effect})
    restored = theme.restore()
    return f"Standing down, sir. Lights are back and the theme is {restored} again."
