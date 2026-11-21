"""Swap the old lamp plug (switch.lamp) for the new Govee lamp (run on Core's host, the Surface).

    cd ~/jarvis && .venv/bin/python ops/core/swap_lamp.py            # find it
    cd ~/jarvis && .venv/bin/python ops/core/swap_lamp.py --apply    # do it
    ... swap_lamp.py --apply light.some_new_lamp                         # pick one

Nick replaced the Tuya plug that switched his lamp with a Govee lamp
(2026-11-03). Before running: add the lamp in the Govee app (and turn on
"LAN Control" in its settings so the local integration sees it too).

1. Reloads both Govee integrations so HA discovers the lamp, then lists the
   new Govee lights (anything that isn't the strip, the desk or a segment).
2. With --apply: the cloud entity becomes ``light.lamp`` "Lamp" (the LAN one,
   if any, ``light.lamp_lan``; added to LIGHT_TWINS so scene offs hit both).
   The old ``switch.lamp`` is renamed ``switch.old_lamp_plug`` and disabled.
3. Every scene and automation that named switch.lamp now names light.lamp
   (switch.* services in those automations become homeassistant.*, which
   still works for the other switches in them).
4. ``.env``: switch.lamp -> light.lamp (HOME_DASHBOARD_ENTITIES and anything
   else), plus the LIGHT_TWINS pair. Restart jarvis-api afterwards.

Safe to run twice. Never touches switch.wii_u (Nick's rule).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.status_hub import _DEFAULT_LIGHT_TWINS  # noqa: E402
from integrations import home_assistant as ha  # noqa: E402

OLD, OLD_RENAMED, NEW, NEW_LAN = "switch.lamp", "switch.old_lamp_plug", "light.lamp", "light.lamp_lan"
KNOWN = {"light.desk_lights", "light.desk_lights_2", "light.strip_lights", "light.nick_strip_lights",
         NEW, NEW_LAN}
GOVEE = ("govee", "govee_light_local")


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


def reload_govee() -> None:
    ok, entries = ha._request("GET", "/api/config/config_entries/entry")
    for entry in entries if ok else []:
        if entry.get("domain") in GOVEE:
            done, _ = ha._request("POST", f"/api/config/config_entries/entry/{entry['entry_id']}/reload")
            print(f"reload {entry['domain']}: {'ok' if done else 'failed'}")
    time.sleep(15)


def candidates(ws: _WS) -> list[dict]:
    registry = ws.call(type="config/entity_registry/list").get("result") or []
    return [e for e in registry
            if e["entity_id"].startswith("light.") and e.get("platform") in GOVEE
            and "segment" not in e["entity_id"] and e["entity_id"] not in KNOWN
            and not e.get("disabled_by")]


def rename(ws: _WS, entity_id: str, new_id: str | None, name: str | None, disable: bool = False) -> None:
    update = {"type": "config/entity_registry/update", "entity_id": entity_id}
    if new_id and new_id != entity_id:
        update["new_entity_id"] = new_id
    if name:
        update["name"] = name
    if disable:
        update["disabled_by"] = "user"
    reply = ws.call(**update)
    print(f"{entity_id} -> {new_id or entity_id} {name or ''}: {'ok' if reply.get('success') else reply.get('error')}")


def _configs(domain: str) -> list[tuple[str, dict]]:
    ok, states = ha._request("GET", "/api/states")
    found = []
    for state in states if ok else []:
        config_id = (state.get("attributes") or {}).get("id")
        if state["entity_id"].startswith(f"{domain}.") and config_id:
            ok, config = ha._request("GET", f"/api/config/{domain}/config/{config_id}")
            if ok and isinstance(config, dict):
                found.append((str(config_id), config))
    return found


def fix_scenes() -> None:
    for config_id, config in _configs("scene"):
        entities = config.get("entities") or {}
        if OLD not in entities:
            continue
        spec = entities.pop(OLD)
        state = spec.get("state") if isinstance(spec, dict) else spec
        entities[NEW] = {"state": state if state in ("on", "off") else "off"}
        ok, reply = ha._request("POST", f"/api/config/scene/config/{config_id}", json=config)
        print(f"scene {config_id}: lamp {entities[NEW]['state']} {'ok' if ok else f'FAILED {reply}'}")
    ha.call_service("scene", "reload")


def fix_automations() -> None:
    for config_id, config in _configs("automation"):
        text = json.dumps(config)
        if OLD not in text:
            continue
        text = text.replace(f'"{OLD}"', f'"{NEW}"')
        text = re.sub(r'"switch\.(turn_on|turn_off|toggle)"', r'"homeassistant.\1"', text)
        ok, reply = ha._request("POST", f"/api/config/automation/config/{config_id}", json=json.loads(text))
        print(f"automation {config.get('alias') or config_id}: {'ok' if ok else f'FAILED {reply}'}")
    ha.call_service("automation", "reload")


def fix_env(lan: bool) -> None:
    path = ROOT / ".env"
    lines = path.read_text().splitlines()
    out, has_twins = [], False
    for line in lines:
        line = re.sub(r"\bswitch\.lamp\b", NEW, line)
        if lan and line.startswith("LIGHT_TWINS="):
            has_twins = True
            if f"{NEW}=" not in line:
                line += f",{NEW}={NEW_LAN}"
        out.append(line)
    if lan and not has_twins:
        out.append(f"LIGHT_TWINS={_DEFAULT_LIGHT_TWINS},{NEW}={NEW_LAN}")
    if out != lines:
        path.write_text("\n".join(out) + "\n")
        print(".env: updated (restart jarvis-api)")
    else:
        print(".env: already fine")


def main(argv: list[str]) -> None:
    apply = "--apply" in argv
    picked = [a for a in argv if a.startswith("light.")]
    reload_govee()
    ws = _WS()
    try:
        found = candidates(ws)
        for e in found:
            print(f"found {e['entity_id']} ({e['platform']}) {e.get('original_name') or ''}")
        if picked:
            found = [e for e in found if e["entity_id"] in picked] or [
                {"entity_id": p, "platform": "govee"} for p in picked]
        cloud = [e for e in found if e["platform"] == "govee"]
        local = [e for e in found if e["platform"] == "govee_light_local"]
        if not found:
            print("No new Govee light yet. Add it in the Govee app (turn on LAN Control), then run again.")
            return
        if len(cloud) > 1 or len(local) > 1:
            print("More than one new Govee light; name the lamp: swap_lamp.py --apply light.xxx [light.yyy]")
            return
        if not apply:
            print("Looks right? Run again with --apply.")
            return
        main_light, twin = (cloud or local)[0], (local[0] if cloud and local else None)
        rename(ws, OLD, OLD_RENAMED, None, disable=True)
        rename(ws, main_light["entity_id"], NEW, "Lamp")
        if twin:
            rename(ws, twin["entity_id"], NEW_LAN, "Lamp")
    finally:
        ws.ws.close()
    fix_scenes()
    fix_automations()
    fix_env(lan=twin is not None)


if __name__ == "__main__":
    main(sys.argv[1:])
