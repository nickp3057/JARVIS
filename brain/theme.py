"""The colour theme every JARVIS screen shares (phone, TV).

Picked on any client and saved here (``memory/theme.json``) so the others
follow; ``JARVIS_THEME`` in ``.env`` is only the default until someone picks.

The Experimenting theme's switches (``experiments``, name -> on/off, e.g.
``plasma``, ``neural``) are shared the same way. Names are whatever the iOS
``Experiment`` enum uses: Core stores any short alphanumeric name, so a new
experiment needs no change here; an unset one is the app's default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any

# Same names as JarvisPalette (iOS) and Theme.java (TV).
THEMES = ("arc", "hotRod", "malibu", "crimson", "mark85", "spiderMan", "mcqueen", "experimenting", "barnDoor")
# experimenting is the iOS visual lab (Views/Experiments.swift); the TV shows it as Spider-Man.
# barnDoor is Barn Door Protocol's red alert (brain/protocols.py), not a pick.
# Retired picks land on their closest survivor.
_RETIRED = {"markIII": "hotRod", "graphite": "arc", "stealth": "arc"}
_FILE = Path(__file__).resolve().parents[1] / "memory" / "theme.json"
_EXPERIMENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,23}$")
_MAX_EXPERIMENTS = 16


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(_FILE)


def _experiments(data: dict[str, Any]) -> dict[str, bool]:
    raw = data.get("experiments")
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and _EXPERIMENT_NAME.match(k) and isinstance(v, bool)}


def _stamp(data: dict[str, Any], key: str) -> float:
    try:
        return float(data.get(key, 0))
    except (TypeError, ValueError):
        return 0.0


def current() -> dict[str, object]:
    """``{"theme", "chosen", "updated_at", "experiments", "revision", "core", "lab"}``; chosen is
    False while it's the .env default. ``revision`` moves with any theme pick or
    experiment switch (the home stream carries it so screens refetch at once)."""
    data = _read()
    experiments = _experiments(data)
    switched = _stamp(data, "experiments_updated_at")
    theme = _RETIRED.get(data.get("theme"), data.get("theme"))
    if theme in THEMES:
        updated = _stamp(data, "updated_at")
        underneath = _saved_before() if theme == "barnDoor" else theme
        return {"theme": theme, "chosen": True, "updated_at": updated, "experiments": experiments,
                "revision": max(updated, switched), **_looks(underneath, experiments)}
    default = os.getenv("JARVIS_THEME", "arc").strip()
    default = _RETIRED.get(default, default)
    return {"theme": default if default in THEMES else "arc", "chosen": False, "updated_at": 0.0,
            "experiments": experiments, "revision": switched, **_looks(default, experiments)}


# Each experiment's default while nobody has flipped it (the iOS
# `Experiment.defaultOn`; keep the two in step).
_LAB_DEFAULTS = {"plasma": True, "neural": True, "orb3D": False, "glass": True}


def _lab(underneath: str | None, experiments: dict[str, bool]) -> dict[str, bool]:
    """The experiments actually showing: every switch with its default filled
    in while Experimenting is the pick (or what Barn Door replaced), else none.
    The TV reads this; the phone and iPad apply the switches themselves."""
    if underneath != "experimenting":
        return {}
    return {name: experiments.get(name, default) for name, default in _LAB_DEFAULTS.items()}


def _looks(underneath: str | None, experiments: dict[str, bool]) -> dict[str, object]:
    lab = _lab(underneath, experiments)
    return {"core": "neural" if lab.get("neural") else "classic", "lab": lab}


def choose(theme: str, remember: bool = False) -> dict[str, object]:
    """Pick the shared theme; ``remember`` keeps the one it replaces for restore()."""
    if theme not in THEMES:
        raise ValueError(f"Theme must be one of {', '.join(THEMES)}.")
    before = current()["theme"]
    data: dict[str, object] = {"theme": theme, "updated_at": time.time()}
    if remember:
        data["before"] = _saved_before() or before
    experiments = _experiments(_read())
    if experiments:
        data["experiments"] = experiments
    _write(data)
    return current()


def set_experiments(changes: dict[str, Any]) -> dict[str, object]:
    """Merge experiment switches into the shared pick; bad names or values raise ValueError."""
    for name, value in changes.items():
        if not isinstance(name, str) or not _EXPERIMENT_NAME.match(name) or not isinstance(value, bool):
            raise ValueError(f"Experiment switches are short names set to true or false, not {name!r}: {value!r}.")
    data = _read()
    merged = {**_experiments(data), **changes}
    if len(merged) > _MAX_EXPERIMENTS:
        raise ValueError(f"At most {_MAX_EXPERIMENTS} experiment switches.")
    data["experiments"] = merged
    data["experiments_updated_at"] = time.time()
    _write(data)
    return current()


def _saved_before() -> str | None:
    before = _read().get("before")
    return before if before in THEMES else None


def restore() -> str:
    """Back to the theme a remembered choose() replaced (Arc if none)."""
    previous = _saved_before() or "arc"
    choose(previous)
    return previous
