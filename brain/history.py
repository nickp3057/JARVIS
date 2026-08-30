"""Persistent conversation history backed by SQLite.

Stores user/assistant text pairs grouped into sessions.  A new session is
created each time JarvisAgent starts so previous conversations are browsable
and resumable.

Database: memory/history.db
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "history.db"

# Keep at most this many user+assistant pairs per session before pruning.
MAX_STORED_TURNS = 200


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables and migrate older schemas in place."""
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id  TEXT PRIMARY KEY,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now')),
            title       TEXT
        )
        """
    )
    session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "updated_at" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN updated_at TEXT")
        conn.execute("UPDATE sessions SET updated_at = created_at WHERE updated_at IS NULL")
    if "title" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
    conn.commit()


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


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def start_new_session() -> str:
    """Register a new session and return its ID."""
    session_id = uuid.uuid4().hex[:12]
    try:
        with _connect() as conn:
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
        with _connect() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT
                    s.session_id,
                    s.created_at,
                    COALESCE(s.updated_at, s.created_at) AS updated_at,
                    s.title,
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
                ORDER BY COALESCE(s.updated_at, s.created_at) DESC
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
        with _connect() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT role, content, created_at
                FROM conversation
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        if include_timestamps:
            return [
                {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
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
        with _connect() as conn:
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
        with _connect() as conn:
            _ensure_schema(conn)
            conn.executemany(
                "INSERT INTO conversation (session_id, role, content) VALUES (?, ?, ?)",
                [(session_id, "user", user_text), (session_id, "assistant", assistant_text)],
            )
            conn.execute(
                "UPDATE sessions SET updated_at = datetime('now') WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
            _prune(conn, session_id)
    except Exception:  # noqa: BLE001
        log.exception("Failed to persist conversation turn.")


def session_exists(session_id: str) -> bool:
    """Return whether a session is registered."""
    try:
        with _connect() as conn:
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
        with _connect() as conn:
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
        with _connect() as conn:
            _ensure_schema(conn)
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
        with _connect() as conn:
            _ensure_schema(conn)
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
        with _connect() as conn:
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
