"""What each agent spends on the model: a ledger of every API call's tokens
and an estimated dollar cost, for the companion's Agents page.

``record(agent, model, usage)`` is called after every Messages API response
(JARVIS's conversation rounds, the memory keeper, study coach, vision,
subagents, Donna). Costs are *estimates* from the public per-token list
prices below: prompt-cache reads and writes, Haiku's long-prompt tier and
server web searches included; nothing is fetched from Anthropic's billing.

Database: memory/usage.db (one row per call; only counts, never content).
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

_DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "usage.db"
_lock = Lock()
_ready: set[str] = set()

# $ per million tokens: (input, output, cache read). Cache writes are billed
# at 1.25x input (5-minute cache). Matched by model-id prefix, longest first.
_PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-2": (8.0, 40.0, 0.20),
    "claude-mythos-5-2": (8.0, 40.0, 0.20),
    "claude-fable-5-1": (10.0, 50.0, 1.00),
    "claude-fable-5": (10.0, 50.0, 1.00),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4": (1.0, 5.0, 0.10),
}
# Haiku 5.5 above 100K prompt tokens.
_HAIKU_LONG = (0.50, 2.50, 0.05)
_WEB_SEARCH_USD = 10.0 / 1000
_CACHE_WRITE_FACTOR = 1.25


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    if str(_DB_PATH) not in _ready:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at REAL NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL,
                input INTEGER NOT NULL, cache_read INTEGER NOT NULL,
                cache_write INTEGER NOT NULL, output INTEGER NOT NULL,
                searches INTEGER NOT NULL DEFAULT 0, usd REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS calls_at ON calls(at);
            """
        )
        _ready.add(str(_DB_PATH))
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


def _price(model: str, prompt_tokens: int) -> tuple[float, float, float]:
    model = re.sub(r"-\d{8}$", "", (model or "").lower())
    if model.startswith("claude-haiku-5-5") and prompt_tokens > 100_000:
        return _HAIKU_LONG
    for prefix in sorted(_PRICES, key=len, reverse=True):
        if model.startswith(prefix):
            return _PRICES[prefix]
    return _PRICES["claude-sonnet-5-5"]  # unknown: a middle-of-the-road guess


def _n(usage: Any, key: str) -> int:
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _searches(usage: Any) -> int:
    server = usage.get("server_tool_use") if isinstance(usage, dict) else getattr(usage, "server_tool_use", None)
    return _n(server, "web_search_requests") if server is not None else 0


def cost(model: str, input_tokens: int, cache_read: int, cache_write: int, output: int, searches: int = 0) -> float:
    price_in, price_out, price_read = _price(model, input_tokens + cache_read + cache_write)
    return (input_tokens * price_in + cache_write * price_in * _CACHE_WRITE_FACTOR
            + cache_read * price_read + output * price_out) / 1_000_000 + searches * _WEB_SEARCH_USD


def record(agent: str, model: str, usage: Any) -> None:
    """Log one API response's usage. Never raises: bookkeeping can't break a turn."""
    if usage is None:
        return
    try:
        values = (_n(usage, "input_tokens"), _n(usage, "cache_read_input_tokens"),
                  _n(usage, "cache_creation_input_tokens"), _n(usage, "output_tokens"), _searches(usage))
        usd = cost(model, *values)
        with _db() as conn:
            conn.execute("INSERT INTO calls (at, agent, model, input, cache_read, cache_write, output, searches, usd)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (time.time(), agent, model or "?", *values, usd))
    except Exception as exc:  # noqa: BLE001
        log.warning("Usage not recorded for %s: %s", agent, exc)


def record_response(agent: str, response: Any) -> None:
    """``record`` straight from a Messages API response (its model and usage)."""
    get = (lambda k: response.get(k)) if isinstance(response, dict) else (lambda k: getattr(response, k, None))
    record(agent, str(get("model") or ""), get("usage"))


def summary(days: float | None) -> dict[str, Any]:
    """Spend per agent name since ``days`` ago (None = everything), plus a
    per-day total for the chart."""
    since = time.time() - days * 86_400 if days else 0.0
    with _db() as conn:
        rows = conn.execute(
            "SELECT agent, COUNT(*), SUM(input + cache_read + cache_write), SUM(output), SUM(usd),"
            " GROUP_CONCAT(DISTINCT model) FROM calls WHERE at >= ? GROUP BY agent", (since,)).fetchall()
        daily = conn.execute(
            "SELECT date(at, 'unixepoch', 'localtime') AS day, SUM(usd) FROM calls WHERE at >= ?"
            " GROUP BY day ORDER BY day", (since,)).fetchall()
        first = conn.execute("SELECT MIN(at) FROM calls").fetchone()[0]
    agents = {r[0]: {"calls": r[1], "input_tokens": r[2] or 0, "output_tokens": r[3] or 0,
                     "usd": round(r[4] or 0.0, 4), "models": sorted(set((r[5] or "").split(",")) - {""})}
              for r in rows}
    return {"since": since or first, "tracking_since": first, "agents": agents,
            "daily": [{"day": d, "usd": round(u or 0.0, 4)} for d, u in daily]}
