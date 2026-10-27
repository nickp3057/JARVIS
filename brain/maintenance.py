"""Recurring household maintenance: a countdown to the next time and a log
of every time it was done.

Three schedule types:
- ``interval``: due ``interval_value`` days/months/years after the last
  completion; never done means due today.
- ``fixed_calendar``: due on a month (and optional day) every year. Doing it
  up to ``_EARLY_GRACE_DAYS`` before the date counts for that year.
- ``log_only``: no due date, just "last done N days ago".

Days are local calendar days, computed here so every client shows the same
countdown. Database: memory/maintenance.db (seeded once, on creation).
"""

from __future__ import annotations

import calendar
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from jarvis_logger import get_logger

log = get_logger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "maintenance.db"

SCHEDULE_TYPES = ("interval", "fixed_calendar", "log_only")
INTERVAL_UNITS = ("days", "months", "years")
# Within this many days of the due date a task counts as "soon".
SOON_DAYS = 7
_EARLY_GRACE_DAYS = 30

_SEEDS: tuple[tuple[str, int, str], ...] = (
    ("Rotate mattress", 6, "months"),
    ("Replace car scent", 3, "months"),
    ("Trim bonsai", 3, "months"),
)

_lock = Lock()
_schema_ready_paths: set[str] = set()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One serialized connection: commit on success, always close."""
    with _lock:
        conn = _connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the tables, and seed the starter tasks only on first creation
    so a task Nick deletes never comes back."""
    if str(_DB_PATH) in _schema_ready_paths:
        return
    existed = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'maintenance_tasks'"
    ).fetchone() is not None
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS maintenance_tasks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            schedule_type  TEXT    NOT NULL,
            interval_value INTEGER,
            interval_unit  TEXT,
            fixed_month    INTEGER,
            fixed_day      INTEGER,
            notes          TEXT    NOT NULL DEFAULT '',
            created_at     TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS maintenance_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id      INTEGER NOT NULL REFERENCES maintenance_tasks(id) ON DELETE CASCADE,
            completed_at TEXT    NOT NULL,
            note         TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_maintenance_log_task
            ON maintenance_log(task_id, completed_at);
        """
    )
    if not existed:
        stamp = _stamp()
        conn.executemany(
            "INSERT INTO maintenance_tasks (name, schedule_type, interval_value, interval_unit, created_at)"
            " VALUES (?, 'interval', ?, ?, ?)",
            [(name, value, unit, stamp) for name, value, unit in _SEEDS],
        )
    conn.commit()
    _schema_ready_paths.add(str(_DB_PATH))


def _stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).replace(microsecond=0).isoformat()


# --- Date math ---------------------------------------------------------------

def add_months(start: date, months: int) -> date:
    """Calendar months, clamping the day (Aug 31 + 6 months -> Feb 28/29)."""
    index = start.year * 12 + start.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _add_interval(start: date, value: int, unit: str) -> date:
    if unit == "days":
        return start + timedelta(days=value)
    if unit == "years":
        return add_months(start, value * 12)
    return add_months(start, value)


def _occurrence(year: int, month: int, day: int | None) -> date:
    return date(year, month, min(day or 1, calendar.monthrange(year, month)[1]))


def next_due(task: dict[str, Any], last_done: date | None, today: date) -> date | None:
    kind = task["schedule_type"]
    if kind == "interval":
        if last_done is None:
            return today
        return _add_interval(last_done, int(task["interval_value"]), str(task["interval_unit"]))
    if kind == "fixed_calendar":
        month, day = int(task["fixed_month"]), task["fixed_day"]
        upcoming = _occurrence(today.year, month, day)
        if upcoming < today:
            upcoming = _occurrence(today.year + 1, month, day)
        previous = _occurrence(upcoming.year - 1, month, day)

        def satisfied(due: date) -> bool:
            return last_done is not None and last_done >= due - timedelta(days=_EARLY_GRACE_DAYS)

        # A date that passed since the task existed and wasn't done is overdue.
        created = date.fromisoformat(str(task["created_at"])[:10])
        if created <= previous and not satisfied(previous):
            return previous
        if satisfied(upcoming):
            return _occurrence(upcoming.year + 1, month, day)
        return upcoming
    return None


def schedule_label(task: dict[str, Any]) -> str:
    kind = task["schedule_type"]
    if kind == "interval":
        value, unit = int(task["interval_value"]), str(task["interval_unit"])
        if value == 3 and unit == "months":
            return "Every quarter"
        return f"Every {unit[:-1]}" if value == 1 else f"Every {value} {unit}"
    if kind == "fixed_calendar":
        month = calendar.month_abbr[int(task["fixed_month"])]
        return f"Every {month} {task['fixed_day']}" if task["fixed_day"] else f"Every {month}"
    return "Log only"


def _status(days_remaining: int | None) -> str:
    if days_remaining is None:
        return "log"
    if days_remaining < 0:
        return "overdue"
    if days_remaining == 0:
        return "due"
    return "soon" if days_remaining <= SOON_DAYS else "ok"


# --- Validation ----------------------------------------------------------------

def _validated(fields: dict[str, Any]) -> dict[str, Any]:
    name = str(fields.get("name") or "").strip()
    if not name:
        raise ValueError("A task needs a name.")
    kind = str(fields.get("schedule_type") or "")
    if kind not in SCHEDULE_TYPES:
        raise ValueError(f"schedule_type must be one of {', '.join(SCHEDULE_TYPES)}.")
    clean: dict[str, Any] = {
        "name": name[:120],
        "schedule_type": kind,
        "interval_value": None,
        "interval_unit": None,
        "fixed_month": None,
        "fixed_day": None,
        "notes": str(fields.get("notes") or "").strip()[:2000],
    }
    if kind == "interval":
        value, unit = fields.get("interval_value"), fields.get("interval_unit")
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 3650:
            raise ValueError("interval_value must be a whole number from 1 to 3650.")
        if unit not in INTERVAL_UNITS:
            raise ValueError(f"interval_unit must be one of {', '.join(INTERVAL_UNITS)}.")
        clean.update(interval_value=value, interval_unit=unit)
    elif kind == "fixed_calendar":
        month, day = fields.get("fixed_month"), fields.get("fixed_day")
        if not isinstance(month, int) or not 1 <= month <= 12:
            raise ValueError("fixed_month must be 1-12.")
        if day is not None and (not isinstance(day, int) or not 1 <= day <= 31):
            raise ValueError("fixed_day must be 1-31.")
        clean.update(fixed_month=month, fixed_day=day)
    return clean


def _parse_day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError("Dates must be YYYY-MM-DD.") from exc


def _completion_stamp(on: date | None) -> str:
    """Now, or noon on a backdated day. Future days are refused."""
    if on is None or on == date.today():
        return _stamp()
    if on > date.today():
        raise ValueError("A completion can't be in the future.")
    return _stamp(datetime.combine(on, datetime.min.time()).replace(hour=12))


# --- Reads --------------------------------------------------------------------------

def _serialize(row: sqlite3.Row, today: date) -> dict[str, Any]:
    task = dict(row)
    last_at = task.pop("last_done")
    last_day = date.fromisoformat(last_at[:10]) if last_at else None
    due = next_due(task, last_day, today)
    days_remaining = (due - today).days if due else None
    task.update(
        last_done=last_at,
        days_since=(today - last_day).days if last_day else None,
        next_due=due.isoformat() if due else None,
        days_remaining=days_remaining,
        status=_status(days_remaining),
        schedule_label=schedule_label(task),
    )
    return task


_TASK_QUERY = """
    SELECT t.*,
           (SELECT completed_at FROM maintenance_log l WHERE l.task_id = t.id
             ORDER BY completed_at DESC, id DESC LIMIT 1) AS last_done,
           (SELECT note FROM maintenance_log l WHERE l.task_id = t.id
             ORDER BY completed_at DESC, id DESC LIMIT 1) AS last_note,
           (SELECT COUNT(*) FROM maintenance_log l WHERE l.task_id = t.id) AS log_count
      FROM maintenance_tasks t
"""


def list_tasks(today: date | None = None) -> list[dict[str, Any]]:
    """Every task, soonest due first; log-only tasks last, most recent first."""
    today = today or date.today()
    with _db() as conn:
        rows = conn.execute(_TASK_QUERY).fetchall()
    tasks = [_serialize(row, today) for row in rows]
    never = 10**6
    tasks.sort(key=lambda t: (
        t["days_remaining"] is None,
        t["days_remaining"] if t["days_remaining"] is not None
        else (never if t["days_since"] is None else t["days_since"]),
        t["name"].lower(),
    ))
    return tasks


def get_task(task_id: int, today: date | None = None) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(_TASK_QUERY + " WHERE t.id = ?", (task_id,)).fetchone()
    return _serialize(row, today or date.today()) if row else None


def due_within(days: int = SOON_DAYS, today: date | None = None) -> list[dict[str, Any]]:
    """Overdue and upcoming tasks for the Now feed."""
    return [t for t in list_tasks(today) if t["days_remaining"] is not None and t["days_remaining"] <= days]


def history(task_id: int) -> list[dict[str, Any]] | None:
    """Completions, newest first, or None for an unknown task."""
    with _db() as conn:
        if conn.execute("SELECT 1 FROM maintenance_tasks WHERE id = ?", (task_id,)).fetchone() is None:
            return None
        rows = conn.execute(
            "SELECT id, task_id, completed_at, note FROM maintenance_log WHERE task_id = ?"
            " ORDER BY completed_at DESC, id DESC",
            (task_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def find_task(name: str) -> dict[str, Any] | None:
    """Match a spoken name: exact first, then the unique substring match."""
    wanted = " ".join(name.lower().split())
    if not wanted:
        return None
    tasks = list_tasks()
    for task in tasks:
        if task["name"].lower() == wanted:
            return task
    words = wanted.split()
    matches = [t for t in tasks if wanted in t["name"].lower()] or [
        t for t in tasks if all(word in t["name"].lower() for word in words)
    ]
    return matches[0] if len(matches) == 1 else None


# --- Writes -------------------------------------------------------------------------

def create_task(fields: dict[str, Any]) -> dict[str, Any]:
    """Add a task. An optional ``last_done`` (YYYY-MM-DD) starts its log so
    a new task doesn't show as due today."""
    clean = _validated(fields)
    last_done = _parse_day(fields.get("last_done"))
    stamp = _completion_stamp(last_done) if last_done else None
    with _db() as conn:
        cursor = conn.execute(
            "INSERT INTO maintenance_tasks (name, schedule_type, interval_value, interval_unit,"
            " fixed_month, fixed_day, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (clean["name"], clean["schedule_type"], clean["interval_value"], clean["interval_unit"],
             clean["fixed_month"], clean["fixed_day"], clean["notes"], _stamp()),
        )
        task_id = int(cursor.lastrowid)
        if stamp:
            conn.execute(
                "INSERT INTO maintenance_log (task_id, completed_at, note) VALUES (?, ?, '')",
                (task_id, stamp),
            )
    log.info("Maintenance task %s added: %s", task_id, clean["name"])
    return get_task(task_id)  # type: ignore[return-value]


def update_task(task_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Replace a task's name, schedule and notes (merged over its current values)."""
    current = get_task(task_id)
    if current is None:
        return None
    clean = _validated({**current, **fields})
    with _db() as conn:
        conn.execute(
            "UPDATE maintenance_tasks SET name = ?, schedule_type = ?, interval_value = ?,"
            " interval_unit = ?, fixed_month = ?, fixed_day = ?, notes = ? WHERE id = ?",
            (clean["name"], clean["schedule_type"], clean["interval_value"], clean["interval_unit"],
             clean["fixed_month"], clean["fixed_day"], clean["notes"], task_id),
        )
    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    with _db() as conn:
        deleted = conn.execute("DELETE FROM maintenance_tasks WHERE id = ?", (task_id,)).rowcount > 0
    if deleted:
        log.info("Maintenance task %s deleted", task_id)
    return deleted


def complete_task(task_id: int, note: str = "", on: Any = None) -> dict[str, Any] | None:
    """Log a completion (now, or noon on a past day) and return the task
    with its reset countdown."""
    stamp = _completion_stamp(_parse_day(on))
    with _db() as conn:
        if conn.execute("SELECT 1 FROM maintenance_tasks WHERE id = ?", (task_id,)).fetchone() is None:
            return None
        conn.execute(
            "INSERT INTO maintenance_log (task_id, completed_at, note) VALUES (?, ?, ?)",
            (task_id, stamp, str(note or "").strip()[:1000]),
        )
    return get_task(task_id)


def delete_log_entry(task_id: int, entry_id: int) -> bool:
    """Undo one completion (a mistaken tap)."""
    with _db() as conn:
        return conn.execute(
            "DELETE FROM maintenance_log WHERE id = ? AND task_id = ?", (entry_id, task_id)
        ).rowcount > 0


# --- Spoken summaries ---------------------------------------------------------------

def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def countdown_text(task: dict[str, Any]) -> str:
    days = task["days_remaining"]
    if days is None:
        since = task["days_since"]
        if since is None:
            return "never logged"
        return "last done today" if since == 0 else f"last done {since} day{'s' if since != 1 else ''} ago"
    if days < 0:
        return f"overdue by {-days} day{'s' if days != -1 else ''}"
    if days == 0:
        return "due today" if task["last_done"] else "due now (never logged)"
    return "due tomorrow" if days == 1 else f"due in {days} days"


def describe_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No maintenance tasks are set up, sir."
    lines = [f"- {t['name']} (id {t['id']}, {_lower_first(t['schedule_label'])}): {countdown_text(t)}" for t in tasks]
    return "Maintenance, soonest first:\n" + "\n".join(lines)
