"""The colour theme every JARVIS screen shares (phone, TV).

Picked on any client and saved here (``memory/theme.json``) so the others
follow; ``JARVIS_THEME`` in ``.env`` is only the default until someone picks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

# Same names as JarvisPalette (iOS) and Theme.java (TV).
THEMES = ("arc", "hotRod", "malibu", "crimson", "mark85", "spiderMan", "mcqueen")
# Retired picks land on their closest survivor.
_RETIRED = {"markIII": "hotRod", "graphite": "arc", "stealth": "arc"}
_FILE = Path(__file__).resolve().parents[1] / "memory" / "theme.json"


def current() -> dict[str, object]:
    """``{"theme", "chosen", "updated_at"}``; chosen is False while it's the .env default."""
    try:
        data = json.loads(_FILE.read_text())
        data["theme"] = _RETIRED.get(data.get("theme"), data.get("theme"))
        if data.get("theme") in THEMES:
            return {"theme": data["theme"], "chosen": True, "updated_at": float(data.get("updated_at", 0))}
    except (OSError, ValueError, AttributeError):
        pass
    default = os.getenv("JARVIS_THEME", "arc").strip()
    default = _RETIRED.get(default, default)
    return {"theme": default if default in THEMES else "arc", "chosen": False, "updated_at": 0.0}


def choose(theme: str) -> dict[str, object]:
    if theme not in THEMES:
        raise ValueError(f"Theme must be one of {', '.join(THEMES)}.")
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"theme": theme, "updated_at": time.time()}))
    tmp.replace(_FILE)
    return current()
