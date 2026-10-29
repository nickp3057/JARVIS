"""Barn Door Protocol: Nick's room goes to red alert, and back.

Engage: Home Assistant snapshots the room's lights (``scene.create``), every
colour light in ``BARN_DOOR_LIGHTS`` goes full red, everything in
``BARN_DOOR_OFF`` that can't be red (the bedroom light, the Avengers tower)
goes dark, the TV turns on (``BARN_DOOR_TV``, on by default), the shared
theme switches to the red ``barnDoor`` palette (phone and TV follow;
brain/theme.py remembers the old one) and the TV hands its whole screen to
the red-alert view. Stand down: the snapshot and the old theme come back
(the TV stays on).

Speed: the theme flips and the TV wake goes out first (the TV hears the
theme over ``/api/home/stream`` at once), then the lights change in
parallel; they used to go one by one with the theme last, so the TV lagged
every slow Govee call.

The fan light (``light.nicks_fan``) is only ever turned off (Nick's rule),
so it goes dark with the bedroom light rather than red.

A scene snapshot restores colour and brightness but not animated modes, and
Govee's app modes (Nick's rainbow) don't show up in Home Assistant at all.
So stand-down also re-applies each light's effect: the one HA reported at
engage time, else ``BARN_DOOR_EFFECTS`` ("light.nick_strip_lights=Rainbow-A,
light.desk_lights_2=Rainbow"). The fan's rotating rainbow isn't exposed by
its integration, so it can't come back from here.

Govee DIY scenes (Nick's own rainbows, made in the Govee app) aren't light
effects: the Govee integration shows them as a dropdown per light,
``select.<light>_diy_scene``. A ``BARN_DOOR_EFFECTS`` entry keyed by that
select ("select.nick_strip_lights_diy_scene=Real Rainbow") is restored with
``select.select_option``, and whatever DIY scene was picked at engage wins.
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from brain import theme
from integrations import home_assistant

_SCENE = "barn_door_before"
_COLOUR = "light.nick_strip_lights,light.strip_lights,light.desk_lights,light.desk_lights_2"
_OFF = "light.bedroom_lights,light.nicks_fan,switch.avengers_tower_socket_1"  # not red: dark
_EFFECTS_FILE = Path(__file__).resolve().parents[1] / "memory" / "barn_door_effects.json"


def _ids(key: str, default: str) -> list[str]:
    return [e.strip() for e in os.getenv(key, default).split(",") if e.strip()]


def engage() -> str:
    colour, off = _ids("BARN_DOOR_LIGHTS", _COLOUR), _ids("BARN_DOOR_OFF", _OFF)
    first = theme.current()["theme"] != "barnDoor"  # engaging twice must not snapshot the red
    if first:
        home_assistant.call_service("scene", "create", service_data={
            "scene_id": _SCENE, "snapshot_entities": colour + off})
    # No alert: the red room and the TV taking over say it (Nick's call).
    theme.choose("barnDoor", remember=True)
    if os.getenv("BARN_DOOR_TV", "1").strip().lower() not in ("0", "off", "false", "no"):
        from integrations import tv_remote
        # The red-alert screen needs the TV awake; it takes seconds, so not inline.
        threading.Thread(target=tv_remote.power, args=(True,), daemon=True, name="barn-door-tv").start()
    if first:
        _save_effects(colour)

    def red(entity: str) -> bool:
        return home_assistant.call_service(
            "light", "turn_on", entity, {"rgb_color": [255, 0, 0], "brightness": 255})[0]

    def dark(entity: str) -> None:
        home_assistant.call_service(entity.split(".", 1)[0], "turn_off", entity)

    with ThreadPoolExecutor(max_workers=8) as pool:
        darkened = [pool.submit(dark, e) for e in off]
        failed = [e for e, ok in zip(colour, pool.map(red, colour)) if not ok]
        for job in darkened:
            job.result()
    note = f" {len(failed)} light(s) didn't answer." if failed else ""
    return f"Barn Door Protocol engaged, sir. Everything's red.{note}"


def _save_effects(entities: list[str]) -> None:
    """Each light's running effect (and each configured DIY-scene dropdown's
    pick), which scene snapshots don't bring back."""
    running = {}
    for entity in [s for s in _configured_effects() if s.startswith("select.")]:
        ok, state = home_assistant._request("GET", f"/api/states/{entity}")
        option = state.get("state") if ok and isinstance(state, dict) else None
        if option and str(option).lower() not in ("none", "unknown", "unavailable"):
            running[entity] = option
    for entity in entities:
        ok, state = home_assistant._request("GET", f"/api/states/{entity}")
        effect = (state.get("attributes") or {}).get("effect") if ok and isinstance(state, dict) else None
        if effect and str(effect).lower() != "none":
            running[entity] = effect
    _EFFECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EFFECTS_FILE.write_text(json.dumps(running))


def _configured_effects() -> dict[str, str]:
    effects = {}
    for pair in os.getenv("BARN_DOOR_EFFECTS", "").split(","):
        entity, _, effect = pair.partition("=")
        if entity.strip() and effect.strip():
            effects[entity.strip()] = effect.strip()
    return effects


def _effects_to_restore() -> dict[str, str]:
    effects = _configured_effects()
    try:
        effects.update(json.loads(_EFFECTS_FILE.read_text()))  # what was actually running wins
    except (OSError, ValueError):
        pass
    return effects


def stand_down() -> str:
    restored = theme.restore()  # first, so the TV lets go at once
    home_assistant.call_service("scene", "turn_on", f"scene.{_SCENE}")
    for entity, effect in _effects_to_restore().items():
        if entity.startswith("select."):  # a Govee DIY scene dropdown
            home_assistant.call_service("select", "select_option", entity, {"option": effect})
        else:
            home_assistant.call_service("light", "turn_on", entity, {"effect": effect})
    return f"Standing down, sir. Lights are back and the theme is {restored} again."
