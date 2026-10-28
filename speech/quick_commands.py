"""Home commands the room voice runs itself, without asking Claude.

The point is speed: "game time", "peace", "desk lights off", "turn on the
lamp", "bedroom light to 40" go straight to Home Assistant (through the same
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
_ON_OFF = re.compile(r"^(?:turn|switch|shut|put)\s+(on|off)\s+(.+)$|^(?:turn|switch|shut|put)\s+(.+?)\s+(on|off)$|^(.+?)\s+(on|off)$")
_LEVEL = re.compile(r"^(?:set|dim|turn|put|make)?\s*(.+?)\s+(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?$")
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
    return loose[0] if len(loose) == 1 else None


def run(text: str) -> QuickResult | None:
    """Run ``text`` if it is plainly a scene, an on/off or a brightness; else None."""
    text = normalize(text)
    if not text or len(text.split()) > 8:
        return None
    scenes = status_hub.home_scenes().get("scenes", [])
    scene = _match_scene(text, scenes)
    if scene:
        result = status_hub.activate_scene(scene["entity_id"])
        return QuickResult(not result.get("error"), scene["entity_id"], result.get("error", ""))

    entities = status_hub.home_entities().get("entities", [])
    level = _LEVEL.match(text)
    if level:
        entity = _match_entity(level.group(1), entities)
        if entity and entity["domain"] == "light":
            result = status_hub.set_light(entity["entity_id"], brightness_pct=int(level.group(2)))
            return QuickResult(not result.get("error"), f"{entity['entity_id']} {level.group(2)}%",
                               result.get("error", ""))
        return None

    on_off = _ON_OFF.match(text)
    if on_off:
        groups = on_off.groups()
        action, target = (groups[0], groups[1]) if groups[0] else (
            (groups[3], groups[2]) if groups[3] else (groups[5], groups[4]))
        entity = _match_entity(target, entities)
        if entity:
            result = status_hub.home_control(entity["entity_id"], action)
            return QuickResult(not result.get("error"), f"{entity['entity_id']} {action}",
                               result.get("error", ""))
    return None
