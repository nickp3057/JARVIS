"""Session-bound, human-only approval for side-effecting fixes.

Same rules as the arr restart gate in ``brain.arr_stack`` (which keeps its own
tuned implementation): a tool may only *propose* an action; it runs only after
a message Nick himself sends.

- One open proposal per session, valid for ``_TTL_S``.
- A bare "yes" approves only as the very next message after the prompt;
  every message disarms it.
- "approve <code>" approves any time before expiry. The verb differs from
  the restart gate's "confirm <code>" so the two never answer each other.
- The proposal is consumed before the action runs, so a failure or a
  repeated "yes" can never run it twice.

``conversation_action`` runs on the original user text at the agent boundary,
never on tool arguments, so the model cannot approve its own proposal.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Callable, NamedTuple

from jarvis_logger import get_logger

log = get_logger(__name__)

_TTL_S = 600
_CODE_RE = re.compile(r"(?:approve )?(\d{4})")
_YES = frozenset({"yes", "y", "yeah", "yep", "yup", "yes please", "yes sir", "yes do it", "do it",
                  "go", "go ahead", "proceed", "ok", "okay", "approve", "approved"})
_CANCEL = frozenset({"cancel", "no", "nope", "nevermind", "never mind", "not now"})


class Proposal(NamedTuple):
    created: float
    code: str
    description: str
    run: Callable[[], str]


_lock = threading.Lock()
_pending: dict[str, Proposal] = {}
_armed: set[str] = set()


def _live(session_id: str, now: float) -> Proposal | None:
    """Caller holds _lock. Drops expired entries as a side effect."""
    for key, proposal in list(_pending.items()):
        if now - proposal.created > _TTL_S:
            _pending.pop(key, None)
    return _pending.get(session_id)


def propose(session_id: str | None, description: str, run: Callable[[], str]) -> str:
    """Open (or replace) this session's proposal; return the prompt for Nick."""
    if not session_id:
        return "Refused: a conversation session is required to approve fixes, sir."
    with _lock:
        proposal = Proposal(time.monotonic(), f"{secrets.randbelow(10000):04d}", description, run)
        _pending[session_id] = proposal
        _armed.add(session_id)
    return (f'Proposed: {description}. Reply "yes" now, or "approve {proposal.code}" '
            f"within {_TTL_S // 60} minutes, sir. Nothing has been changed yet.")


def pending_code(session_id: str | None) -> str | None:
    """The open proposal's approval code (for non-chat clients that show it)."""
    if not session_id:
        return None
    with _lock:
        proposal = _live(session_id, time.monotonic())
    return proposal.code if proposal else None


def pending_description(session_id: str | None) -> str | None:
    if not session_id:
        return None
    with _lock:
        proposal = _live(session_id, time.monotonic())
    return proposal.description if proposal else None


def conversation_action(text: str, session_id: str, *, handled: bool = False) -> str | None:
    """Approve, cancel or ignore Nick's message. ``handled`` only disarms."""
    normalized = " ".join(text.lower().strip().split()).rstrip(".!?")
    with _lock:
        armed = session_id in _armed
        _armed.discard(session_id)
        proposal = _live(session_id, time.monotonic())
    if handled or proposal is None:
        return None
    if normalized == "cancel fix" or (armed and normalized in _CANCEL):
        with _lock:
            _pending.pop(session_id, None)
        return f"Cancelled: {proposal.description}. Nothing was changed, sir."
    match = _CODE_RE.fullmatch(normalized)
    if match is None and not (armed and normalized in _YES):
        return None
    if match is not None and match[1] != proposal.code:
        return (f'That code does not match. Reply "approve {proposal.code}" to {proposal.description}, '
                "sir. Nothing was changed.")
    with _lock:
        if _pending.get(session_id) is not proposal:
            return None
        _pending.pop(session_id, None)
    log.info("Approved fix for session %s: %s", session_id, proposal.description)
    try:
        return proposal.run()
    except Exception as exc:  # noqa: BLE001
        log.exception("Approved fix failed: %s", proposal.description)
        return f"The fix failed ({exc}). It will not be retried automatically, sir."


def clear_for_tests() -> None:
    with _lock:
        _pending.clear()
        _armed.clear()
