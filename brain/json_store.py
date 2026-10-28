"""Small JSON files holding one list of records (timers, reminders).

``save`` writes atomically (temp file + rename) so a crash mid-write never
leaves a half file; ``load`` skips records that don't parse instead of
losing the whole list.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TypeVar

from jarvis_logger import get_logger

log = get_logger(__name__)
T = TypeVar("T")


def save(path: Path, key: str, records: list[dict[str, Any]]) -> None:
    """Write ``{key: records}``; logs (never raises) when the disk says no."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump({key: records}, handle, indent=2)
        tmp.replace(path)
    except OSError as exc:
        log.error("Could not save %s: %s", path.name, exc)


def load(path: Path, key: str, build: Callable[[dict[str, Any]], T]) -> list[T]:
    """Every record under ``key`` that ``build`` accepts; [] for no file or bad JSON."""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read %s (%s); starting empty.", path.name, exc)
        return []
    records = data.get(key, []) if isinstance(data, dict) else []
    out: list[T] = []
    for raw in records:
        try:
            out.append(build(raw))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.warning("Skipping malformed record in %s %r: %s", path.name, raw, exc)
    return out
