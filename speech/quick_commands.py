"""Home commands the room voice runs itself, without asking Claude.

The point is speed: "game time", "peace", "desk lights off", "turn on the
lamp", "bedroom light to 40", "fan to 30", "TV off" go straight to Home Assistant (through the same
guarded ``app.status_hub`` calls the apps use) and answer with a jingle,
like a smart speaker. Anything that doesn't clearly match returns None and
goes to the normal conversation.

Only saved HA scenes and the ``HOME_DASHBOARD_ENTITIES`` allowlist can be
reached here.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from app import status_hub

_FILLER = re.compile(r"\b(please|the|my|jarvis|hey|can you|could you|would you|for me|now|mode|scene)\b")
_SPACES = re.compile(r"\s+")
_VERB = r"(?:turn|switch|shut|put)\s+"
# "turn off the lamp", "turn the lamp off", "lamp off"
_ON_OFF = (re.compile(rf"^{_VERB}(?P<action>on|off)\s+(?P<target>.+)$"),
           re.compile(rf"^(?:{_VERB})?(?P<target>.+?)\s+(?P<action>on|off)$"))
_LEVEL = re.compile(r"^(?:set|dim|turn|put|make)?\s*(.+?)\s+(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?$")
# Nick's room TV (its own remote protocol, integrations/tv_remote.py), not an allowlisted entity.
_TV = re.compile(r"^(?:tv|telly|television)$")
# Any of these means "lock the room camera off now" (said to the room voice).
_KILL_CAMERA = re.compile(r"\b(?:kill|stop|disable|shut (?:off|down)|turn off)\b.*\bcamera\b|\bcamera\b.*\boff\b")
# "start light sync", "light sync off", "stop the light sync" (brain/light_sync.py).
_LIGHT_SYNC = re.compile(r"^(?:(?P<verb>start|stop|begin|end|resume|turn on|turn off|switch on|switch off|"
                         r"enable|disable|cancel|kill)\s+)?lights?\s?sync(?:\s+(?P<tail>on|off))?$")
_LIGHT_SYNC_ON = {"start", "begin", "resume", "turn on", "switch on", "enable", "on"}
_SCENE_VERBS = re.compile(r"^(?:run|start|activate|set|turn on|do|it's|its|time for)\s+")


@dataclass
class QuickResult:
    ok: bool
    action: str  # e.g. "scene.game_time", "light.desk_lights_2 off"
    error: str = ""


def normalize(text: str) -> str:
    text = re.sub(r"[^\w\s%']", " ", (text or "").lower())
    return _SPACES.sub(" ", _FILLER.sub(" ", text)).strip()


def _squash(text: str) -> str:
    return text.replace(" ", "").replace("'", "")


def _match_scene(text: str, scenes: list[dict]) -> dict | None:
    bare = _SCENE_VERBS.sub("", text)
    for scene in scenes:
        if _squash(normalize(scene["name"])) == _squash(bare):  # "good night" == "Goodnight"
            return scene
    return None


def _match_entity(target: str, entities: list[dict]) -> dict | None:
    """The allowlisted entity whose name best matches; None when unclear."""
    target = _squash(target)
    exact = [e for e in entities if _squash(normalize(e["friendly_name"])) == target]
    if len(exact) == 1:
        return exact[0]
    # "desk light" -> "Desk Lights", "fan" -> "Nick's Fan": a unique containment.
    loose = [e for e in entities
             if target and (target in _squash(normalize(e["friendly_name"]))
                            or _squash(normalize(e["friendly_name"])).rstrip("s") == target.rstrip("s"))]
    if len(loose) > 1:
        # "fan" matches Nick's Fan and the Fan Light: the noun names the domain.
        noun = target.rstrip("s")
        loose = [e for e in loose if e["domain"] == noun] or loose
    return loose[0] if len(loose) == 1 else None


def run(text: str) -> QuickResult | None:
    """Run ``text`` if it is plainly a scene, an on/off, a brightness or a fan speed; else None."""
    text = normalize(text)
    if not text or len(text.split()) > 8:
        return None
    sync = _LIGHT_SYNC.match(text)
    if sync and (sync["verb"] or sync["tail"]):
        from brain import light_sync
        if (sync["tail"] or sync["verb"]) in _LIGHT_SYNC_ON:
            ok, _ = light_sync.controller.start()
            # Can't (no track, nothing playing): the conversation says why.
            return QuickResult(True, "light sync on") if ok else None
        light_sync.controller.stop()
        return QuickResult(True, "light sync off")

    scenes = status_hub.home_scenes().get("scenes", [])
    scene = _match_scene(text, scenes)
    if scene:
        result = status_hub.activate_scene(scene["entity_id"])
        return QuickResult(not result.get("error"), scene["entity_id"], result.get("error", ""))

    entities = status_hub.home_entities().get("entities", [])
    level = _LEVEL.match(text)
    if level:
        # Fans first: "fan to 30" means the fan's speed, never its light.
        fan = _match_entity(level.group(1), [e for e in entities if e["domain"] == "fan"])
        if fan:
            result = status_hub.set_fan(fan["entity_id"], int(level.group(2)))
            return QuickResult(not result.get("error"), f"{fan['entity_id']} {level.group(2)}%",
                               result.get("error", ""))
        entity = _match_entity(level.group(1), [e for e in entities if e["domain"] == "light"])
        if entity:
            result = status_hub.set_light(entity["entity_id"], brightness_pct=int(level.group(2)))
            return QuickResult(not result.get("error"), f"{entity['entity_id']} {level.group(2)}%",
                               result.get("error", ""))
        return None

    # "kill the camera" / "camera off" / "kill camera": a hard lockout, always.
    if _KILL_CAMERA.search(text):
        from brain import camera
        camera.kill()
        return QuickResult(True, "camera killed")

    on_off = _ON_OFF[0].match(text) or _ON_OFF[1].match(text)
    if on_off and _TV.match(on_off["target"].strip()):
        from integrations import tv_remote
        ok, detail = tv_remote.power(on_off["action"] == "on")
        return QuickResult(ok, f"tv {on_off['action']}", "" if ok else detail)
    if on_off:
        action = on_off["action"]
        entity = _match_entity(on_off["target"], entities)
        if entity:
            result = status_hub.home_control(entity["entity_id"], action, settle=False)
            return QuickResult(not result.get("error"), f"{entity['entity_id']} {action}",
                               result.get("error", ""))
    return None
