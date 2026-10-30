"""Session-bound, human-only approval for side-effecting fixes.

Same rules as the arr restart gate in ``brain.arr_stack`` (which keeps its own
tuned implementation): a tool may only *propose* an action; it runs only after
a message Nick himself sends.

- Several open proposals per session, each with its own code, valid for
  ``_TTL_S``. A turn that stages two fixes keeps both codes alive.
- A bare "yes" approves every proposal staged since Nick's last message, and
  only as the very next message; every message disarms it.
- "approve <code>" approves any time before expiry, and one message may
  carry several ("approve 1631, approve 5774"). The verb differs from the
  restart gate's "confirm <code>" so the two never answer each other.
- A leading wake phrase ("Hey Jarvis, yes") is ignored: voice transcripts
  often keep it.
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
_CODE_WORD_RE = re.compile(r"\d{4}")
_CODE_FILLER = frozenset({"approve", "and", "code", "codes"})
_WAKE_RE = re.compile(r"^(?:(?:hey|hi|ok|okay)[ ,]+)?jarvis[ ,.!]*")
_YES = frozenset({"yes", "y", "yeah", "yep", "yup", "yes please", "yes sir", "yes do it", "do it",
                  "go", "go ahead", "proceed", "ok", "okay", "approve", "approved",
                  "approve both", "approve all", "yes to both", "both", "do both"})
_CANCEL = frozenset({"cancel", "no", "nope", "nevermind", "never mind", "not now"})


class Proposal(NamedTuple):
    created: float
    code: str
    description: str
    run: Callable[[], str]


_lock = threading.Lock()
# session -> {code: proposal}, oldest first.
_pending: dict[str, dict[str, Proposal]] = {}
# session -> codes a bare "yes" would approve (staged since Nick's last message).
_armed: dict[str, list[str]] = {}


def _live(session_id: str, now: float) -> dict[str, Proposal]:
    """Caller holds _lock. Drops expired entries as a side effect."""
    for key in list(_pending):
        alive = {code: p for code, p in _pending[key].items() if now - p.created <= _TTL_S}
        if alive:
            _pending[key] = alive
        else:
            _pending.pop(key, None)
    return _pending.get(session_id, {})


def propose(session_id: str | None, description: str, run: Callable[[], str]) -> str:
    """Open a proposal for this session; return the prompt for Nick."""
    if not session_id:
        return "Refused: a conversation session is required to approve fixes, sir."
    with _lock:
        now = time.monotonic()
        live = _live(session_id, now)
        # Re-staging the same action replaces its old code instead of
        # leaving two live codes for one fix.
        for code, old in list(live.items()):
            if old.description == description:
                live.pop(code)
        code = f"{secrets.randbelow(10000):04d}"
        while code in live:
            code = f"{secrets.randbelow(10000):04d}"
        live[code] = Proposal(now, code, description, run)
        _pending[session_id] = live
        armed = [c for c in _armed.get(session_id, []) if c in live]
        _armed[session_id] = armed + [code]
    return (f'Proposed: {description}. Reply "yes" now, or "approve {code}" '
            f"within {_TTL_S // 60} minutes, sir. Nothing has been changed yet.")


def pending_code(session_id: str | None) -> str | None:
    """The newest open proposal's approval code (for non-chat clients)."""
    if not session_id:
        return None
    with _lock:
        live = _live(session_id, time.monotonic())
    return list(live)[-1] if live else None


def pending_description(session_id: str | None) -> str | None:
    if not session_id:
        return None
    with _lock:
        live = _live(session_id, time.monotonic())
    return list(live.values())[-1].description if live else None


def _normalize(text: str) -> str:
    normalized = " ".join(text.lower().strip().split()).rstrip(".!?")
    return _WAKE_RE.sub("", normalized).strip().rstrip(".!?")


def _codes(normalized: str) -> list[str] | None:
    """Every code in a message made only of codes ("approve 1631, 5774"), else None."""
    words = re.split(r"[\s,.;]+", normalized)
    codes = [w for w in words if _CODE_WORD_RE.fullmatch(w)]
    filler = [w for w in words if w and w not in codes and w not in _CODE_FILLER]
    return codes if codes and not filler else None


def _run(proposal: Proposal, session_id: str) -> str:
    log.info("Approved fix for session %s: %s", session_id, proposal.description)
    try:
        return proposal.run()
    except Exception as exc:  # noqa: BLE001
        log.exception("Approved fix failed: %s", proposal.description)
        return f"The fix failed ({exc}). It will not be retried automatically, sir."


def conversation_action(text: str, session_id: str, *, handled: bool = False) -> str | None:
    """Approve, cancel or ignore Nick's message. ``handled`` only disarms."""
    normalized = _normalize(text)
    with _lock:
        armed = _armed.pop(session_id, [])
        live = dict(_live(session_id, time.monotonic()))
    if handled or not live:
        return None
    if normalized == "cancel fix" or (armed and normalized in _CANCEL):
        with _lock:
            _pending.pop(session_id, None)
        what = "; ".join(p.description for p in live.values())
        return f"Cancelled: {what}. Nothing was changed, sir."
    codes = _codes(normalized)
    if codes is None:
        if not (armed and normalized in _YES):
            return None
        codes = [c for c in armed if c in live]
    unknown = [c for c in codes if c not in live]
    chosen: list[Proposal] = []
    with _lock:
        current = _pending.get(session_id, {})
        for code in dict.fromkeys(codes):
            proposal = current.pop(code, None)
            if proposal is not None and live.get(code) is proposal:
                chosen.append(proposal)
        if not current:
            _pending.pop(session_id, None)
    replies = [_run(p, session_id) for p in chosen]
    if unknown:
        still = [p for c, p in live.items() if c not in codes]
        hint = " ".join(f'Reply "approve {p.code}" to {p.description}.' for p in still)
        replies.append(f"Code {', '.join(unknown)} does not match anything open, sir. {hint}".strip()
                       + (" Nothing else was changed." if not chosen else ""))
    return " ".join(replies) if replies else None


def clear_for_tests() -> None:
    with _lock:
        _pending.clear()
        _armed.clear()
