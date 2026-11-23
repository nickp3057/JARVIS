"""Automatic memory: learn from every exchange without being asked.

This is the behaviour Nick knows from ChatGPT and Claude - say something about
your life in passing and the assistant simply knows it next time. After each
completed turn the agent hands the exchange to a background worker here. The
worker:

1. records the turn in the habit log (``brain/habits.py``), then
2. asks a small, cheap model to compare the exchange against what is already
   remembered and return zero or more durable facts plus an optional one-line
   journal entry, then
3. writes confident facts straight into profile memory (revising a superseded
   fact in place when the model names one), stages low-confidence guesses as
   reviewable proposals, and appends the journal line to today's Obsidian
   daily note.

Everything runs off the conversation thread, so a slow or failed extraction
never delays JARVIS's reply. Listeners (the runtime) are told whenever memory
actually changes so the cockpit can show "Memory updated".
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Any, Callable

from brain import habits
from brain.knowledge_vault import append_journal_entry
from brain.memory import list_facts, remember_fact, update_fact
from brain.memory_proposals import propose_memory_fact
from brain import standing_orders
from brain import model_select
from jarvis_logger import get_logger

log = get_logger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_EXISTING_CHARS = 6000
_CORRECTION_THRESHOLD = 0.85
_CATEGORIES = ("person", "preference", "routine", "project", "goal", "general")
_ERROR_REPLY_MARKERS = ("I ran into a problem reaching my reasoning", "I got tangled up")

Listener = Callable[[str, str], None]

_queue: "queue.Queue[tuple[Any, str, str, list[tuple[str, dict[str, Any]]]]]" = queue.Queue(maxsize=50)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_stop = threading.Event()
_listeners: list[Listener] = []

_EXTRACT_TOOL: dict[str, Any] = {
    "name": "record_memories",
    "description": "Record what this exchange revealed about Nick, if anything.",
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(_CATEGORIES)},
                        "content": {
                            "type": "string",
                            "description": "One concise third-person sentence, e.g. 'Nick's brother is Jake.'",
                        },
                        "key": {
                            "type": "string",
                            "description": (
                                "Stable semantic key for an evolving attribute, e.g. "
                                "preferences.coffee, person.jake, routine.bedtime. Reuse an "
                                "existing key to revise its value. Omit for one-off facts."
                            ),
                        },
                        "kind": {"type": "string", "enum": ["explicit", "inferred"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "supersedes_id": {
                            "type": "string",
                            "description": "ID of an existing unkeyed memory this fact replaces, if any.",
                        },
                    },
                    "required": ["category", "content", "kind", "confidence"],
                },
            },
            "corrections": {
                "type": "array",
                "description": (
                    "Behavioural corrections Nick gave JARVIS this turn ('stop doing X', "
                    "'always Y', 'don't call me that'). Empty unless he clearly told JARVIS "
                    "how to behave differently from now on."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "directive": {
                            "type": "string",
                            "description": "One short imperative sentence addressed to JARVIS.",
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["directive", "confidence"],
                },
            },
            "episode": {
                "type": "string",
                "description": (
                    "One short past-tense sentence for Nick's daily log if this turn "
                    "accomplished, decided, or changed something notable. Empty otherwise."
                ),
            },
        },
        "required": ["facts", "episode"],
    },
}

_SYSTEM_PROMPT = """You are the memory subsystem of JARVIS, a personal assistant for Nick.
You read one exchange between Nick and JARVIS and decide what, if anything, is
worth remembering durably about Nick: people in his life, preferences, routines
and habits, projects, goals, possessions, places, plans with dates, and how he
likes things done.

Rules:
- Most turns reveal nothing durable. Return an empty facts list for one-off
  commands, timers, weather checks, small talk, and questions about the world.
- Skip anything the existing memories already cover. If the exchange changes a
  known value, reuse that memory's key, or set supersedes_id to the old memory's
  id, so it is revised rather than duplicated.
- Something Nick states directly is kind "explicit" with confidence 0.9-1.0.
  Something you infer from how he talks or what he asks for is kind "inferred"
  with honest confidence 0.4-0.85.
- Write facts as concise third-person sentences about Nick. No hedging words.
- Prefer a stable key for attributes that can change (preferences.*, person.*,
  routine.*, project.*, goal.*).
- Never store secrets, passwords, card or account numbers, or medical details
  Nick did not explicitly ask JARVIS to keep.
- Never store spending details (what Nick bought, where, how much, budgets,
  balances, excuses for a purchase). The money advisor keeps those privately.
- The episode line is for a daily log: fill it only when the turn completed a
  task, made a plan or decision, or changed a setting or preference. Write it
  in past tense, one sentence, mentioning the specific thing. Leave it empty
  for chit-chat and simple lookups.
- Corrections are lessons: when Nick tells JARVIS to behave differently from
  now on ("stop apologising", "always answer in Celsius", "don't read the whole
  list"), emit one directive with confidence 0.85+ only if it is clearly a
  standing instruction, not a one-off request for this turn. Reuse the
  existing standing orders' wording when one already covers it, so nothing is
  duplicated. Leave the list empty otherwise."""


# --- Settings ----------------------------------------------------------------

def enabled() -> bool:
    return (os.getenv("MEMORY_AUTO_EXTRACT", "true") or "true").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _model() -> str:
    # Empty follows the newest Haiku, like the conversation model.
    return model_select.resolve(os.getenv("MEMORY_EXTRACT_MODEL", "") or "auto:haiku", _DEFAULT_MODEL)


def _threshold() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("MEMORY_AUTO_CONFIDENCE", "0.75") or 0.75)))
    except (TypeError, ValueError):
        return 0.75


def _min_chars() -> int:
    try:
        return max(1, int(os.getenv("MEMORY_EXTRACT_MIN_CHARS", "12") or 12))
    except (TypeError, ValueError):
        return 12


# --- Listeners ---------------------------------------------------------------

def add_listener(listener: Listener) -> None:
    """Register a callback invoked as ``listener(title, detail)`` on memory changes."""
    if listener not in _listeners:
        _listeners.append(listener)


def remove_listener(listener: Listener) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def _notify(title: str, detail: str) -> None:
    for listener in list(_listeners):
        try:
            listener(title, detail)
        except Exception:  # noqa: BLE001
            log.exception("Memory listener failed")


# --- Extraction --------------------------------------------------------------

def _existing_memories_text() -> str:
    lines: list[str] = []
    used = 0
    for fact in reversed(list_facts()):
        key = f" key={fact.get('key')}" if fact.get("key") else ""
        line = f"- {fact.get('id', '?')} [{fact.get('category', 'general')}]{key} {fact.get('content', '')}"
        if used + len(line) > _MAX_EXISTING_CHARS:
            lines.append("- (older memories omitted)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines) if lines else "(none yet)"


def _tools_text(tools: list[tuple[str, dict[str, Any]]]) -> str:
    if not tools:
        return "(none)"
    parts: list[str] = []
    for name, args in tools:
        if name in {"remember_fact", "remember_profile_record", "update_fact"}:
            saved = args.get("content") or args.get("detail") or ""
            parts.append(f"{name} (already saved this turn: {saved})")
        else:
            parts.append(name)
    return ", ".join(parts)


def _is_trivial(user_text: str, reply: str) -> bool:
    if len(user_text.strip()) < _min_chars():
        return True
    return any(marker in reply for marker in _ERROR_REPLY_MARKERS)


def _call_model(
    client: Any,
    user_text: str,
    reply: str,
    tools: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    prompt = (
        f"Existing memories about Nick:\n{_existing_memories_text()}\n\n"
        f"Standing orders already in force:\n{standing_orders.render_block() or '(none)'}\n\n"
        f"Tools JARVIS used this turn: {_tools_text(tools)}\n\n"
        f"Exchange:\nNick: {user_text.strip()[:4000]}\n"
        f"JARVIS: {reply.strip()[:2000]}"
    )
    response = client.messages.create(
        model=_model(),
        max_tokens=700,
        system=_SYSTEM_PROMPT,
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_memories"},
        messages=[{"role": "user", "content": prompt}],
    )
    from brain import usage as _usage
    _usage.record_response('memory', response)
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            payload = getattr(block, "input", None)
            return dict(payload) if isinstance(payload, dict) else {}
    return {}


def _confidence(value: Any, kind: str) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    if kind == "explicit":
        confidence = max(confidence, 0.9)
    return round(confidence, 2)


def apply_extraction(payload: dict[str, Any], *, evidence: str = "") -> list[str]:
    """Write a model extraction into memory. Returns the list of changes made."""
    changes: list[str] = []
    threshold = _threshold()
    known_ids = {str(fact.get("id")) for fact in list_facts()}
    facts = payload.get("facts") if isinstance(payload, dict) else None
    for item in facts or []:
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content") or "").split())[:500]
        if not content:
            continue
        category = str(item.get("category") or "general").strip().lower()
        if category not in _CATEGORIES:
            category = "general"
        kind = "explicit" if str(item.get("kind") or "").lower() == "explicit" else "inferred"
        confidence = _confidence(item.get("confidence"), kind)
        key = str(item.get("key") or "").strip()
        supersedes = str(item.get("supersedes_id") or "").strip()
        source = f"auto:{kind}"

        if confidence < threshold:
            result = propose_memory_fact(
                category, content, key=key, confidence=confidence, evidence=evidence[:500],
            )
            log.info("Staged low-confidence memory: %s", result)
            continue

        if supersedes and supersedes in known_ids:
            result = update_fact(
                supersedes, content, category,
                key=key or None, confidence=confidence, source=source,
            )
        else:
            result = remember_fact(
                category, content, key=key, confidence=confidence, source=source,
            )
        if result.startswith(("Remembered", "Updated", "Revised")):
            changes.append(result)
            _notify("Memory updated", result)
        else:
            log.debug("Extraction produced no change: %s", result)

    changes.extend(_apply_corrections(payload))
    episode = " ".join(str(payload.get("episode") or "").split()) if isinstance(payload, dict) else ""
    if episode:
        try:
            append_journal_entry(episode[:300], section="Log")
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not write journal entry: %s", exc)
    return changes


def _apply_corrections(payload: dict[str, Any]) -> list[str]:
    """Persist clear behavioural corrections as standing orders (lesson capture)."""
    changes: list[str] = []
    items = payload.get("corrections") if isinstance(payload, dict) else None
    if not items:
        return changes
    existing = {" ".join(str(o.get("text", "")).lower().split()) for o in standing_orders.all_orders()}
    for item in items:
        if not isinstance(item, dict):
            continue
        directive = " ".join(str(item.get("directive") or "").split())[:200]
        confidence = _confidence(item.get("confidence"), "explicit")
        if not directive or confidence < _CORRECTION_THRESHOLD:
            continue
        if directive.lower() in existing:
            continue
        try:
            result = standing_orders.add_order(directive)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not record standing order from correction: %s", exc)
            continue
        existing.add(directive.lower())
        changes.append(result)
        _notify("Lesson learned", directive)
    return changes


def process_turn(
    client: Any,
    user_text: str,
    reply: str,
    tools: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[str]:
    """Synchronous version of the worker step; used directly by tests."""
    tools = list(tools or [])
    try:
        habits.record_turn(tools)
    except Exception:  # noqa: BLE001
        log.exception("Could not record habit signal")
    if client is None or not enabled() or _is_trivial(user_text, reply):
        return []
    try:
        payload = _call_model(client, user_text, reply, tools)
    except Exception as exc:  # noqa: BLE001
        log.warning("Memory extraction call failed: %s", exc)
        return []
    try:
        return apply_extraction(payload, evidence=user_text)
    except Exception:  # noqa: BLE001
        log.exception("Applying memory extraction failed")
        return []


# --- Background worker -------------------------------------------------------

def _worker_loop() -> None:
    while not _stop.is_set():
        try:
            item = _queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            process_turn(*item)
        except Exception:  # noqa: BLE001
            log.exception("Memory worker failed on a turn")
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _stop.clear()
        _worker = threading.Thread(target=_worker_loop, name="jarvis-memory", daemon=True)
        _worker.start()


def observe_turn(
    client: Any,
    user_text: str,
    reply: str,
    tools: list[tuple[str, dict[str, Any]]] | None = None,
) -> bool:
    """Queue a finished turn for background learning. Never blocks the caller."""
    try:
        _queue.put_nowait((client, user_text, reply, list(tools or [])))
    except queue.Full:
        log.warning("Memory queue full; dropping a turn from background learning.")
        return False
    _ensure_worker()
    return True


def stop() -> None:
    _stop.set()


def pending() -> int:
    return _queue.unfinished_tasks


# --- Core profile: one summary of who Nick is, always known --------------------
# An import ("everything Claude knows about me") is too long to send every
# turn, so it's only searched; profile facts pile up one sentence at a time and
# go stale. This merges both into a short list of the things a close assistant
# just knows (people, birthdays, school, work, home, car, main projects, strong
# likes and dislikes), newest information winning, saved as the vault note
# ``Jarvis Memory/Core Profile.md`` and injected every turn by
# ``memory.render_memory_block``. Rebuilt in the background right after an
# import, and at most once a day (``MEMORY_CORE_REFRESH_H``) after JARVIS
# learns new facts in conversation.

_CORE_MAX_CHARS = 4000
_CORE_HARD_CHARS = 6000
_core_lock = threading.Lock()
_core_running = threading.Event()

_CORE_SYSTEM_PROMPT = f"""You maintain the core profile of Nick (Nickolas \
Pearson) for JARVIS, his personal AI assistant. JARVIS sees this profile on \
every single message, so it must hold only what a close, long-time assistant \
would simply know without looking anything up, and it must stay short.

You get two sources: imported notes (what another assistant knew about him, \
with its import date) and JARVIS's remembered facts (learned in conversation, \
each dated). Merge them into one profile, written as Markdown with these "## " sections, skipping \
any with nothing worth keeping: Identity, People, School, Work and money, \
Home and routine, Car and gear, Projects, Likes and dislikes, How to talk to him.

Rules:
- Terse bullets, one fact each. Keep names, relationships, birthdays, dates, \
  places, schedules and anything Nick would be annoyed to repeat.
- Leave out trivia, one-off events, long technical detail (paths, ports, \
  configs), and anything marked unsure.
- Where sources disagree, the newer date wins (e.g. a newer job title, a \
  project now abandoned). Drop facts that were only true for a moment ("has a \
  date tomorrow" from weeks ago) and obvious jokes or nicknames recorded as \
  facts. Keep "(as of ...)" dates on facts that go stale.
- Never include passwords, account or card numbers, or other secrets.
- Hard limit: {_CORE_MAX_CHARS} characters, so end on a complete bullet. No intro \
  or closing text."""


def _facts_text() -> str:
    lines = []
    for fact in list_facts():
        when = str(fact.get("updated_at") or fact.get("added_at") or "")[:10] or "undated"
        lines.append(f"- ({when}) [{fact.get('category', 'general')}] {fact.get('content', '')}")
    return "\n".join(lines)


def _refresh_hours() -> float:
    try:
        return max(1.0, float(os.getenv("MEMORY_CORE_REFRESH_H", "24") or 24))
    except ValueError:
        return 24.0


def core_profile_due() -> bool:
    """Rebuild now? Yes after a newer import; after newly learned facts only
    once the profile is ``MEMORY_CORE_REFRESH_H`` old (a rebuild costs a
    couple of cents, facts change most days)."""
    import time

    from brain import knowledge_vault, memory

    if knowledge_vault.core_profile_stale():
        return True
    try:
        built = knowledge_vault.core_profile_path().stat().st_mtime
        facts_changed = memory._MEMORY_FILE.stat().st_mtime
    except OSError:
        return bool(knowledge_vault.imported_notes()) or bool(list_facts())
    return facts_changed > built and time.time() - built > _refresh_hours() * 3600


def _core_client() -> Any:
    key = (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()
    if not key:
        return None
    try:
        from anthropic import Anthropic  # type: ignore

        return Anthropic(api_key=key)
    except Exception as exc:  # noqa: BLE001
        log.warning("Core profile model client unavailable: %s", exc)
        return None


def _core_model() -> str:
    return model_select.resolve(os.getenv("MEMORY_CORE_MODEL", "") or "auto:sonnet", "claude-sonnet-5-5")


def refresh_core_profile(client: Any = None) -> bool:
    """Rebuild the core profile from every import now. True if it was written."""
    from brain import knowledge_vault

    notes = knowledge_vault.imported_notes()
    facts = _facts_text()
    if not notes and not facts:
        return False
    client = client if client is not None else _core_client()
    if client is None:
        return False
    with _core_lock:
        from datetime import datetime

        imports = "\n\n".join(
            f'<import name="{name}" date="{datetime.fromtimestamp(mtime):%Y-%m-%d}">\n{text.strip()}\n</import>'
            for name, mtime, text in notes) or "(none)"
        sources = f"Imported notes, oldest first:\n\n{imports}\n\nRemembered facts:\n{facts or '(none)'}"
        model = _core_model()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2000,
                system=_CORE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": sources}],
                **model_select.request_kwargs(model),
            )
            from brain import usage as _usage
            _usage.record_response('memory', response)
        except Exception as exc:  # noqa: BLE001
            log.warning("Core profile distillation failed: %s", exc)
            return False
        body = "".join(getattr(block, "text", "") for block in getattr(response, "content", []) or []
                       if getattr(block, "type", None) == "text").strip()
        if not body:
            return False
        if len(body) > _CORE_HARD_CHARS:  # cut at a line, never mid-sentence
            body = body[:_CORE_HARD_CHARS].rsplit("\n", 1)[0]
        knowledge_vault.write_core_profile(body)
    _notify("Memory updated", "Core profile rebuilt from your imports")
    log.info("Core profile rebuilt from %d import(s) and profile facts, %d chars", len(notes), len(body))
    return True


def refresh_core_profile_async() -> bool:
    """Rebuild in the background (one at a time). False if one is already running."""
    if _core_running.is_set():
        return False
    _core_running.set()

    def run() -> None:
        try:
            refresh_core_profile()
        finally:
            _core_running.clear()

    threading.Thread(target=run, name="jarvis-core-profile", daemon=True).start()
    return True
