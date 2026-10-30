"""Persistent conversation history backed by SQLite.

Stores user/assistant text pairs grouped into sessions.  A new session is
created each time JarvisAgent starts so previous conversations are browsable
and resumable.

Database: memory/history.db
"""

from __future__ import annotations

from contextvars import ContextVar
from threading import Lock
import sqlite3
from contextlib import closing
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

request_context: ContextVar[str | None] = ContextVar("companion_request", default=None)

_DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "history.db"

# Keep at most this many user+assistant pairs per session before pruning.
MAX_STORED_TURNS = 200
MAX_SESSION_SUMMARY_CHARS = 1_600
_SUMMARY_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_'’-]*", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# Schema creation/migration is idempotent but not free (table creation plus
# PRAGMA scans); run it once per database path instead of on every query.
# Keyed by path so tests that repoint _DB_PATH still initialize fresh files.
_schema_ready_paths: set[str] = set()
_schema_lock = Lock()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    with _schema_lock:
        _initialize_schema(conn)


def _initialize_schema(conn: sqlite3.Connection) -> None:
    """Create tables and migrate older schemas in place (once per DB path)."""
    if str(_DB_PATH) in _schema_ready_paths:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT    NOT NULL DEFAULT 'legacy',
            role        TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
        """
    )
    # Migrate: add session_id column if an older DB exists without it.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversation)")}
    if "session_id" not in cols:
        conn.execute(
            "ALTER TABLE conversation ADD COLUMN session_id TEXT NOT NULL DEFAULT 'legacy'"
        )
    if "request_id" not in cols:
        conn.execute("ALTER TABLE conversation ADD COLUMN request_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_session_id ON conversation(session_id, id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS companion_requests (
        request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
        message TEXT NOT NULL, reply TEXT
    )""")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id  TEXT PRIMARY KEY,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now')),
            title       TEXT,
            summary     TEXT,
            summarized_at TEXT
        )
        """
    )
    session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "updated_at" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN updated_at TEXT")
        conn.execute("UPDATE sessions SET updated_at = created_at WHERE updated_at IS NULL")
    if "title" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
    if "summary" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
    if "summarized_at" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN summarized_at TEXT")
    conn.commit()
    _schema_ready_paths.add(str(_DB_PATH))


def _prune(conn: sqlite3.Connection, session_id: str) -> None:
    """Keep only the most recent MAX_STORED_TURNS pairs for a session."""
    keep = MAX_STORED_TURNS * 2
    conn.execute(
        """
        DELETE FROM conversation
        WHERE session_id = ?
          AND id NOT IN (
              SELECT id FROM conversation
              WHERE session_id = ?
              ORDER BY id DESC
              LIMIT ?
          )
        """,
        (session_id, session_id, keep),
    )
    conn.commit()


def _refresh_session_summary(conn: sqlite3.Connection, session_id: str) -> None:
    """Maintain a bounded extractive digest without another model/API call."""
    rows = conn.execute(
        """
        SELECT role, content
        FROM conversation
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (session_id,),
    ).fetchall()
    if not rows:
        return
    lines: list[str] = []
    for row in reversed(rows):
        content = " ".join(str(row["content"] or "").split())
        if not content:
            continue
        label = "Nick" if row["role"] == "user" else "JARVIS"
        lines.append(f"{label}: {content[:280]}")
    summary = "\n".join(lines)[-MAX_SESSION_SUMMARY_CHARS:]
    if os.getenv("LOCAL_MODEL_SUMMARIES", "false").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from brain.local_model import generate_summary

            messages = [
                {"role": str(row["role"]), "content": str(row["content"] or "")}
                for row in reversed(rows)
            ]
            summary = generate_summary(messages) or summary
        except Exception:  # noqa: BLE001 - persistence must keep deterministic fallback
            log.exception("Local session summarization failed; retaining extractive digest.")
    conn.execute(
        """
        UPDATE sessions
        SET summary = ?, summarized_at = datetime('now')
        WHERE session_id = ?
        """,
        (summary, session_id),
    )


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def start_new_session() -> str:
    """Register a new session and return its ID."""
    session_id = uuid.uuid4().hex[:12]
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO sessions (session_id) VALUES (?)", (session_id,)
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.exception("Failed to create session record.")
    return session_id


def list_sessions(limit: int = 30) -> list[dict[str, Any]]:
    """Return recent sessions, newest first.

    Each dict has session metadata plus a preview of the first user message.
    """
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT
                    s.session_id,
                    s.created_at,
                    COALESCE(s.updated_at, s.created_at) AS updated_at,
                    s.title,
                    s.summary,
                    s.summarized_at,
                    COUNT(c.id) / 2            AS turn_count,
                    (
                        SELECT first.content
                        FROM conversation AS first
                        WHERE first.session_id = s.session_id AND first.role = 'user'
                        ORDER BY first.id ASC
                        LIMIT 1
                    ) AS preview
                FROM sessions s
                LEFT JOIN conversation c ON c.session_id = s.session_id
                GROUP BY s.session_id
                ORDER BY COALESCE(s.updated_at, s.created_at) DESC, s.rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        log.exception("Failed to list sessions.")
        return []


def load_session_messages(
    session_id: str, *, include_timestamps: bool = False
) -> list[dict[str, Any]]:
    """Return all messages for a session in chronological order."""
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT id, role, content, created_at, request_id
                FROM conversation
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        if include_timestamps:
            return [
                {"id": r["id"], "role": r["role"], "content": r["content"], "created_at": r["created_at"], "request_id": r["request_id"]}
                for r in rows
            ]
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    except Exception:  # noqa: BLE001
        log.exception("Failed to load session messages.")
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_history(max_turns: int = 20, session_id: str | None = None) -> list[dict[str, Any]]:
    """Return the last ``max_turns`` pairs for a session (or globally)."""
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            if session_id:
                rows = conn.execute(
                    """
                    SELECT role, content FROM conversation
                    WHERE session_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (session_id, max_turns * 2),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT role, content FROM conversation
                    ORDER BY id DESC LIMIT ?
                    """,
                    (max_turns * 2,),
                ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except Exception:  # noqa: BLE001
        log.exception("Failed to load conversation history; starting fresh.")
        return []


def append_turn(user_text: str, assistant_text: str, session_id: str = "default") -> None:
    """Persist one complete turn."""
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            conn.executemany(
                "INSERT INTO conversation (session_id, role, content, request_id) VALUES (?, ?, ?, ?)",
                [(session_id, "user", user_text, request_context.get()),
                 (session_id, "assistant", assistant_text, request_context.get())],
            )
            conn.execute(
                "UPDATE sessions SET updated_at = datetime('now') WHERE session_id = ?",
                (session_id,),
            )
            if request_context.get():
                conn.execute("UPDATE companion_requests SET reply = ? WHERE request_id = ?",
                             (assistant_text, request_context.get()))
            _refresh_session_summary(conn, session_id)
            conn.commit()
            _prune(conn, session_id)
    except Exception:  # noqa: BLE001
        log.exception("Failed to persist conversation turn.")


def session_exists(session_id: str) -> bool:
    """Return whether a session is registered."""
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            return conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone() is not None
    except Exception:  # noqa: BLE001
        log.exception("Failed to query session %s.", session_id)
        return False


def rename_session(session_id: str, title: str) -> bool:
    """Set a user-visible session title."""
    clean = title.strip()[:120]
    if not clean:
        return False
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            cursor = conn.execute(
                "UPDATE sessions SET title = ?, updated_at = datetime('now') WHERE session_id = ?",
                (clean, session_id),
            )
            conn.commit()
            return cursor.rowcount > 0
    except Exception:  # noqa: BLE001
        log.exception("Failed to rename session %s.", session_id)
        return False


def delete_session(session_id: str) -> bool:
    """Delete one session and its messages."""
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            conn.execute("DELETE FROM companion_requests WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM conversation WHERE session_id = ?", (session_id,))
            cursor = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0
    except Exception:  # noqa: BLE001
        log.exception("Failed to delete session %s.", session_id)
        return False


def clear_history() -> str:
    """Delete all stored conversation history."""
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            conn.execute("DELETE FROM companion_requests")
            conn.execute("DELETE FROM conversation")
            conn.execute("DELETE FROM sessions")
            conn.commit()
        log.info("Conversation history cleared.")
        return "Done. Conversation history wiped."
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to clear conversation history.")
        return f"Couldn't clear history: {exc}"


def history_stats() -> str:
    """Return a spoken summary of stored history size."""
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            turns = conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0] // 2
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        return (
            f"I have {turns} conversation turn{'s' if turns != 1 else ''} "
            f"across {sessions} session{'s' if sessions != 1 else ''} in long-term memory."
        )
    except Exception:  # noqa: BLE001
        log.exception("Failed to query history stats.")
        return "Couldn't read history stats."


def search_session_summaries(query: str = "", limit: int = 5) -> list[dict[str, Any]]:
    """Return bounded recent/relevant session digests for deliberate recall."""
    query_tokens = {token.lower() for token in _SUMMARY_WORD_RE.findall(query or "")}
    limit = max(1, min(20, int(limit or 5)))
    try:
        with closing(_connect()) as conn, conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT session_id, created_at, updated_at, title, summary, summarized_at
                FROM sessions
                WHERE summary IS NOT NULL AND summary != ''
                ORDER BY COALESCE(updated_at, created_at) DESC, rowid DESC
                LIMIT 200
                """
            ).fetchall()
        results: list[tuple[int, int, dict[str, Any]]] = []
        for recency, row in enumerate(rows):
            item = dict(row)
            haystack = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
            score = sum(haystack.count(token) for token in query_tokens)
            if query_tokens and score <= 0:
                continue
            results.append((score, -recency, item))
        results.sort(key=lambda item: (-item[0], -item[1]))
        return [item[2] for item in results[:limit]]
    except Exception:  # noqa: BLE001
        log.exception("Failed to search session summaries.")
        return []


def describe_session_summaries(query: str = "", limit: int = 5) -> str:
    """Format session digests with IDs and timestamps for agent/tool use."""
    summaries = search_session_summaries(query, limit)
    if not summaries:
        return "No prior session summaries matched."
    sections = []
    for item in summaries:
        heading = item.get("title") or f"Session {str(item.get('session_id', ''))[:8]}"
        sections.append(
            f"{heading} [{item.get('session_id')}, updated {item.get('updated_at')}]:\n"
            f"{item.get('summary', '')}"
        )
    return "\n\n".join(sections)


def begin_companion_request(request_id: str, session_id: str, message: str) -> str | None:
    """Claim once under the runtime lock; never replay an uncertain tool turn."""
    with closing(_connect()) as conn, conn:
        _ensure_schema(conn)
        row = conn.execute("SELECT * FROM companion_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row:
            if row["session_id"] != session_id or row["message"] != message:
                raise ValueError("Request ID belongs to a different turn")
            if row["reply"] is None:
                raise ValueError("This turn was interrupted. Check its outcome before sending a new request.")
            return str(row["reply"])
        conn.execute("INSERT INTO companion_requests (request_id, session_id, message) VALUES (?, ?, ?)",
                     (request_id, session_id, message))
        return None
