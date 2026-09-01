"""Reviewable staging area for inferred long-term memories.

Extraction may suggest a durable fact, but only explicit approval of the exact
proposal ID can write it into the authoritative profile memory.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brain.memory import remember_fact
from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROPOSAL_FILE = _PROJECT_ROOT / "memory" / "memory_proposals.json"
_CATEGORIES = {"person", "preference", "routine", "project", "goal", "general"}
_MAX_PROPOSALS = 100


def _load() -> dict[str, Any]:
    if not _PROPOSAL_FILE.exists():
        return {"proposals": []}
    try:
        data = json.loads(_PROPOSAL_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("proposals"), list):
            return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not load memory proposals: %s", exc)
    return {"proposals": []}


def _save(data: dict[str, Any]) -> bool:
    try:
        _PROPOSAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = _PROPOSAL_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_PROPOSAL_FILE)
        return True
    except OSError as exc:
        log.error("Could not save memory proposals: %s", exc)
        return False


def propose_memory_fact(
    category: str,
    content: str,
    *,
    key: str = "",
    confidence: float = 0.7,
    evidence: str = "",
) -> str:
    """Stage one bounded fact candidate without changing durable memory."""
    category = (category or "general").strip().lower()
    content = " ".join((content or "").split())[:500]
    key = " ".join((key or "").split())[:120]
    evidence = " ".join((evidence or "").split())[:500]
    if category not in _CATEGORIES:
        return f"Refused: category must be one of {', '.join(sorted(_CATEGORIES))}."
    if not content:
        return "Refused: empty memory proposal."
    try:
        confidence = round(max(0.0, min(1.0, float(confidence))), 2)
    except (TypeError, ValueError):
        return "Refused: invalid confidence."

    data = _load()
    for item in data["proposals"]:
        if item.get("status") == "pending" and item.get("content") == content:
            return f"Already proposed as {item.get('id')}: {content}"
    proposal_id = uuid.uuid4().hex[:8]
    data["proposals"].append({
        "id": proposal_id,
        "category": category,
        "content": content,
        "key": key,
        "confidence": confidence,
        "evidence": evidence,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    data["proposals"] = data["proposals"][-_MAX_PROPOSALS:]
    if not _save(data):
        return "Failed to stage memory proposal."
    return (
        f"Memory proposal {proposal_id}: [{category}] {content} "
        "Review it, then explicitly approve this exact ID before applying."
    )


def describe_memory_proposals(status: str = "pending") -> str:
    status = (status or "pending").strip().lower()
    if status not in {"pending", "applied", "rejected", "all"}:
        return "Invalid proposal status."
    items = _load()["proposals"]
    if status != "all":
        items = [item for item in items if item.get("status") == status]
    if not items:
        return f"No {status} memory proposals."
    lines = [f"Memory proposals ({len(items)}):"]
    for item in items[-30:]:
        lines.append(
            f"- {item.get('id')} [{item.get('status')}/{item.get('category')}] "
            f"{item.get('content')} (confidence={item.get('confidence')})"
        )
    return "\n".join(lines)


def apply_memory_proposal(proposal_id: str, *, confirmed: bool = False) -> str:
    """Apply one exact pending proposal after explicit confirmation."""
    proposal_id = (proposal_id or "").strip()
    if not confirmed:
        return "Confirmation required before applying that memory proposal."
    data = _load()
    proposal = next((item for item in data["proposals"] if item.get("id") == proposal_id), None)
    if proposal is None:
        return f"No memory proposal found with id {proposal_id}."
    if proposal.get("status") != "pending":
        return f"Memory proposal {proposal_id} is already {proposal.get('status')}."
    result = remember_fact(
        str(proposal.get("category") or "general"),
        str(proposal.get("content") or ""),
        key=str(proposal.get("key") or ""),
        confidence=proposal.get("confidence"),
        source=f"approved_proposal:{proposal_id}",
    )
    if result.startswith(("Failed", "Refused")):
        return result
    proposal["status"] = "applied"
    proposal["applied_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not _save(data):
        return "The fact was remembered, but proposal status could not be updated."
    return f"Applied memory proposal {proposal_id}. {result}"


def reject_memory_proposal(proposal_id: str) -> str:
    """Reject a pending proposal without touching durable memory."""
    proposal_id = (proposal_id or "").strip()
    data = _load()
    proposal = next((item for item in data["proposals"] if item.get("id") == proposal_id), None)
    if proposal is None:
        return f"No memory proposal found with id {proposal_id}."
    if proposal.get("status") != "pending":
        return f"Memory proposal {proposal_id} is already {proposal.get('status')}."
    proposal["status"] = "rejected"
    proposal["rejected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"Rejected memory proposal {proposal_id}." if _save(data) else "Failed to reject proposal."
