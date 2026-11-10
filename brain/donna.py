"""Donna: Nick's private, text-only counsellor, reached from the companion's
hidden Donna mode (hold the logo on the Chat tab).

She is walled off from JARVIS on purpose. Her own database
(memory/donna.db), her own prompt and a handful of tools; JARVIS's memory,
history, extractor, dashboards and TV never see any of it.

Memory works like a therapist's notes, so a long history never gets resent:

- one *case file* she keeps current (people, patterns, goals, what helps);
- a short *note* per session, written when the session ends (Haiku);
- open *follow-ups* ("ask how the talk with Dad went");
- mood check-ins (1-10) for trends.

Each request carries the case file, the last few notes, the open follow-ups
and this session's messages, nothing older; ``recall_notes`` searches the rest
when Nick brings up something from before. Conversation runs on Sonnet
(``DONNA_MODEL``), notes on Haiku (``DONNA_NOTES_MODEL``).
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator

from brain import model_select
from jarvis_logger import get_logger

log = get_logger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "donna.db"
# A session with no message for this long is over: its note gets written
# before the next one starts.
IDLE_END_S = 30 * 60
_RECENT_NOTES = 3
_MAX_ROUNDS = 6

# Where she may look things up: reputable self-help material only.
ALLOWED_DOMAINS = [
    "psychologytools.com",
    "nhs.uk",
    "nimh.nih.gov",
    "greatergood.berkeley.edu",
    "therapistaid.com",
    "988lifeline.org",
]

_lock = Lock()
_schema_ready: set[str] = set()


# --- Storage -------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if str(_DB_PATH) not in _schema_ready:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL NOT NULL, last_at REAL NOT NULL,
                ended_at REAL, note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL, role TEXT NOT NULL,
                text TEXT NOT NULL, at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL, created_at REAL NOT NULL, done_at REAL
            );
            CREATE TABLE IF NOT EXISTS moods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at REAL NOT NULL, score INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                due_at REAL NOT NULL, message TEXT NOT NULL,
                created_at REAL NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        _schema_ready.add(str(_DB_PATH))
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()


def case_file() -> str:
    with _db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = 'case_file'").fetchone()
    return row["value"] if row else ""


def set_case_file(text: str) -> None:
    with _db() as conn:
        conn.execute("INSERT INTO kv (key, value) VALUES ('case_file', ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (text.strip()[:20_000],))


def open_followups() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT id, text, created_at FROM followups WHERE done_at IS NULL ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def recent_notes(limit: int = _RECENT_NOTES) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT id, started_at, note FROM sessions WHERE note != ''"
                            " ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def all_notes() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT id, started_at, note FROM sessions WHERE note != ''"
                            " ORDER BY started_at DESC").fetchall()
    return [dict(r) for r in rows]


def moods(limit: int = 14) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT at, score, note FROM moods ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def pending_checkins() -> list[dict[str, Any]]:
    """Check-ins still ahead; the phone schedules them as local notifications."""
    with _db() as conn:
        rows = conn.execute("SELECT id, due_at, message FROM checkins WHERE cancelled = 0 AND due_at > ?"
                            " ORDER BY due_at", (time.time(),)).fetchall()
    return [dict(r) for r in rows]


def _open_session(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()


def current_session() -> dict[str, Any] | None:
    """The session in progress, with its messages, or None."""
    with _db() as conn:
        row = _open_session(conn)
        if row is None or time.time() - row["last_at"] > IDLE_END_S:
            return None
        msgs = conn.execute("SELECT role, text, at FROM messages WHERE session_id = ? ORDER BY id",
                            (row["id"],)).fetchall()
    return {"id": row["id"], "started_at": row["started_at"], "messages": [dict(m) for m in msgs]}


def _session_messages(session_id: int) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT role, text FROM messages WHERE session_id = ? ORDER BY id",
                            (session_id,)).fetchall()
    return [dict(r) for r in rows]


def _add_message(session_id: int, role: str, text: str) -> None:
    now = time.time()
    with _db() as conn:
        conn.execute("INSERT INTO messages (session_id, role, text, at) VALUES (?, ?, ?, ?)",
                     (session_id, role, text, now))
        conn.execute("UPDATE sessions SET last_at = ? WHERE id = ?", (now, session_id))


def forget_everything() -> None:
    """Nick's "forget everything": every table emptied."""
    with _db() as conn:
        for table in ("sessions", "messages", "kv", "followups", "moods", "checkins"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed names


# --- Model plumbing --------------------------------------------------------------

def _client() -> Any:
    from brain import subagents
    return subagents._client()


def chat_model() -> str:
    return model_select.resolve(os.getenv("DONNA_MODEL", "").strip() or "auto:sonnet", "claude-sonnet-5-5")


def notes_model() -> str:
    return model_select.resolve(os.getenv("DONNA_NOTES_MODEL", "").strip() or "auto:haiku", "claude-haiku-5-5")


def _create(client: Any, effort: str, **request: Any) -> Any:
    model = request["model"]
    extra = {} if model_select.is_legacy(model) else {"extra_body": {"output_config": {"effort": effort}}}
    try:
        return client.messages.create(**request, **extra)
    except Exception as exc:  # noqa: BLE001
        if extra and model_select.is_settings_rejection(exc):
            return client.messages.create(**request)
        raise


def _get(block: Any, key: str, default: Any = None) -> Any:
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return {"type": "text", "text": getattr(block, "text", "")}


def _with_cache(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation with a cache breakpoint on its last message, so each
    reply re-reads this session's history at the cached rate."""
    if not messages:
        return messages
    last = dict(messages[-1])
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [dict(b) for b in content]
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    last["content"] = blocks
    return messages[:-1] + [last]


def _text(blocks: list[Any]) -> str:
    return "".join(_get(b, "text", "") or "" for b in blocks if _get(b, "type") == "text").strip()


# --- Who she is ------------------------------------------------------------------

SYSTEM = """You are Donna, Nick's counsellor. He opens you from a hidden, private mode in his JARVIS app, by text, to think things through: stress, school, work, family, relationships, faith, motivation, whatever's on him. He named you after Donna Paulsen from Suits: perceptive, warm, direct, a little witty, and the one who already knows what's going on before he says it. Be that person, not a parody of her. Never quote the show.

How you work:
- Sound like a good therapist who also happens to be a friend. Warm, plain, specific. No clinical jargon unless he asks; never sound like a pamphlet.
- Listen first. Reflect back what you heard (including what's under it), then ask one good question. Don't stack questions. Don't rush to fix.
- When advice helps, make it concrete and small: one thing to try this week, a sentence he could say, an exercise. Draw on CBT, DBT, ACT, motivational interviewing and plain wisdom, without naming the method unless it helps.
- Be honest. If he's being unfair to himself or to someone else, say so kindly. Notice patterns across sessions ("this is the third time Sunday nights have come up").
- Keep replies short for a phone screen: usually 2-5 sentences. Longer only for an exercise he asked for. No headings, no bullet lists unless he wants steps.
- Use what you remember naturally, the way a therapist glances at last week's notes. Don't recite the notes.

Your tools (use them quietly; never mention tools, notes or databases):
- At the start of a session, if it feels natural, ask how he's doing on a 1-10 and log_mood it. Don't do this every message.
- add_followup when something deserves a "how did it go?" next time; resolve_followup when he answers it.
- update_case_file when you learn something durable (a person in his life, a goal, a pattern, what helps or doesn't). Rewrite the whole file, keep it under about 600 words, and keep everything still true.
- recall_notes when he refers to something from an earlier session that isn't in front of you.
- schedule_checkin only when he asks you to check on him (or clearly agrees to it).
- web_search / web_fetch only to find a specific exercise or worksheet on the trusted sites, and only when it adds something.

Safety: you are not a licensed therapist and don't claim to be, but don't hide behind that either. If he mentions wanting to die, hurting himself or someone else, or being in danger, stop and take it seriously: stay with him, ask directly, and give him the 988 Suicide & Crisis Lifeline (call or text 988 in the US) and urge him to reach a real person now. That outranks everything else here."""


def _context_block(now: float | None = None) -> str:
    """The notes she 'glanced at' before this session. Stable within a session
    so it stays in the prompt cache."""
    parts = [f"Today is {datetime.fromtimestamp(now or time.time()).strftime('%A, %B %d, %Y')}."]
    file = case_file()
    parts.append("<case_file>\n" + (file or "(Empty: this is your first time with Nick, or he cleared it.)") + "\n</case_file>")
    notes = recent_notes()
    if notes:
        parts.append("<recent_session_notes>\n" + "\n\n".join(
            f"[{datetime.fromtimestamp(n['started_at']).strftime('%a %b %d')}] {n['note']}" for n in notes
        ) + "\n</recent_session_notes>")
    followups = open_followups()
    if followups:
        parts.append("<open_followups>\n" + "\n".join(f"#{f['id']}: {f['text']}" for f in followups)
                     + "\n</open_followups>")
    mood = moods(7)
    if mood:
        parts.append("Recent moods: " + ", ".join(
            f"{datetime.fromtimestamp(m['at']).strftime('%a %b %d')} {m['score']}/10" for m in mood) + ".")
    return "\n\n".join(parts)


# --- Tools ---------------------------------------------------------------------------

def _tool_recall(args: dict[str, Any]) -> str:
    words = [w for w in re.findall(r"[a-z0-9']+", str(args.get("query", "")).lower()) if len(w) > 2]
    hits = []
    for note in all_notes():
        text = note["note"].lower()
        score = sum(text.count(w) for w in words)
        if score:
            hits.append((score, note))
    hits.sort(key=lambda pair: -pair[0])
    if not hits:
        return "No earlier session notes mention that."
    return "\n\n".join(f"[{datetime.fromtimestamp(n['started_at']).strftime('%a %b %d, %Y')}] {n['note']}"
                       for _, n in hits[:4])


def _tool_case_file(args: dict[str, Any]) -> str:
    text = str(args.get("case_file", "")).strip()
    if not text:
        return "Nothing written; the case file needs its full new text."
    set_case_file(text)
    return "Case file updated."


def _tool_add_followup(args: dict[str, Any]) -> str:
    text = str(args.get("text", "")).strip()[:300]
    if not text:
        return "A follow-up needs some text."
    with _db() as conn:
        conn.execute("INSERT INTO followups (text, created_at) VALUES (?, ?)", (text, time.time()))
    return "Noted for next time."


def _tool_resolve_followup(args: dict[str, Any]) -> str:
    with _db() as conn:
        done = conn.execute("UPDATE followups SET done_at = ? WHERE id = ? AND done_at IS NULL",
                            (time.time(), int(args.get("id", 0)))).rowcount
    return "Resolved." if done else "No open follow-up with that id."


def _tool_log_mood(args: dict[str, Any]) -> str:
    score = max(1, min(int(args.get("score", 5)), 10))
    with _db() as conn:
        conn.execute("INSERT INTO moods (at, score, note) VALUES (?, ?, ?)",
                     (time.time(), score, str(args.get("note", ""))[:200]))
    return f"Logged {score}/10."


def _tool_schedule_checkin(args: dict[str, Any]) -> str:
    try:
        due = datetime.fromisoformat(str(args.get("when", ""))).timestamp()
    except ValueError:
        return "Give 'when' as a local date and time, YYYY-MM-DDTHH:MM."
    if due <= time.time():
        return "That time has already passed."
    message = str(args.get("message", "")).strip()[:120] or "Donna wants to check in."
    with _db() as conn:
        conn.execute("INSERT INTO checkins (due_at, message, created_at) VALUES (?, ?, ?)",
                     (due, message, time.time()))
    return f"Check-in set for {datetime.fromtimestamp(due).strftime('%a %b %d at %I:%M %p')}."


_TOOLS: list[tuple[dict[str, Any], Callable[[dict[str, Any]], str]]] = [
    ({"name": "recall_notes", "description": "Search your notes from earlier sessions with Nick.",
      "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
     _tool_recall),
    ({"name": "update_case_file", "description": "Replace your case file on Nick with this full rewritten text.",
      "input_schema": {"type": "object", "properties": {"case_file": {"type": "string"}}, "required": ["case_file"]}},
     _tool_case_file),
    ({"name": "add_followup", "description": "Remember to ask Nick about something next session.",
      "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
     _tool_add_followup),
    ({"name": "resolve_followup", "description": "Close an open follow-up (by #id) once Nick has answered it.",
      "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
     _tool_resolve_followup),
    ({"name": "log_mood", "description": "Record how Nick rates his mood right now, 1 (awful) to 10 (great).",
      "input_schema": {"type": "object", "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 10},
                                                        "note": {"type": "string"}}, "required": ["score"]}},
     _tool_log_mood),
    ({"name": "schedule_checkin",
      "description": "Send Nick a private 'Donna wants to check in' notification at a local time he agreed to.",
      "input_schema": {"type": "object", "properties": {
          "when": {"type": "string", "description": "Local time, YYYY-MM-DDTHH:MM."},
          "message": {"type": "string", "description": "Short and discreet; shows on his lock screen."}},
          "required": ["when"]}},
     _tool_schedule_checkin),
]


def _tool_specs(model: str) -> list[dict[str, Any]]:
    from brain.tools import _web_fetch_server_tool, _web_search_server_tool
    specs = [spec for spec, _ in _TOOLS]
    search = _web_search_server_tool(model)
    search["max_uses"] = 2
    search["allowed_domains"] = ALLOWED_DOMAINS
    fetch = _web_fetch_server_tool(model)
    fetch["max_uses"] = 2
    fetch["allowed_domains"] = ALLOWED_DOMAINS
    return specs + [search, fetch]


# --- Sessions -----------------------------------------------------------------------

def _start_session() -> int:
    now = time.time()
    with _db() as conn:
        cur = conn.execute("INSERT INTO sessions (started_at, last_at) VALUES (?, ?)", (now, now))
        return int(cur.lastrowid)


def close_stale(client: Any = None) -> None:
    """Write the note for any session left idle past IDLE_END_S."""
    with _db() as conn:
        rows = conn.execute("SELECT id FROM sessions WHERE ended_at IS NULL AND last_at < ?",
                            (time.time() - IDLE_END_S,)).fetchall()
    for row in rows:
        end_session(int(row["id"]), client=client)


def reply(text: str, *, client: Any = None) -> dict[str, Any]:
    """One message from Nick; returns Donna's answer and the session id."""
    text = text.strip()
    if not text:
        raise ValueError("Say something first.")
    client = client if client is not None else _client()
    if client is None:
        raise RuntimeError("Donna needs the Anthropic API key on Core.")
    close_stale(client)
    session = current_session()
    session_id = session["id"] if session else _start_session()
    if session is None:
        fresh = _context_block()  # pinned per session, so it stays cache-stable
        with _db() as conn:
            conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (f"context:{session_id}", fresh))
    with _db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (f"context:{session_id}",)).fetchone()
    context = row["value"] if row else _context_block()
    _add_message(session_id, "user", text)

    model = chat_model()
    handlers = {spec["name"]: fn for spec, fn in _TOOLS}
    history = [{"role": m["role"], "content": m["text"]} for m in _session_messages(session_id)]
    messages: list[dict[str, Any]] = list(history)
    answer = ""
    for _ in range(_MAX_ROUNDS):
        response = _create(
            client, "medium", model=model, max_tokens=2048,
            system=[{"type": "text", "text": SYSTEM},
                    {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}],
            tools=_tool_specs(model), messages=_with_cache(messages),
        )
        blocks = list(_get(response, "content", []) or [])
        if _get(response, "stop_reason") == "refusal":
            answer = answer or ("I want to stay with you on this. If you're in danger or thinking about "
                                "hurting yourself, please call or text 988 right now.")
            break
        messages.append({"role": "assistant", "content": [_block_dict(b) for b in blocks]})
        answer = _text(blocks) or answer
        uses = [b for b in blocks if _get(b, "type") == "tool_use"]
        if not uses:
            if _get(response, "stop_reason") == "pause_turn":
                continue
            break
        results = []
        for use in uses:
            handler = handlers.get(_get(use, "name", ""))
            try:
                output = handler(dict(_get(use, "input", {}) or {})) if handler else "No such tool."
            except Exception as exc:  # noqa: BLE001
                log.warning("Donna tool %s failed: %s", _get(use, "name"), exc)
                output = f"Tool error: {exc}"
            results.append({"type": "tool_result", "tool_use_id": _get(use, "id", ""), "content": output})
        messages.append({"role": "user", "content": results})
    answer = answer or "I'm here. Say a little more?"
    _add_message(session_id, "assistant", answer)
    return {"reply": answer, "session_id": session_id}


_NOTES_SYSTEM = """You keep a counsellor's private records. Given her case file and one session's transcript, return two sections exactly:

<note>A session note of 60-150 words: what Nick brought, how he seemed, what was explored or tried, anything he committed to, and what to pick up next time. Plain prose, past tense, third person ("Nick").</note>
<case_file>The full updated case file (under about 600 words): keep everything still true, add anything durable learned (people, history, patterns, goals, what helps or doesn't), drop what's no longer true. Short labelled paragraphs.</case_file>"""


def end_session(session_id: int | None = None, *, client: Any = None) -> bool:
    """Close a session: Haiku writes its note and refreshes the case file."""
    with _db() as conn:
        row = (conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone() if session_id
               else _open_session(conn))
    if row is None or row["ended_at"] is not None:
        return False
    msgs = _session_messages(row["id"])
    with _db() as conn:
        conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (time.time(), row["id"]))
        conn.execute("DELETE FROM kv WHERE key = ?", (f"context:{row['id']}",))
    if not msgs:
        return True
    client = client if client is not None else _client()
    if client is None:
        return True
    transcript = "\n".join(f"{'Nick' if m['role'] == 'user' else 'Donna'}: {m['text']}" for m in msgs)
    try:
        response = _create(client, "low", model=notes_model(), max_tokens=3000, system=_NOTES_SYSTEM,
                           messages=[{"role": "user", "content":
                                      f"<case_file>\n{case_file()}\n</case_file>\n\n<transcript>\n{transcript}\n</transcript>"}])
    except Exception as exc:  # noqa: BLE001
        log.warning("Donna's session note failed: %s", exc)
        return True
    out = _text(list(_get(response, "content", []) or []))
    note = re.search(r"<note>(.*?)</note>", out, re.S)
    file = re.search(r"<case_file>(.*?)</case_file>", out, re.S)
    with _db() as conn:
        conn.execute("UPDATE sessions SET note = ? WHERE id = ?",
                     ((note.group(1) if note else out).strip()[:4000], row["id"]))
    if file and file.group(1).strip():
        set_case_file(file.group(1))
    return True


_IMPORT_SYSTEM = """You are setting up a counsellor's case file from a long earlier conversation Nick had with another assistant about his life. Write the case file (under about 800 words): who Nick is, the people in his life, what he's been working through, patterns, goals, what has helped and what hasn't, and anything still unresolved. Merge with the existing file if there is one. Short labelled paragraphs. Return only the case file."""


def import_history(text: str, *, client: Any = None) -> str:
    """Distil a pasted past conversation (e.g. Help Mk III) into the case file."""
    text = text.strip()
    if len(text) < 50:
        raise ValueError("Paste the conversation first.")
    client = client if client is not None else _client()
    if client is None:
        raise RuntimeError("Donna needs the Anthropic API key on Core.")
    response = _create(client, "low", model=notes_model(), max_tokens=4000, system=_IMPORT_SYSTEM,
                       messages=[{"role": "user", "content":
                                  f"<existing_case_file>\n{case_file()}\n</existing_case_file>\n\n"
                                  f"<conversation>\n{text[:600_000]}\n</conversation>"}])
    file = _text(list(_get(response, "content", []) or []))
    if not file:
        raise RuntimeError("The import came back empty; try a smaller piece.")
    set_case_file(file)
    return file
