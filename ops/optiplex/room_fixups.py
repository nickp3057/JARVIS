"""One-shot Home Assistant fixes for Nick's room (run on the Optiplex).

    cd ~/jarvis && .venv/bin/python ops/optiplex/room_fixups.py

1. Scenes that turn the fan on now also say its direction and preset.
   HA 2026.10's fan reproduce-state compares every fan attribute and, when
   the scene leaves one out while the fan is already running, calls
   ``fan.set_direction`` with None: "string value is None at 'direction'".
2. Adds a "Normal" scene, the everyday room: bedroom light on, the rest
   off. Pressing the scene the room is in (untouched) on the iPad or phone
   runs it, and running it means "no scene". Leaves the fan, the Scentsy and
   the Wii U alone; edit it in HA to taste (the script won't overwrite it).
3. Renames the room's entities (display names only; entity ids, and so
   every scene, automation and .env list, stay as they are).

Safe to run twice. Never touches switch.wii_u's state (Nick's rule).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from integrations import home_assistant as ha  # noqa: E402

FAN = "fan.nicks_fan"
FAN_DEFAULTS = {"direction": "forward", "preset_mode": "fresh"}
SCENES = ("good_morning", "study", "movie", "game_time", "goodnight", "peace", "rainbow")
NAMES = {
    "light.nick_strip_lights": "Strip Lights",
    "light.desk_lights_2": "Desk Lights",
    "light.bedroom_lights": "Bedroom Lights",
    "fan.nicks_fan": "Nick's Fan",
    "light.nicks_fan": "Fan Light",  # was also "Nick's Fan": voice couldn't tell them apart
    "switch.lamp": "Lamp",
    "switch.avengers_tower_socket_1": "Avengers Tower",
    "switch.scentsy_socket_1": "Scentsy",
    "switch.wii_u": "Wii U",
}


def fix_scenes() -> None:
    for scene_id in SCENES:
        ok, config = ha._request("GET", f"/api/config/scene/config/{scene_id}")
        if not ok or not isinstance(config, dict):
            print(f"scene {scene_id}: not found ({config})")
            continue
        fan = (config.get("entities") or {}).get(FAN)
        if not fan or fan.get("state") != "on" or all(k in fan for k in FAN_DEFAULTS):
            print(f"scene {scene_id}: fine")
            continue
        for key, value in FAN_DEFAULTS.items():
            fan.setdefault(key, value)
        ok, reply = ha._request("POST", f"/api/config/scene/config/{scene_id}", json=config)
        print(f"scene {scene_id}: {'fixed' if ok else f'FAILED {reply}'}")


NORMAL = {
    "id": "normal",
    "name": "Normal",
    "icon": "mdi:lightbulb-on",
    "entities": {
        "light.bedroom_lights": {"state": "on", "brightness": 255},
        "light.nick_strip_lights": {"state": "off"},
        "light.desk_lights_2": {"state": "off"},
        "light.nicks_fan": {"state": "off"},
        "switch.lamp": {"state": "off"},
        "switch.avengers_tower_socket_1": {"state": "off"},
    },
}


def add_normal() -> None:
    ok, _ = ha._request("GET", "/api/config/scene/config/normal")
    if ok:
        print("scene normal: already there (left as is)")
        return
    ok, reply = ha._request("POST", "/api/config/scene/config/normal", json=NORMAL)
    print(f"scene normal: {'added' if ok else f'FAILED {reply}'}")


def rename() -> None:
    import websocket

    base_url, token = ha._config()
    ws = websocket.create_connection(ha._websocket_url(base_url), timeout=10)
    try:
        ws.recv()
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            raise SystemExit("Home Assistant refused the token")
        for n, (entity_id, name) in enumerate(NAMES.items(), start=1):
            ws.send(json.dumps({"id": n, "type": "config/entity_registry/update",
                                "entity_id": entity_id, "name": name}))
            reply = json.loads(ws.recv())
            print(f"{entity_id} -> {name}: {'ok' if reply.get('success') else reply.get('error')}")
    finally:
        ws.close()


if __name__ == "__main__":
    fix_scenes()
    add_normal()
    ha.call_service("scene", "reload")
    rename()
