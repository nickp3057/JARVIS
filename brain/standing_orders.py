"""Standing orders: durable behavioral directives Nick gives JARVIS.

Distinct from the three memory layers. Memory facts are things JARVIS
*knows about Nick*; settings are numeric or enumerated knobs in ``.env``;
a standing order is a free-form instruction about how JARVIS should
*behave* from now on: "stop opening with 'certainly'", "give temperatures
in Celsius", "never suggest films rated under six". They are injected
into every system prompt until Nick removes them, so a correction sticks
instead of lasting a single turn.

Persisted to ``memory/standing_orders.json``. The model adds and removes
orders through the ``add_standing_order`` / ``remove_standing_order`` tools;
the store is deliberately small (a few dozen short lines) because every
order costs prompt space on every turn.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "standing_orders.json"
MAX_ORDERS = 40
MAX_CHARS = 240
_lock = threading.Lock()


def _path() -> Path:
    return _FILE


def _load() -> list[dict[str, Any]]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("Could not read standing orders: %s", exc)
        return []
    items = raw.get("orders", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict) and str(item.get("text", "")).strip()]


def _save(items: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"orders": items}, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())[:MAX_CHARS].strip()


def add_order(text: Any) -> str:
    """Record a directive. Returns a spoken-friendly confirmation."""
    cleaned = _clean(text)
    if not cleaned:
        return "A standing order needs some wording."
    with _lock:
        items = _load()
        for item in items:
            if str(item.get("text", "")).lower() == cleaned.lower():
                return f"That is already standing order {item['id']}: {item['text']}"
        if len(items) >= MAX_ORDERS:
            return f"Standing orders are capped at {MAX_ORDERS}. Remove one before adding another."
        item = {"id": uuid.uuid4().hex[:8], "text": cleaned, "created_at": time.time()}
        items.append(item)
        _save(items)
    log.info("Standing order added %s: %s", item["id"], cleaned)
    return f"Standing order {item['id']} recorded: {cleaned}"


def remove_order(identifier: Any) -> str:
    """Remove by exact id, or by a unique case-insensitive text fragment."""
    needle = _clean(identifier)
    if not needle:
        return "Say which standing order to remove, by id or a few of its words."
    with _lock:
        items = _load()
        matches = [item for item in items if str(item.get("id")) == needle]
        if not matches:
            matches = [item for item in items if needle.lower() in str(item.get("text", "")).lower()]
        if not matches:
            return f"No standing order matches '{needle}'."
        if len(matches) > 1:
            listing = "; ".join(f"{m['id']}: {m['text']}" for m in matches)
            return f"Several standing orders match. Use the id: {listing}"
        target = matches[0]
        items = [item for item in items if item is not target]
        _save(items)
    log.info("Standing order removed %s: %s", target["id"], target["text"])
    return f"Standing order {target['id']} removed: {target['text']}"


def list_orders() -> str:
    items = _load()
    if not items:
        return "No standing orders on file."
    lines = [f"{item['id']}: {item['text']}" for item in items]
    return "Standing orders:\n" + "\n".join(lines)


def all_orders() -> list[dict[str, Any]]:
    return _load()


def render_block() -> str:
    """Prompt-ready bullet list, or "" when there are no orders."""
    items = _load()
    if not items:
        return ""
    return "\n".join(f"- {item['text']} (order {item['id']})" for item in items)
