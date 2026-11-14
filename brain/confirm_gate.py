"""Session-bound, human-only approval for side-effecting actions.

Two entry points:

- ``act``: an action Nick asked for in this turn (most tools since
  2026-11-04). It runs at once when the speaker is trusted
  (``brain.voice_trust``: an authenticated channel, or a room voice that
  counts as Nick); for a guest voice it falls back to ``propose``.
- ``propose``: an action that must wait for Nick's own next message, used
  for fixes JARVIS suggests on its own and for every real-money order
  (``brain.trading``, ``money=True``). Same rules as the arr restart gate in
  ``brain.arr_stack`` (which keeps its own tuned implementation).

Rules for proposals:

- Several open proposals per session, each with its own code, valid for
  ``_TTL_S``. A turn that stages two fixes keeps both codes alive.
- A bare "yes" approves every proposal staged since Nick's last message, and
  only as the very next message; every message disarms it.
- "approve <code>" approves any time before expiry, and one message may
  carry several ("approve 1631, approve 5774"). The verb differs from the
  restart gate's "confirm <code>" so the two never answer each other.
- "deny <code>" (or decline/reject/cancel <code>) drops just that proposal.
  The companion's Approve and Deny buttons send exactly these messages
  (``pending`` lists what they show), so a tap is the same human-only path
  as typing it.
- A leading wake phrase ("Hey Jarvis, yes") is ignored: voice transcripts
  often keep it.
- The proposal is consumed before the action runs, so a failure or a
  repeated "yes" can never run it twice.
- A money proposal (``money=True``) is approved only where
  ``voice_trust.may_move_money()``: the app, or a room voice that matched
  Nick's voiceprint. "Voice ID off" isn't enough; the order stays open
  for the app.

``conversation_action`` runs on the original user text at the agent boundary,
never on tool arguments, so the model cannot approve its own proposal; a
guest's message only disarms (``brain/agent._arr_stack_action``).
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
_CANCEL = frozenset({"cancel", "no", "nope", "nevermind", "never mind", "not now", "forget it", "nothing",
                     "stop", "don't", "dont", "no thanks", "no thank you", "cancel it", "cancel that"})
_DENY_WORDS = frozenset({"deny", "decline", "reject", "cancel"})


class Proposal(NamedTuple):
    created: float
    code: str
    description: str
    run: Callable[[], str]
    money: bool = False


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


def propose(session_id: str | None, description: str, run: Callable[[], str], *, money: bool = False) -> str:
    """Open a proposal for this session; return the prompt for Nick.
    ``money``: a real order; only ``voice_trust.may_move_money`` approves it."""
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
        live[code] = Proposal(now, code, description, run, money)
        _pending[session_id] = live
        armed = [c for c in _armed.get(session_id, []) if c in live]
        _armed[session_id] = armed + [code]
    return (f'Proposed: {description}. Reply "yes" now, or "approve {code}" '
            f"within {_TTL_S // 60} minutes, sir. Nothing has been changed yet.")


def act(session_id: str | None, description: str, run: Callable[[], str]) -> str:
    """Run an action Nick asked for right away (2026-11-04: he hates the
    "approve 4033" round trip). A guest voice still only gets a proposal,
    which only Nick can approve. Use ``propose`` for fixes JARVIS suggests
    on its own."""
    from brain import voice_trust
    if not voice_trust.trusted():
        return propose(session_id, description, run)
    log.info("Running requested action for session %s: %s", session_id, description)
    try:
        return run()
    except Exception as exc:  # noqa: BLE001
        log.exception("Requested action failed: %s", description)
        return f"That failed ({exc}), sir. Nothing will retry it automatically."


def awaiting_yes(session_id: str | None) -> bool:
    """True when a bare "yes" as Nick's next message would approve something."""
    if not session_id:
        return False
    with _lock:
        live = _live(session_id, time.monotonic())
        return any(code in live for code in _armed.get(session_id, []))


def reply_kind(text: str) -> str | None:
    """How ``conversation_action`` would read this message while a "yes" is
    armed: "yes", "cancel", "codes" ("approve 1631" / "deny 1631") or None
    (anything else, which only disarms). For front ends with their own fast
    paths (the room voice), so an answer to a proposal reaches the gate."""
    normalized = _normalize(text)
    if normalized == "cancel fix" or normalized in _CANCEL:
        return "cancel"
    if normalized in _YES:
        return "yes"
    if _codes(normalized) is not None or _denied(normalized) is not None:
        return "codes"
    return None


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


def pending(session_id: str | None) -> list[dict[str, object]]:
    """Open proposals for this session, oldest first, for the companion's
    Approve/Deny buttons: [{"code", "description", "expires_in"}]."""
    if not session_id:
        return []
    with _lock:
        now = time.monotonic()
        live = _live(session_id, now)
        return [{"code": p.code, "description": p.description,
                 "expires_in": max(0, int(_TTL_S - (now - p.created)))} for p in live.values()]


def _denied(normalized: str) -> list[str] | None:
    """Codes in "deny 1631" / "decline 1631, 5774", else None."""
    words = [w for w in re.split(r"[\s,.;]+", normalized) if w]
    if len(words) < 2 or words[0] not in _DENY_WORDS:
        return None
    codes = [w for w in words[1:] if _CODE_WORD_RE.fullmatch(w)]
    rest = [w for w in words[1:] if w not in codes and w not in _CODE_FILLER]
    return codes if codes and not rest else None


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
    denied = _denied(normalized)
    if denied is not None:
        with _lock:
            current = _pending.get(session_id, {})
            dropped = [current.pop(c) for c in dict.fromkeys(denied) if c in current]
            if not current:
                _pending.pop(session_id, None)
        if not dropped:
            return f"Code {', '.join(denied)} does not match anything open, sir. Nothing was changed."
        log.info("Denied for session %s: %s", session_id, "; ".join(p.description for p in dropped))
        return f"Denied: {'; '.join(p.description for p in dropped)}. Nothing was done, sir."
    codes = _codes(normalized)
    if codes is None:
        if not (armed and normalized in _YES):
            return None
        codes = [c for c in armed if c in live]
    unknown = [c for c in codes if c not in live]
    from brain import voice_trust
    held = [] if voice_trust.may_move_money() else [c for c in codes if c in live and live[c].money]
    if held:  # a real order heard on an unconfirmed room voice: it stays open for the app
        log.info("Money approval needs the app or a voiceprint match for session %s (%s)",
                 session_id, voice_trust.current().reason)
    chosen: list[Proposal] = []
    with _lock:
        current = _pending.get(session_id, {})
        for code in dict.fromkeys(codes):
            if code in held:
                continue
            proposal = current.pop(code, None)
            if proposal is not None and live.get(code) is proposal:
                chosen.append(proposal)
        if not current:
            _pending.pop(session_id, None)
    replies = [_run(p, session_id) for p in chosen]
    if held:
        replies.append(voice_trust.MONEY_REFUSAL_LINE + " Nothing was sent to Schwab.")
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
