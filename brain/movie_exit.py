"""Exit Movie: the room goes back to however it was before the Movie scene.

When the Movie scene runs (the app's scene tiles through
``status_hub.activate_scene``, or "Hey JARVIS, movie time" through
``home_assistant.activate_home_action``), the room is snapshotted first,
the way Barn Door does it: a Home Assistant ``scene.create`` snapshot
(``scene.movie_before``, hidden from the app because it has no ``id``) of
the dashboard's lights, switches and fans, plus what HA's snapshot can't
bring back (each light's running effect / Govee DIY dropdown, and whether
the fan ring was in its rainbow). The scene the room was in goes to
``memory/movie_before.json``.

``exit_movie()`` (the nightstand's movie remote, ``POST /api/home/scenes/exit-movie``)
lets go of light sync without handing the strips back to the Movie scene,
restores the snapshot, and records the exit so ``status_hub.home_scenes``
reports the earlier scene (or none) as ``current`` instead of Movie. HA's
Movie scene still says it ran last, so Movie running again (a newer
``last_activated``) ends the override by itself.

A snapshot older than the Movie scene's last run (Movie started some way
that skipped the snapshot) isn't trusted: exit then falls back to
``status_hub._leave_scene`` (HA's Normal scene, or the bedroom light).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MOVIE_SCENE = "scene.movie"
_SNAPSHOT = "movie_before"
_NEVER = {"switch.wii_u"}  # Nick's rule: JARVIS never touches the Wii U plug
_STRIP = "light.nick_strip_lights"

_DIR = Path(__file__).resolve().parents[1] / "memory"
# Off in the test suite (tests/__init__.py), so a faked Movie run never snapshots real HA.
_SNAPSHOTS = True
# A snapshot taken more than this long before Movie's last run belongs to an older film.
_FRESH_S = 120.0


def _state_file() -> Path:
    return _DIR / "movie_before.json"


def _effects_file() -> Path:
    return _DIR / "movie_before_effects.json"


def _fan_file() -> Path:
    return _DIR / "movie_before_fan.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_state_file().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _iso_seconds(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def before_movie(previous: str | None) -> None:
    """Movie is about to run: snapshot the room. ``previous`` is the scene
    the room is in now (``home_scenes()["current"]``). Already in Movie:
    nothing (re-running Movie mustn't snapshot the Movie lights). Never
    raises: a failed snapshot only means exit falls back to Normal."""
    if previous == MOVIE_SCENE or not _SNAPSHOTS:
        return
    try:
        from app import status_hub
        from brain import protocols
        from integrations import home_assistant
        entities = [e for e in status_hub._dashboard_allowlist()
                    if e.split(".", 1)[0] in ("light", "switch", "fan") and e not in _NEVER]
        if not entities:
            return
        ok, message = home_assistant.call_service(
            "scene", "create", service_data={"scene_id": _SNAPSHOT, "snapshot_entities": entities})
        if not ok:
            log.warning("Couldn't snapshot the room before Movie: %s", message)
            return
        protocols._save_effects([e for e in entities if e.startswith("light.")], _effects_file())
        protocols._save_fan(_STRIP, _fan_file())
        _save({"previous": previous, "taken_at": time.time()})
    except Exception:  # noqa: BLE001 - Movie runs regardless
        log.warning("Couldn't snapshot the room before Movie", exc_info=True)


def current_override(movie_activated: str | None) -> tuple[bool, str | None]:
    """For ``home_scenes``: (True, scene) when Movie was exited since its last
    run, with the scene the room went back to (None = no scene)."""
    data = _load()
    exited = data.get("exited_activation")
    if not exited or exited != movie_activated:
        return False, None
    return True, data.get("previous")


def exit_movie() -> dict[str, Any]:
    """Leave the Movie scene for the room as it was before it."""
    from app import status_hub
    from brain import light_sync, protocols
    from integrations import home_assistant

    status_hub.invalidate("scenes")
    listing = status_hub.home_scenes()
    if listing.get("current") != MOVIE_SCENE:
        return {"error": "", "restored": False, "note": "The room isn't in the Movie scene."}
    movie = next((s for s in listing.get("scenes", []) if s["entity_id"] == MOVIE_SCENE), {})
    activated = movie.get("last_activated")

    light_sync.release_for_movie_exit()  # the strips stop streaming; no Movie scene put back

    data = _load()
    ran_at = _iso_seconds(activated)
    taken = data.get("taken_at")
    fresh = isinstance(taken, (int, float)) and (ran_at is None or ran_at - taken < _FRESH_S)
    if fresh:
        ok, message = home_assistant.call_service("scene", "turn_on", f"scene.{_SNAPSHOT}")
        if ok:
            for entity, effect in protocols._effects_to_restore(_effects_file()).items():
                if entity.startswith("select."):  # a Govee DIY scene dropdown
                    home_assistant.call_service("select", "select_option", entity, {"option": effect})
                else:
                    home_assistant.call_service("light", "turn_on", entity, {"effect": effect})
            protocols._fan_back(_fan_file())
        else:
            log.warning("Movie snapshot didn't restore (%s); falling back to Normal", message)
            fresh = False
    if not fresh:
        message = status_hub._leave_scene(MOVIE_SCENE)
        ok = status_hub._HA_SUCCESS_MARKER in str(message).lower()
        data["previous"] = None

    previous = data.get("previous")
    if previous and previous not in {s["entity_id"] for s in listing.get("scenes", [])}:
        previous = None
    _save({"previous": previous, "exited_activation": activated, "exited_at": time.time()})
    _effects_file().unlink(missing_ok=True)
    status_hub.invalidate("scenes")
    status_hub.invalidate("entities")
    return {"error": "" if ok else str(message), "restored": fresh, "current": previous}
