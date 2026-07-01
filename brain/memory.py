"""Persistent memory layer for the JARVIS assistant.

Memory is stored as JSON at ``memory/profile.json`` at the project root.
The file holds structured, evolving facts that Jarvis injects into its
system prompt on every request, so it has long-term context about Nick
across sessions.

Design notes:
- The agent (the LLM) writes to memory via the ``remember_fact`` tool -
  there is no manual editing flow.
- Each fact is a small dict with ``category``, ``content``, and an
  ``added_at`` timestamp. Categories are free-form so the model can
  invent useful ones (people, interests, preferences, projects, ...).
- ``forget_fact`` removes by id so the model can correct itself.
- Reads/writes are atomic enough for single-process use; we don't need
  concurrency control here.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_FILE = _PROJECT_ROOT / "memory" / "profile.json"


# --- Storage primitives -----------------------------------------------------

def _empty_memory() -> dict[str, Any]:
    return {"facts": []}


def _load() -> dict[str, Any]:
    if not _MEMORY_FILE.exists():
        return _empty_memory()
    try:
        with _MEMORY_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if "facts" not in data or not isinstance(data["facts"], list):
            log.warning("Memory file shape unexpected; resetting in-memory copy.")
            return _empty_memory()
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read memory file (%s); using empty memory.", exc)
        return _empty_memory()


def _save(data: dict[str, Any]) -> bool:
    try:
        _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _MEMORY_FILE.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        tmp_path.replace(_MEMORY_FILE)
        return True
    except OSError as exc:
        log.error("Could not write memory file: %s", exc)
        return False


# --- Public API -------------------------------------------------------------

def remember_fact(category: str, content: str) -> str:
    """Add a new fact to memory. Returns a short confirmation."""
    category = (category or "general").strip().lower() or "general"
    content = (content or "").strip()
    if not content:
        return "Refused: empty fact."

    data = _load()

    # De-dupe: if an existing fact in the same category has identical content,
    # don't add another copy.
    for existing in data["facts"]:
        if existing.get("category") == category and existing.get("content") == content:
            return f"Already remembered: {content}"

    fact = {
        "id": uuid.uuid4().hex[:8],
        "category": category,
        "content": content,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data["facts"].append(fact)

    if _save(data):
        log.info("Remembered [%s] %s", category, content)
        return f"Remembered ({category}): {content}"
    return "Failed to write to memory."


def forget_fact(fact_id: str) -> str:
    """Remove a fact by id. Returns a confirmation or an error."""
    fact_id = (fact_id or "").strip()
    if not fact_id:
        return "Refused: missing fact id."

    data = _load()
    before = len(data["facts"])
    data["facts"] = [f for f in data["facts"] if f.get("id") != fact_id]

    if len(data["facts"]) == before:
        return f"No fact found with id {fact_id}."

    if _save(data):
        log.info("Forgot fact id=%s", fact_id)
        return f"Forgotten: {fact_id}"
    return "Failed to write to memory."


def list_facts() -> list[dict[str, Any]]:
    """Return every stored fact (used by tools, debugging)."""
    return _load()["facts"]


def render_memory_block() -> str:
    """Return a human-readable text block for injection into the system prompt.

    To keep the system prompt from bloating as memory grows, only the most
    recently added facts are injected once the total exceeds MEMORY_MAX_FACTS
    (default 60; set to 0 to disable the cap). Older facts stay on disk and are
    still searchable - they're just not all force-fed into every prompt.
    """
    facts = list_facts()
    if not facts:
        return ""

    try:
        max_facts = int(os.getenv("MEMORY_MAX_FACTS", "60") or 60)
    except (ValueError, TypeError):
        max_facts = 60

    total = len(facts)
    truncated = False
    if max_facts > 0 and total > max_facts:
        # Facts are appended in chronological order, so the tail is newest.
        facts = facts[-max_facts:]
        truncated = True

    by_category: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        by_category.setdefault(fact.get("category", "general"), []).append(fact)

    lines: list[str] = []
    for category in sorted(by_category):
        lines.append(f"## {category}")
        for fact in by_category[category]:
            content = fact.get("content", "")
            fact_id = fact.get("id", "")
            lines.append(f"- ({fact_id}) {content}")
        lines.append("")

    block = "\n".join(lines).strip()
    if truncated:
        block += (
            f"\n\n(Showing the {max_facts} most recent of {total} "
            f"remembered facts.)"
        )
    return block
