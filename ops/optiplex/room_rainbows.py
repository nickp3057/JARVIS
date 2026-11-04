"""Rainbows for the lamp and the fan ring (run on the Optiplex).

    cd ~/jarvis && .venv/bin/python ops/optiplex/room_rainbows.py
    ... room_rainbows.py scene.some_tuya_scene     # name the Tap-to-Run yourself

Nick, 2026-11-04: the lamp comes on, in its rainbow, in every scene where the
desk lights come on; the fan light gets a rainbow that goes around its ring,
and it's on whenever the strip lights are on.

1. Scenes that turn the desk lights on also turn on the lamp (100%) in its
   built-in rotating "Rainbow" (``select.lamp_scene``).
2. Scenes that turn the strip on no longer also turn the fan light off
   (Movie did): the fan light follows the strip now.
3. The ring's multi-colour mode is a Tuya "scene" work mode that HA's Tuya
   integration can't set, but it does show Smart Life Tap-to-Run scenes as
   ``scene.*`` entities. Make one in Smart Life first (Create Scene > Launch
   Tap-to-Run > action: the fan light, its Scene mode, the rainbow one; name
   it "Fan Light Rainbow"). This reloads Tuya, finds it (a Tuya scene with
   "rainbow" in its name) and renames it ``scene.fan_light_rainbow``, which
   the apps' Rainbow button for the fan light uses (LIGHT_RAINBOWS default).
4. Adds the automation "Fan light follows the strip"
   (``automation.fan_light_follows_strip``): strip off -> on runs that scene,
   strip -> off turns the fan light off. Barn Door pauses it while engaged.

Safe to run twice. Never touches switch.wii_u (Nick's rule).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from integrations import home_assistant as ha  # noqa: E402

DESK, STRIP, LAMP, FAN_LIGHT = "light.desk_lights_2", "light.nick_strip_lights", "light.lamp", "light.nicks_fan"
LAMP_SCENE, LAMP_RAINBOW = "select.lamp_scene", "Rainbow"
FAN_RAINBOW = "scene.fan_light_rainbow"
AUTOMATION_ID = "fan_light_follows_strip"


def _on(spec) -> bool:
    return (spec.get("state") if isinstance(spec, dict) else spec) == "on"


def fix_scenes() -> None:
    ok, states = ha._request("GET", "/api/states")
    for state in states if ok else []:
        config_id = (state.get("attributes") or {}).get("id")
        if not state["entity_id"].startswith("scene.") or not config_id:
            continue
        ok, config = ha._request("GET", f"/api/config/scene/config/{config_id}")
        if not ok or not isinstance(config, dict):
            continue
        entities = config.get("entities") or {}
        changes = []
        if _on(entities.get(DESK)):
            want = {"state": "on", "brightness": 255}
            if entities.get(LAMP) != want or entities.get(LAMP_SCENE) != LAMP_RAINBOW:
                entities[LAMP] = want
                entities[LAMP_SCENE] = LAMP_RAINBOW
                changes.append("lamp rainbow")
        if _on(entities.get(STRIP)) and FAN_LIGHT in entities and not _on(entities[FAN_LIGHT]):
            entities.pop(FAN_LIGHT)
            changes.append("fan light follows the strip")
        if not changes:
            print(f"scene {config_id}: fine")
            continue
        config["entities"] = entities
        ok, reply = ha._request("POST", f"/api/config/scene/config/{config_id}", json=config)
        print(f"scene {config_id}: {', '.join(changes)} {'ok' if ok else f'FAILED {reply}'}")
    ha.call_service("scene", "reload")


class _WS:
    def __init__(self) -> None:
        import websocket

        base_url, token = ha._config()
        self.ws = websocket.create_connection(ha._websocket_url(base_url), timeout=15)
        self.ws.recv()
        self.ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(self.ws.recv()).get("type") != "auth_ok":
            raise SystemExit("Home Assistant refused the token")
        self.n = 0

    def call(self, **message):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, **message}))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") == self.n:
                return reply


def find_fan_rainbow(picked: str | None) -> bool:
    """Reload Tuya, find the Tap-to-Run and make it scene.fan_light_rainbow."""
    ok, entries = ha._request("GET", "/api/config/config_entries/entry")
    for entry in entries if ok else []:
        if entry.get("domain") == "tuya":
            done, _ = ha._request("POST", f"/api/config/config_entries/entry/{entry['entry_id']}/reload")
            print(f"reload tuya: {'ok' if done else 'failed'}")
    time.sleep(10)
    ws = _WS()
    try:
        registry = ws.call(type="config/entity_registry/list").get("result") or []
        scenes = [e for e in registry if e["entity_id"].startswith("scene.") and e.get("platform") == "tuya"]
        for e in scenes:
            print(f"tuya scene: {e['entity_id']} {e.get('original_name') or e.get('name') or ''}")
        if any(e["entity_id"] == FAN_RAINBOW for e in scenes):
            print(f"{FAN_RAINBOW}: already there")
            return True
        if picked:
            found = [e for e in scenes if e["entity_id"] == picked]
        else:
            found = [e for e in scenes if "rainbow" in
                     f"{e['entity_id']} {e.get('original_name') or ''} {e.get('name') or ''}".lower()]
        if len(found) != 1:
            print("No single Tuya rainbow scene found. In Smart Life: Scenes > + > Launch Tap-to-Run,"
                  " action = the fan light in Scene mode on its rainbow, name it \"Fan Light Rainbow\"."
                  " Then run this again (or name it: room_rainbows.py scene.xxx).")
            return False
        reply = ws.call(type="config/entity_registry/update", entity_id=found[0]["entity_id"],
                        new_entity_id=FAN_RAINBOW, name="Fan Light Rainbow")
        print(f"{found[0]['entity_id']} -> {FAN_RAINBOW}: {'ok' if reply.get('success') else reply.get('error')}")
        return bool(reply.get("success"))
    finally:
        ws.ws.close()


AUTOMATION = {
    "id": AUTOMATION_ID,
    "alias": "Fan light follows the strip",
    "description": "The fan ring's rainbow is on whenever the strip lights are (Nick, 2026-11-04). "
                   "JARVIS's Barn Door turns this off while engaged. Made by ops/optiplex/room_rainbows.py.",
    "mode": "restart",
    "triggers": [
        {"trigger": "state", "entity_id": STRIP, "from": "off", "to": "on", "id": "on"},
        {"trigger": "state", "entity_id": STRIP, "to": "off", "id": "off"},
    ],
    "conditions": [],
    "actions": [{"choose": [
        {"conditions": [{"condition": "trigger", "id": "on"}],
         "sequence": [{"action": "scene.turn_on", "target": {"entity_id": FAN_RAINBOW}}]},
        {"conditions": [{"condition": "trigger", "id": "off"}],
         "sequence": [{"action": "light.turn_off", "target": {"entity_id": FAN_LIGHT}}]},
    ]}],
}


def add_automation() -> None:
    ok, reply = ha._request("POST", f"/api/config/automation/config/{AUTOMATION_ID}", json=AUTOMATION)
    print(f"automation {AUTOMATION['alias']}: {'ok' if ok else f'FAILED {reply}'}")
    ha.call_service("automation", "reload")


if __name__ == "__main__":
    fix_scenes()
    if find_fan_rainbow(next((a for a in sys.argv[1:] if a.startswith("scene.")), None)):
        add_automation()
        ok, state = ha._request("GET", f"/api/states/{STRIP}")
        if ok and isinstance(state, dict) and state.get("state") == "on":
            ha.call_service("scene", "turn_on", FAN_RAINBOW)  # the strip's on now: ring on to match
            print("strip is on: fan ring rainbow on")
