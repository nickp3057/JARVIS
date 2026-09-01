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
- ``update_fact`` replaces a contradicted fact in place while retaining a
  bounded revision trail; ``forget_fact`` removes by id.
- Reads/writes are atomic enough for single-process use; we don't need
  concurrency control here.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_FILE = _PROJECT_ROOT / "memory" / "profile.json"
_KEY_RE = re.compile(r"[^a-z0-9._-]+")
_FACT_WORD_RE = re.compile(r"[a-z0-9]+")
_CONTRADICTION_STOP_WORDS = {
    "a", "an", "and", "at", "for", "he", "her", "his", "i", "in", "is",
    "it", "my", "of", "on", "she", "that", "the", "to", "was",
}


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
        try:
            from brain.knowledge_vault import sync_profile_note

            sync_profile_note(data["facts"])
        except Exception as exc:  # noqa: BLE001
            # The JSON store remains authoritative; a disconnected NAS or bad
            # vault path must never make remembering fail.
            log.warning("Could not synchronize Obsidian memory mirror: %s", exc)
        return True
    except OSError as exc:
        log.error("Could not write memory file: %s", exc)
        return False


# --- Public API -------------------------------------------------------------

def _normalize_key(value: str | None) -> str:
    return _KEY_RE.sub("_", (value or "").strip().lower()).strip("_")[:120]


def _normalize_confidence(value: Any, default: float = 1.0) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return default


def _normalize_source(value: str | None, default: str = "conversation") -> str:
    return (" ".join((value or default).split()) or default)[:120]


def remember_fact(
    category: str,
    content: str,
    *,
    key: str = "",
    confidence: float | None = None,
    source: str = "conversation",
) -> str:
    """Add a new fact to memory. Returns a short confirmation."""
    category = (category or "general").strip().lower() or "general"
    content = (content or "").strip()
    if not content:
        return "Refused: empty fact."

    data = _load()
    key = _normalize_key(key)
    normalized_confidence = _normalize_confidence(confidence)
    source = _normalize_source(source)

    # A semantic key identifies one evolving attribute (for example,
    # ``preferences.favorite_color``). Learning a different value revises the
    # existing fact instead of silently accumulating a contradiction.
    if key:
        keyed = next((
            fact for fact in data["facts"]
            if fact.get("key") == key
        ), None)
        if keyed is not None:
            if keyed.get("content") == content:
                return f"Already remembered: {content}"
            return update_fact(
                str(keyed.get("id") or ""),
                content,
                category,
                key=key,
                confidence=normalized_confidence,
                source=source,
            ).replace("Updated", "Revised conflicting memory", 1)

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
        "confidence": normalized_confidence,
        "source": source,
    }
    if key:
        fact["key"] = key
    data["facts"].append(fact)

    if _save(data):
        log.info("Remembered [%s] %s", category, content)
        return f"Remembered ({category}): {content}"
    return "Failed to write to memory."


_PROFILE_RECORD_TYPES = {"person", "preference", "project", "routine", "goal"}


def remember_profile_record(
    record_type: str,
    name: str,
    detail: str,
    *,
    confidence: float | None = None,
    source: str = "conversation",
) -> str:
    """Store one typed, stable profile entity using the revision-aware fact layer."""
    record_type = (record_type or "").strip().lower()
    name = " ".join((name or "").split())[:120]
    detail = " ".join((detail or "").split())[:500]
    if record_type not in _PROFILE_RECORD_TYPES:
        return f"Refused: record type must be one of {', '.join(sorted(_PROFILE_RECORD_TYPES))}."
    if not name or not detail:
        return "Refused: profile record requires both a name and detail."
    semantic_name = _normalize_key(name)
    if not semantic_name:
        return "Refused: profile record name has no usable identifier."
    return remember_fact(
        record_type,
        f"{name}: {detail}",
        key=f"{record_type}.{semantic_name}",
        confidence=confidence,
        source=source,
    )


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


def update_fact(
    fact_id: str,
    content: str,
    category: str | None = None,
    *,
    key: str | None = None,
    confidence: float | None = None,
    source: str | None = None,
) -> str:
    """Replace a contradicted fact while preserving its identity and history."""
    fact_id = (fact_id or "").strip()
    content = (content or "").strip()
    if not fact_id:
        return "Refused: missing fact id."
    if not content:
        return "Refused: empty fact."

    data = _load()
    target = next((fact for fact in data["facts"] if fact.get("id") == fact_id), None)
    if target is None:
        return f"No fact found with id {fact_id}."

    next_category = (
        (category or "").strip().lower() or str(target.get("category") or "general")
    )
    current_category = str(target.get("category") or "general")
    current_content = str(target.get("content") or "")
    next_key = _normalize_key(key) if key is not None else str(target.get("key") or "")
    next_confidence = (
        _normalize_confidence(confidence)
        if confidence is not None else _normalize_confidence(target.get("confidence"))
    )
    next_source = (
        _normalize_source(source)
        if source is not None else _normalize_source(target.get("source"))
    )
    if (
        current_category == next_category
        and current_content == content
        and str(target.get("key") or "") == next_key
        and _normalize_confidence(target.get("confidence")) == next_confidence
        and _normalize_source(target.get("source")) == next_source
    ):
        return f"No change needed: {content}"

    changed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    revisions = target.get("revisions")
    if not isinstance(revisions, list):
        revisions = []
    revisions.append({
        "category": current_category,
        "content": current_content,
        "key": target.get("key", ""),
        "confidence": target.get("confidence", 1.0),
        "source": target.get("source", "conversation"),
        "replaced_at": changed_at,
    })
    target["revisions"] = revisions[-20:]
    target["category"] = next_category
    target["content"] = content
    if next_key:
        target["key"] = next_key
    else:
        target.pop("key", None)
    target["confidence"] = next_confidence
    target["source"] = next_source
    target["updated_at"] = changed_at

    if _save(data):
        log.info("Updated fact id=%s [%s] %s", fact_id, next_category, content)
        return f"Updated {fact_id} ({next_category}): {content}"
    return "Failed to write to memory."


def adjust_fact_confidence(fact_id: str, signal: str, amount: float | None = None) -> str:
    """Explicitly reinforce or decay one fact's confidence with an audit timestamp."""
    fact_id = (fact_id or "").strip()
    signal = (signal or "").strip().lower()
    if signal not in {"reinforce", "decay"}:
        return "Refused: confidence signal must be reinforce or decay."
    try:
        delta = float(amount if amount is not None else (0.1 if signal == "reinforce" else 0.05))
    except (TypeError, ValueError):
        return "Refused: invalid confidence amount."
    if not 0 < delta <= 1:
        return "Refused: confidence amount must be greater than 0 and at most 1."
    data = _load()
    target = next((fact for fact in data["facts"] if fact.get("id") == fact_id), None)
    if target is None:
        return f"No fact found with id {fact_id}."
    current = _normalize_confidence(target.get("confidence"))
    next_value = _normalize_confidence(current + delta if signal == "reinforce" else current - delta)
    if next_value == current:
        return f"Confidence already at {'maximum' if signal == 'reinforce' else 'minimum'} for {fact_id}."
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target["confidence"] = next_value
    target[f"last_{'reinforced' if signal == 'reinforce' else 'decayed'}_at"] = timestamp
    if _save(data):
        past = "Reinforced" if signal == "reinforce" else "Decayed"
        return f"{past} confidence for {fact_id}: {current:.2f} → {next_value:.2f}."
    return "Failed to write to memory."


def list_facts() -> list[dict[str, Any]]:
    """Return every stored fact (used by tools, debugging)."""
    return _load()["facts"]


def describe_facts(category: str = "", query: str = "") -> str:
    """Return inspectable fact IDs and metadata for correction or deletion."""
    category = (category or "").strip().lower()
    query = (query or "").strip().lower()
    facts = list_facts()
    if category:
        facts = [fact for fact in facts if str(fact.get("category", "")).lower() == category]
    if query:
        facts = [
            fact for fact in facts
            if query in f"{fact.get('category', '')} {fact.get('content', '')}".lower()
        ]
    if not facts:
        return "No remembered facts matched."

    lines = [f"Remembered facts ({len(facts)}):"]
    for fact in facts:
        revised = fact.get("revisions")
        revision_note = f", revisions={len(revised)}" if isinstance(revised, list) and revised else ""
        changed = fact.get("updated_at") or fact.get("added_at") or "unknown"
        metadata = []
        if fact.get("key"):
            metadata.append(f"key={fact['key']}")
        metadata.append(f"confidence={_normalize_confidence(fact.get('confidence')):.2f}")
        metadata.append(f"source={_normalize_source(fact.get('source'))}")
        lines.append(
            f"- {fact.get('id', '?')} [{fact.get('category', 'general')}] "
            f"{fact.get('content', '')} (changed={changed}, {', '.join(metadata)}{revision_note})"
        )
    return "\n".join(lines)


def find_contradiction_candidates(
    category: str,
    content: str,
    *,
    threshold: float = 0.45,
) -> list[dict[str, Any]]:
    """Find review-only lexical conflicts among legacy facts without semantic keys."""
    category = (category or "general").strip().lower() or "general"
    incoming = {
        token for token in _FACT_WORD_RE.findall((content or "").lower())
        if token not in _CONTRADICTION_STOP_WORDS
    }
    if len(incoming) < 2:
        return []
    threshold = max(0.2, min(0.9, float(threshold)))
    matches: list[tuple[float, dict[str, Any]]] = []
    for fact in list_facts():
        if fact.get("key") or str(fact.get("category") or "").lower() != category:
            continue
        existing_content = str(fact.get("content") or "")
        if existing_content.strip().lower() == (content or "").strip().lower():
            continue
        existing = {
            token for token in _FACT_WORD_RE.findall(existing_content.lower())
            if token not in _CONTRADICTION_STOP_WORDS
        }
        union = incoming | existing
        score = len(incoming & existing) / len(union) if union else 0.0
        if score >= threshold:
            candidate = dict(fact)
            candidate["similarity"] = round(score, 2)
            matches.append((score, candidate))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in matches[:10]]


def describe_contradiction_candidates(category: str, content: str) -> str:
    candidates = find_contradiction_candidates(category, content)
    if not candidates:
        return "No possible unkeyed contradictions found."
    lines = ["Possible contradictions for review; no memory was changed:"]
    for fact in candidates:
        lines.append(
            f"- {fact.get('id')} [{fact.get('category')}] {fact.get('content')} "
            f"(similarity={fact.get('similarity'):.2f})"
        )
    return "\n".join(lines)


def render_memory_block(query: str = "") -> str:
    """Return a human-readable text block for injection into the system prompt.

    To keep the system prompt from bloating as memory grows, only the most
    recently added facts are injected once the total exceeds MEMORY_MAX_FACTS
    (default 60; set to 0 to disable the cap). Older facts stay on disk and are
    still searchable - they're just not all force-fed into every prompt.
    """
    facts = list_facts()
    if facts:
        try:
            from brain.knowledge_vault import ensure_profile_note

            ensure_profile_note(facts)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not initialize Obsidian memory mirror: %s", exc)

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

    # During real turns, prefer a small relevant subset plus the newest facts.
    # Calling without a query preserves the full diagnostic/UI representation.
    if query and facts:
        from brain.knowledge_vault import _tokens

        query_tokens = set(_tokens(query))
        try:
            relevant_limit = max(1, min(30, int(os.getenv("MEMORY_RELEVANT_FACTS", "12"))))
        except (TypeError, ValueError):
            relevant_limit = 12
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, fact in enumerate(facts):
            fact_tokens = set(_tokens(f"{fact.get('category', '')} {fact.get('content', '')}"))
            scored.append((len(query_tokens & fact_tokens), index, fact))
        relevant = [item for item in scored if item[0] > 0]
        relevant.sort(key=lambda item: (-item[0], -item[1]))
        selected = relevant[:relevant_limit]
        # Keep a tiny recency safety net for durable preferences that use
        # different wording than the current request.
        selected_ids = {item[2].get("id") for item in selected}
        for index, fact in reversed(list(enumerate(facts))):
            if len(selected) >= relevant_limit or fact.get("id") in selected_ids:
                continue
            selected.append((0, index, fact))
            selected_ids.add(fact.get("id"))
            if sum(1 for item in selected if item[0] == 0) >= 3:
                break
        facts = [item[2] for item in sorted(selected, key=lambda item: item[1])]

    by_category: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        by_category.setdefault(fact.get("category", "general"), []).append(fact)

    lines: list[str] = []
    for category in sorted(by_category):
        lines.append(f"## {category}")
        for fact in by_category[category]:
            content = fact.get("content", "")
            fact_id = fact.get("id", "")
            metadata = []
            if fact.get("key"):
                metadata.append(f"key={fact['key']}")
            confidence = _normalize_confidence(fact.get("confidence"))
            if confidence < 1.0:
                metadata.append(f"confidence={confidence:.2f}")
            source = _normalize_source(fact.get("source"))
            if source != "conversation":
                metadata.append(f"source={source}")
            suffix = f" [{' | '.join(metadata)}]" if metadata else ""
            lines.append(f"- ({fact_id}) {content}{suffix}")
        lines.append("")

    block = "\n".join(lines).strip()
    if truncated:
        block += (
            f"\n\n(Showing the {max_facts} most recent of {total} "
            f"remembered facts.)"
        )
    if query:
        try:
            from brain.knowledge_vault import render_vault_context

            vault_block = render_vault_context(query)
            if vault_block:
                block = f"{block}\n\n{vault_block}" if block else vault_block
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not retrieve Obsidian context: %s", exc)
    return block
