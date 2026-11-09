"""The shopping list, backed by a catalog of things Nick buys.

Every product Nick has ever added stays in the catalog (name, an optional
brand/detail note, the store, an optional photo). The list is simply the
catalog items flagged ``on_list``. Typing "face lotion" suggests the saved
"CeraVe PM face lotion" with its photo; ticking it off ("bought") takes it off
the list, counts the purchase and keeps it in the catalog for next time.

Database: memory/shopping.db. Photos: memory/shopping_photos/<id>.jpg.
"""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from jarvis_logger import get_logger

log = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent / "memory"
_DB_PATH = _ROOT / "shopping.db"
_PHOTO_DIR = _ROOT / "shopping_photos"
MAX_PHOTO_BYTES = 4 * 1024 * 1024

_lock = Lock()
_schema_ready: set[str] = set()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if str(_DB_PATH) not in _schema_ready:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shopping_items (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT    NOT NULL,
                note           TEXT    NOT NULL DEFAULT '',
                store          TEXT    NOT NULL DEFAULT '',
                quantity       INTEGER NOT NULL DEFAULT 1,
                on_list        INTEGER NOT NULL DEFAULT 0,
                has_photo      INTEGER NOT NULL DEFAULT 0,
                photo_version  INTEGER NOT NULL DEFAULT 0,
                times_bought   INTEGER NOT NULL DEFAULT 0,
                added_at       REAL,
                last_bought_at REAL,
                created_at     REAL    NOT NULL
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


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "note": row["note"],
        "store": row["store"],
        "quantity": row["quantity"],
        "on_list": bool(row["on_list"]),
        "has_photo": bool(row["has_photo"]),
        "photo_version": row["photo_version"],
        "times_bought": row["times_bought"],
        "added_at": row["added_at"],
        "last_bought_at": row["last_bought_at"],
    }


def _clean(text: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


# --- Reads -------------------------------------------------------------------

def get_item(item_id: int) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM shopping_items WHERE id = ?", (item_id,)).fetchone()
    return _row(row) if row else None


def catalog() -> list[dict[str, Any]]:
    """Every saved item, most bought (then most recently used) first."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM shopping_items ORDER BY times_bought DESC,"
            " COALESCE(last_bought_at, added_at, created_at) DESC, name COLLATE NOCASE"
        ).fetchall()
    return [_row(r) for r in rows]


def current_list() -> list[dict[str, Any]]:
    """What's on the list now, grouped by store then oldest-added first."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM shopping_items WHERE on_list = 1"
            " ORDER BY store = '', store COLLATE NOCASE, added_at, id"
        ).fetchall()
    return [_row(r) for r in rows]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w]


def _score(item: dict[str, Any], query: str) -> float:
    """How well a saved item matches what Nick typed; 0 = not at all.
    Every query word has to appear (as a prefix) in the name, note or store,
    so "face lotion" finds "CeraVe PM facial lotion" but not "body lotion"."""
    q = query.lower().strip()
    if not q:
        return 0.0
    name = item["name"].lower()
    haystack = _words(f"{item['name']} {item['note']} {item['store']}")
    qwords = _words(q)
    if not qwords or not all(any(h.startswith(w) for h in haystack) for w in qwords):
        return 0.0
    score = 1.0 + 0.1 * min(item["times_bought"], 20)
    if name == q:
        score += 10
    elif name.startswith(q):
        score += 4
    elif q in name:
        score += 2
    return score


def suggest(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Saved items matching ``query``, best first."""
    scored = [(s, item) for item in catalog() if (s := _score(item, query)) > 0]
    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored[:limit]]


def find_item(name: str) -> dict[str, Any] | None:
    """The one saved item ``name`` means: an exact name, or the single best
    clear match. None when it's missing or ambiguous."""
    matches = suggest(name, limit=3)
    if not matches:
        return None
    exact = [m for m in matches if m["name"].lower() == name.lower().strip()]
    if exact:
        return exact[0]
    if len(matches) == 1 or _score(matches[0], name) >= _score(matches[1], name) + 2:
        return matches[0]
    return None


# --- Writes ------------------------------------------------------------------

def create_item(name: str, note: str = "", store: str = "", on_list: bool = True,
                quantity: int = 1) -> dict[str, Any]:
    name = _clean(name, 120)
    if not name:
        raise ValueError("an item needs a name")
    now = time.time()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO shopping_items (name, note, store, quantity, on_list, added_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, _clean(note, 300), _clean(store, 60), max(1, min(int(quantity), 99)),
             1 if on_list else 0, now if on_list else None, now),
        )
        item_id = cur.lastrowid
    return get_item(int(item_id))  # type: ignore[return-value]


def update_item(item_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    current = get_item(item_id)
    if current is None:
        return None
    name = _clean(fields.get("name", current["name"]), 120)
    if not name:
        raise ValueError("an item needs a name")
    note = _clean(fields.get("note", current["note"]), 300)
    store = _clean(fields.get("store", current["store"]), 60)
    quantity = max(1, min(int(fields.get("quantity", current["quantity"]) or 1), 99))
    with _db() as conn:
        conn.execute(
            "UPDATE shopping_items SET name = ?, note = ?, store = ?, quantity = ? WHERE id = ?",
            (name, note, store, quantity, item_id),
        )
    return get_item(item_id)


def add_to_list(item_id: int, quantity: int | None = None) -> dict[str, Any] | None:
    """Put a saved item on the list (already there: just update the count)."""
    current = get_item(item_id)
    if current is None:
        return None
    with _db() as conn:
        conn.execute(
            "UPDATE shopping_items SET on_list = 1, added_at = COALESCE(CASE WHEN on_list = 1"
            " THEN added_at END, ?), quantity = ? WHERE id = ?",
            (time.time(), max(1, min(int(quantity or current["quantity"] or 1), 99)), item_id),
        )
    return get_item(item_id)


def remove_from_list(item_id: int) -> dict[str, Any] | None:
    """Off the list without counting a purchase (changed my mind)."""
    if get_item(item_id) is None:
        return None
    with _db() as conn:
        conn.execute("UPDATE shopping_items SET on_list = 0, added_at = NULL WHERE id = ?", (item_id,))
    return get_item(item_id)


def mark_bought(item_id: int) -> dict[str, Any] | None:
    """Ticked off at the store: off the list, purchase counted."""
    current = get_item(item_id)
    if current is None:
        return None
    if current["on_list"]:
        with _db() as conn:
            conn.execute(
                "UPDATE shopping_items SET on_list = 0, added_at = NULL,"
                " times_bought = times_bought + 1, last_bought_at = ? WHERE id = ?",
                (time.time(), item_id),
            )
    return get_item(item_id)


def undo_bought(item_id: int) -> dict[str, Any] | None:
    """The Undo after a mis-tap: back on the list, purchase uncounted."""
    current = get_item(item_id)
    if current is None:
        return None
    if not current["on_list"]:
        with _db() as conn:
            conn.execute(
                "UPDATE shopping_items SET on_list = 1, added_at = ?,"
                " times_bought = MAX(times_bought - 1, 0) WHERE id = ?",
                (time.time(), item_id),
            )
    return get_item(item_id)


def delete_item(item_id: int) -> bool:
    with _db() as conn:
        deleted = conn.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,)).rowcount > 0
    if deleted:
        photo_path(item_id).unlink(missing_ok=True)
    return deleted


# --- Photos ------------------------------------------------------------------

def photo_path(item_id: int) -> Path:
    return _PHOTO_DIR / f"{int(item_id)}.jpg"


def set_photo(item_id: int, data: bytes) -> dict[str, Any] | None:
    if get_item(item_id) is None:
        return None
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise ValueError("photo must be a JPEG under 4 MB")
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("photo must be a JPEG")
    _PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    path = photo_path(item_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    with _db() as conn:
        conn.execute(
            "UPDATE shopping_items SET has_photo = 1, photo_version = photo_version + 1 WHERE id = ?",
            (item_id,),
        )
    return get_item(item_id)


def clear_photo(item_id: int) -> dict[str, Any] | None:
    if get_item(item_id) is None:
        return None
    photo_path(item_id).unlink(missing_ok=True)
    with _db() as conn:
        conn.execute("UPDATE shopping_items SET has_photo = 0 WHERE id = ?", (item_id,))
    return get_item(item_id)


# --- Words for JARVIS ----------------------------------------------------------

def _label(item: dict[str, Any]) -> str:
    text = item["name"]
    if item["quantity"] > 1:
        text = f"{item['quantity']}× {text}"
    if item["note"]:
        text += f" ({item['note']})"
    return text


def describe_list() -> str:
    items = current_list()
    if not items:
        return "The shopping list is empty, sir."
    groups: dict[str, list[str]] = {}
    for item in items:
        groups.setdefault(item["store"] or "Anywhere", []).append(_label(item))
    parts = [f"{store}: {', '.join(names)}" for store, names in groups.items()]
    return f"{len(items)} on the shopping list, sir. " + "; ".join(parts) + "."
