"""Reflection after failed turns: JARVIS improves without being told twice.

After every trusted turn the agent hands the exchange here (``observe_turn``).
Cheap heuristics in code decide whether a turn went badly; only then does a
background thread spend one call on the cheap extraction model to boil the
failure down to a one-line lesson, stored by ``brain/lessons.py``.

Signals (see ``detect``):

- ``correction``: Nick's new message pushes back on the previous reply
  ("no, the bedroom", "that's wrong", "not that", "I said...", "that didn't
  work"). A bare "no" that answers JARVIS's own question is not one.
- ``repeat``: Nick says nearly the same thing again within two turns and a
  few minutes, so the first attempt evidently missed.
- ``false_success``: a tool this turn returned an error or an outage phrase
  (``tool_health.looks_like_failure``) and the reply never admitted it.

The first two judge the *previous* turn, using Nick's new message as the
evidence; the third judges the turn just finished. Explicit behaviour rules
("stop doing X") stay with the memory keeper's standing-orders path; the
reflector is told the orders in force and returns no lesson for those.

Never blocks a turn; records usage as ``lessons``; switched off with
``LESSONS_ENABLED=false``.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from brain import lessons, model_select, standing_orders, tool_health
from jarvis_logger import get_logger

log = get_logger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
REPEAT_WINDOW_S = 300.0
_REPEAT_SIMILARITY = 0.7
_MAX_INFLIGHT = 2

_STRONG_CORRECTION = re.compile(
    r"^(?:no[,.!]?\s+)?(?:that'?s|that is|thats|it'?s|you'?re|you are)\s+(?:wrong|not right|incorrect|not what i (?:asked|said|meant|wanted))"
    r"|\bnot that one\b|^not that\b|^wrong\b"
    r"|\bi (?:said|asked for|meant|told you)\b"
    r"|\b(?:that|it) (?:didn'?t|did not|doesn'?t|does not) work\b"
    r"|^(?:that|it) didn'?t\b"
    r"|\bstill (?:not|isn'?t|didn'?t|doesn'?t)\b"
    r"|\byou (?:didn'?t|did not|got it wrong|misheard|misunderstood)\b"
    r"|\bthat'?s not what i\b|\bnot what i (?:asked|said|meant|wanted)\b"
    r"|\bwrong (?:one|room|light|song|show|movie|device|thing|answer)\b"
)
_WEAK_NO = re.compile(r"^(?:no|nope|nah)\b[,.!]?\s*(.*)$")
_POLITE_NO = re.compile(
    r"^(?:no|nope|nah)[,.!]?\s*(?:thanks|thank you|problem|worries|rush|need|that'?s (?:all|it|fine|ok|okay)"
    r"|i'?m good|all good|it'?s fine|that'?s fine|never ?mind|not now|not yet|not today|later)?\b[.!]?\s*$"
)
_ADMITS_FAILURE = re.compile(
    r"\b(?:couldn'?t|could not|can'?t|cannot|failed|failing|error|unable|problem|trouble|isn'?t responding"
    r"|not responding|unreachable|offline|is down|are down|sorry|unfortunately|didn'?t|did not|wasn'?t|was not|no luck"
    r"|not configured|timed out|refused|apolog)"
)
_TOOL_ERROR = re.compile(r"^\s*(?:error|tool error|failed|exception)\b|\berror:", re.IGNORECASE)


@dataclass
class Turn:
    user: str
    reply: str
    tools: list[tuple[str, str]] = field(default_factory=list)  # (tool name, result text)
    at: float = field(default_factory=time.time)


_recent: dict[str, deque[Turn]] = {}
_recent_lock = threading.Lock()
_inflight = threading.BoundedSemaphore(_MAX_INFLIGHT)


def _model() -> str:
    raw = os.getenv("LESSONS_MODEL", "") or os.getenv("MEMORY_EXTRACT_MODEL", "") or "auto:haiku"
    return model_select.resolve(raw, _DEFAULT_MODEL)


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().replace("’", "'").split())


# --- Heuristics --------------------------------------------------------------

def is_correction(user_text: str, previous_reply: str = "") -> bool:
    """Does this message push back on JARVIS's last reply?"""
    text = _norm(user_text)
    if not text:
        return False
    if _STRONG_CORRECTION.search(text):
        return True
    if _POLITE_NO.match(text):
        return False
    weak = _WEAK_NO.match(text)
    if not weak:
        return False
    # "No, the bedroom ones" corrects; "no" answering JARVIS's own question doesn't.
    asked = previous_reply.strip().endswith("?")
    return bool(weak.group(1).strip()) and not asked


def is_repeat(user_text: str, earlier: str) -> bool:
    text, other = _norm(user_text), _norm(earlier)
    if len(lessons._words(text)) < 2 or len(lessons._words(other)) < 2:
        return False
    return lessons.similarity(text, other) >= _REPEAT_SIMILARITY


def failed_tools(turn: Turn) -> list[tuple[str, str]]:
    return [(name, result) for name, result in turn.tools
            if tool_health.looks_like_failure(result) or _TOOL_ERROR.search(result or "")]


def claims_success_despite(turn: Turn) -> bool:
    """A tool failed and the reply never said so."""
    return bool(failed_tools(turn)) and not _ADMITS_FAILURE.search(_norm(turn.reply))


def detect(previous: list[Turn], current: Turn, now: float | None = None) -> tuple[str, Turn] | None:
    """(signal, the turn that went badly), or None when nothing looks wrong."""
    now = current.at if now is None else now
    last = previous[-1] if previous else None
    if last is not None and now - last.at <= REPEAT_WINDOW_S and is_correction(current.user, last.reply):
        return "correction", last
    for earlier in reversed(previous[-2:]):
        if now - earlier.at <= REPEAT_WINDOW_S and is_repeat(current.user, earlier.user):
            return "repeat", earlier
    if claims_success_despite(current):
        return "false_success", current
    return None


# --- Model call --------------------------------------------------------------

_LESSON_TOOL: dict[str, Any] = {
    "name": "record_lesson",
    "description": "Record the one lesson JARVIS should take from this failed exchange, or none.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lesson": {
                "type": "string",
                "description": (
                    "One short imperative sentence JARVIS should follow next time, specific enough "
                    "to act on. Empty when there is no real lesson."
                ),
            },
            "tool": {"type": "string", "description": "The tool most involved, or empty."},
            "situation": {"type": "string", "description": "A few words on when this applies."},
        },
        "required": ["lesson"],
    },
}

_SYSTEM_PROMPT = """You are JARVIS's self-reflection subsystem. A cheap heuristic flagged
an exchange between Nick and JARVIS (his AI assistant) as possibly having gone
badly. Decide whether JARVIS made a mistake it can avoid next time, and if so
write ONE lesson.

Good lessons are concrete and about JARVIS's own behaviour: which device,
tool, argument, or phrasing to use; what to check before claiming success;
what Nick actually means by a phrase. Examples:
- "When Nick says 'the lamp' he means light.desk_lamp, not light.floor_lamp."
- "After control_device reports an error, say it failed; never answer 'Done'."
- "Search Radarr by the year as well when a title has remakes."

Return an empty lesson when:
- the flag is a false alarm (Nick was answering a question, changing his mind,
  or simply asking again for something new);
- Nick stated a general rule for how JARVIS should behave from now on ("stop
  doing X", "always Y") - that is recorded as a standing order elsewhere;
- the failure was outside JARVIS's control and the reply was honest about it;
- an existing lesson or standing order already covers it (reuse an existing
  lesson's exact wording instead to reinforce it).
Never record facts about Nick's life; only how JARVIS should act."""


def _call_model(client: Any, signal: str, bad: Turn, follow_up: str) -> dict[str, Any]:
    tools = "\n".join(f"- {name} -> {result.strip()[:400]}" for name, result in bad.tools) or "(none)"
    existing = "\n".join(f"- {i['text']}" + (f" [{i['tool']}]" if i.get("tool") else "")
                         for i in lessons.all_lessons()[:15]) or "(none)"
    signal_text = {
        "correction": "Nick's next message pushed back on the reply.",
        "repeat": "Nick repeated or rephrased the request shortly after.",
        "false_success": "A tool returned an error but the reply did not say so.",
    }.get(signal, signal)
    prompt = (
        f"Flag: {signal_text}\n\n"
        f"Standing orders already in force:\n{standing_orders.render_block() or '(none)'}\n\n"
        f"Existing lessons:\n{existing}\n\n"
        f"Exchange:\nNick: {bad.user.strip()[:2000]}\n"
        f"Tools JARVIS ran:\n{tools}\n"
        f"JARVIS: {bad.reply.strip()[:1500]}\n"
        + (f"\nNick then said: {follow_up.strip()[:1000]}\n" if follow_up else "")
    )
    response = client.messages.create(
        model=_model(),
        max_tokens=300,
        system=_SYSTEM_PROMPT,
        tools=[_LESSON_TOOL],
        tool_choice={"type": "tool", "name": "record_lesson"},
        messages=[{"role": "user", "content": prompt}],
    )
    from brain import usage as _usage
    _usage.record_response("lessons", response)
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            payload = getattr(block, "input", None)
            return dict(payload) if isinstance(payload, dict) else {}
    return {}


def reflect(client: Any, signal: str, bad: Turn, follow_up: str = "") -> str:
    """Synchronous reflection; returns the lessons-store result or ""."""
    try:
        payload = _call_model(client, signal, bad, follow_up)
    except Exception as exc:  # noqa: BLE001
        log.warning("Reflection call failed: %s", exc)
        return ""
    text = str(payload.get("lesson") or "").strip()
    if not text:
        log.info("Reflection on a %s turn found no lesson.", signal)
        return ""
    tool = str(payload.get("tool") or "").strip()
    if not tool and len(bad.tools) == 1:
        tool = bad.tools[0][0]
    result = lessons.learn(text, tool=tool, situation=payload.get("situation") or "", signal=signal)
    try:
        from brain import memory_extractor
        memory_extractor._notify("Lesson learned", text)
    except Exception:  # noqa: BLE001
        pass
    return result


# --- Entry point -------------------------------------------------------------

def observe_turn(
    client: Any,
    session_id: str,
    user_text: str,
    reply: str,
    tool_results: list[tuple[str, str]] | None = None,
    *,
    background: bool = True,
) -> str | None:
    """Remember the turn; if it (or the one before) went badly, reflect.

    Returns the detected signal (or None). The model call runs on a daemon
    thread unless ``background`` is False (tests)."""
    if not lessons.enabled():
        return None
    current = Turn(str(user_text or ""), str(reply or ""), [(str(n), str(r or "")) for n, r in (tool_results or [])])
    with _recent_lock:
        history = _recent.setdefault(session_id or "", deque(maxlen=3))
        previous = list(history)
        history.append(current)
    found = detect(previous, current)
    if found is None:
        return None
    signal, bad = found
    follow_up = "" if bad is current else current.user
    log.info("Reflector flagged a %s turn: %r", signal, bad.user[:80])
    if client is None:
        return signal
    if not background:
        reflect(client, signal, bad, follow_up)
        return signal
    if not _inflight.acquire(blocking=False):
        log.info("Reflector busy; skipping this one.")
        return signal

    def run() -> None:
        try:
            reflect(client, signal, bad, follow_up)
        finally:
            _inflight.release()

    threading.Thread(target=run, name="jarvis-reflector", daemon=True).start()
    return signal


def reset() -> None:
    """Forget the recent-turn buffer (tests, session switches)."""
    with _recent_lock:
        _recent.clear()
