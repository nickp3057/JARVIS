"""Recurring household maintenance: a countdown to the next time and a log
of every time it was done.

Three schedule types:
- ``interval``: due ``interval_value`` days/months/years after the last
  completion; never done means due today.
- ``fixed_calendar``: due on set calendar dates, every ``interval_value``
  months (1, 2, 3, 4, 6 or 12; 12 when unset) anchored on ``fixed_month`` and
  ``fixed_day`` (day 31 = the month's last day). Quarterly from Jan 1 is Jan 1,
  Apr 1, Jul 1, Oct 1. Doing it a little before a date (``_grace_days``)
  counts for that date; a missed date stays overdue until it's done. An
  optional season (``season_start``..``season_end`` months, may wrap the
  new year) skips dates outside it, e.g. fertilizing only Mar-Oct.
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
# Months between dates for a calendar schedule: they must divide the year.
CALENDAR_PERIODS = (1, 2, 3, 4, 6, 12)
_PERIOD_NAMES = {1: "Monthly", 2: "Every 2 months", 3: "Quarterly", 4: "Every 4 months",
                 6: "Twice a year", 12: "Every year"}
_SCHEMA_VERSION = 2

# (name, months between, anchored on Jan 1)
_SEEDS: tuple[tuple[str, int], ...] = (
    ("Rotate mattress", 6),
    ("Replace car scent", 3),
    ("Trim bonsai", 3),
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
            season_start   INTEGER,
            season_end     INTEGER,
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(maintenance_tasks)")}
    for column in ("season_start", "season_end"):
        if column not in columns:
            conn.execute(f"ALTER TABLE maintenance_tasks ADD COLUMN {column} INTEGER")
    if not existed:
        stamp = _stamp()
        conn.executemany(
            "INSERT INTO maintenance_tasks (name, schedule_type, interval_value, fixed_month, fixed_day,"
            " created_at) VALUES (?, 'fixed_calendar', ?, 1, 1, ?)",
            [(name, period, stamp) for name, period in _SEEDS],
        )
    elif conn.execute("PRAGMA user_version").fetchone()[0] < 1:
        # 2026-11-19: Nick wants quarterly chores due Jan 1, Apr 1, Jul 1,
        # Oct 1 and twice-yearly ones on Jan 1 / Jul 1, not counted from the
        # last time. Rolling schedules are still there to switch back to.
        moved = conn.execute(
            "UPDATE maintenance_tasks SET schedule_type = 'fixed_calendar', interval_unit = NULL,"
            " fixed_month = 1, fixed_day = 1"
            " WHERE schedule_type = 'interval' AND interval_unit = 'months' AND interval_value IN (3, 6)"
        ).rowcount
        if moved:
            log.info("Maintenance: moved %d quarterly/twice-yearly task(s) onto calendar dates", moved)
    conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
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


def calendar_period(task: dict[str, Any]) -> int:
    """Months between a calendar task's dates (older rows had none: yearly)."""
    value = task.get("interval_value")
    return int(value) if value in CALENDAR_PERIODS else 12


def _grace_days(period: int) -> int:
    """How early a completion still counts for the coming date: a week for
    monthly, three weeks for quarterly, a month for twice a year or yearly."""
    return min(30, period * 7)


def in_season(task: dict[str, Any], month: int) -> bool:
    start, end = task.get("season_start"), task.get("season_end")
    if not start or not end:
        return True
    return start <= month <= end if start <= end else (month >= start or month <= end)


def calendar_months(task: dict[str, Any]) -> list[int]:
    """The months a calendar task falls in (within its season), January first."""
    period = calendar_period(task)
    first = (int(task["fixed_month"]) - 1) % period + 1
    return [m for m in range(first, 13, period) if in_season(task, m)]


def season_text(task: dict[str, Any]) -> str:
    start, end = task.get("season_start"), task.get("season_end")
    if not start or not end:
        return ""
    return f"{calendar.month_abbr[start]}\u2013{calendar.month_abbr[end]}"


def _month_occurrence(index: int, day: int | None) -> date:
    year, month = divmod(index, 12)
    return _occurrence(year, month + 1, day)


def next_due(task: dict[str, Any], last_done: date | None, today: date) -> date | None:
    kind = task["schedule_type"]
    if kind == "interval":
        if last_done is None:
            return today
        return _add_interval(last_done, int(task["interval_value"]), str(task["interval_unit"]))
    if kind == "fixed_calendar":
        period, day = calendar_period(task), task["fixed_day"]
        residue = (int(task["fixed_month"]) - 1) % period
        # Month indexes (year * 12 + month - 1) share the anchor's residue
        # because every period divides 12.
        if not calendar_months(task):
            return None  # a season that holds none of its dates

        def step(index: int, direction: int) -> int:
            """The nearest in-season date index from ``index`` onward (or back)."""
            while not in_season(task, index % 12 + 1):
                index += direction * period
            return index

        index = today.year * 12 + today.month - 1
        index += (residue - index) % period
        if _month_occurrence(index, day) < today:
            index += period
        index = step(index, 1)
        upcoming = _month_occurrence(index, day)
        previous = _month_occurrence(step(index - period, -1), day)
        grace = timedelta(days=_grace_days(period))

        def satisfied(due: date) -> bool:
            return last_done is not None and last_done >= due - grace

        # A date that passed since the task existed and wasn't done is overdue.
        created = date.fromisoformat(str(task["created_at"])[:10])
        if created <= previous and not satisfied(previous):
            return previous
        if satisfied(upcoming):
            return _month_occurrence(step(index + period, 1), day)
        return upcoming
    return None


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def calendar_dates_text(task: dict[str, Any]) -> str:
    """"Jan 1, Apr 1, Jul 1, Oct 1" or "the last day of Jan, Jul"."""
    names = [calendar.month_abbr[m] for m in calendar_months(task)]
    day = task["fixed_day"]
    if day == 31:
        return "the last day of " + ", ".join(names)
    return ", ".join(f"{name} {day}" if day else name for name in names)


def schedule_label(task: dict[str, Any]) -> str:
    kind = task["schedule_type"]
    if kind == "interval":
        value, unit = int(task["interval_value"]), str(task["interval_unit"])
        return f"Every {unit[:-1]}" if value == 1 else f"Every {value} {unit}"
    if kind == "fixed_calendar":
        period, day = calendar_period(task), task["fixed_day"]
        season = f", {season_text(task)}" if season_text(task) else ""
        if period == 1:
            if day == 31:
                return "Monthly on the last day" + season
            return f"Monthly on the {_ordinal(day or 1)}{season}"
        if period == 12:
            return f"Every year on {calendar_dates_text(task)}" if day == 31 else f"Every {calendar_dates_text(task)}"
        return f"{_PERIOD_NAMES[period]} ({calendar_dates_text(task)})"
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
        "season_start": None,
        "season_end": None,
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
        period = fields.get("interval_value")
        if period is None:
            period = 12
        if period not in CALENDAR_PERIODS or isinstance(period, bool):
            raise ValueError("For calendar dates, interval_value is the months between them: 1, 2, 3, 4, 6 or 12.")
        if not isinstance(month, int) or not 1 <= month <= 12:
            raise ValueError("fixed_month must be 1-12.")
        if day is not None and (not isinstance(day, int) or not 1 <= day <= 31):
            raise ValueError("fixed_day must be 1-31.")
        start, end = fields.get("season_start"), fields.get("season_end")
        if (start is None) != (end is None):
            raise ValueError("A season needs both season_start and season_end (months 1-12).")
        if start is not None:
            if not all(isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12 for m in (start, end)):
                raise ValueError("season_start and season_end must be months 1-12.")
            if not calendar_months({"interval_value": period, "fixed_month": month,
                                    "season_start": start, "season_end": end}):
                raise ValueError("None of the task's dates fall in that season.")
        # Store the anchor as the year's first date in the cycle.
        clean.update(interval_value=period, fixed_month=(month - 1) % period + 1, fixed_day=day,
                     season_start=start, season_end=end)
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
            " fixed_month, fixed_day, season_start, season_end, notes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (clean["name"], clean["schedule_type"], clean["interval_value"], clean["interval_unit"],
             clean["fixed_month"], clean["fixed_day"], clean["season_start"], clean["season_end"],
             clean["notes"], _stamp()),
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
            " interval_unit = ?, fixed_month = ?, fixed_day = ?, season_start = ?, season_end = ?,"
            " notes = ? WHERE id = ?",
            (clean["name"], clean["schedule_type"], clean["interval_value"], clean["interval_unit"],
             clean["fixed_month"], clean["fixed_day"], clean["season_start"], clean["season_end"],
             clean["notes"], task_id),
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
