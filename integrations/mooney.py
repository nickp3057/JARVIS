"""Mooney's money hub on the NAS (REMOTE_AND_MONEY_PLAN.md, Part 2).

The Mooney backend (``mooney/Backend``, Docker ``mooney-plaid-backend``,
:4001) holds the Plaid links and the shared ledger: transactions (Plaid sync
plus Apple Pay entries from the Shortcut), annotations both apps show, the
app's budget snapshot and JARVIS's advisor notes. JARVIS talks to it with
``MOONEY_JARVIS_TOKEN``, which can read spending and write notes and
annotations but can never link, disconnect or see a Plaid token.

Settings (.env): ``MOONEY_URL`` (e.g. ``http://100.x.y.z:4001``, the NAS),
``MOONEY_JARVIS_TOKEN``.
"""
from __future__ import annotations

import os
from typing import Any

import requests

_TIMEOUT_S = 15


class MooneyError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("MOONEY_URL", "").strip() and os.getenv("MOONEY_JARVIS_TOKEN", "").strip())


def _call(method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None,
          timeout: float = _TIMEOUT_S) -> Any:
    if not configured():
        raise MooneyError("Mooney isn't linked (MOONEY_URL / MOONEY_JARVIS_TOKEN).")
    url = os.getenv("MOONEY_URL", "").strip().rstrip("/") + path
    try:
        response = requests.request(method, url, params=params, json=body, timeout=timeout,
                                    headers={"Authorization": f"Bearer {os.getenv('MOONEY_JARVIS_TOKEN', '').strip()}"})
    except requests.RequestException as error:
        raise MooneyError(f"Mooney's backend didn't answer ({type(error).__name__}).") from error
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        raise MooneyError(f"Mooney's backend said {response.status_code}: {data.get('error', '')}".strip())
    return data


def accounts() -> list[dict[str, Any]]:
    """Linked items, each with its accounts (no tokens)."""
    return _call("GET", "/accounts").get("items", [])


def transactions(since: str, until: str | None = None) -> list[dict[str, Any]]:
    params = {"since": since, "limit": 5000}
    if until:
        params["until"] = until
    return _call("GET", "/transactions", params=params).get("transactions", [])


def sync() -> list[dict[str, Any]]:
    return _call("POST", "/sync", timeout=120).get("results", [])


def budget() -> dict[str, Any]:
    return _call("GET", "/budget")


def annotate(transaction_id: str, kind: str, amount: float | None = None, note: str = "") -> None:
    _call("POST", f"/transactions/{requests.utils.quote(transaction_id, safe='')}/annotation",
          body={"kind": kind, "amount": amount, "note": note or None})


def add_manual(*, entry_id: str, date: str, amount: float, merchant: str, card: str) -> str:
    return _call("POST", "/transactions/manual",
                 body={"id": entry_id, "date": date, "amount": amount, "merchant": merchant, "card": card}).get("id", "")


def add_note(kind: str, title: str, body: str, period: str = "") -> None:
    _call("POST", "/advisor/notes", body={"kind": kind, "title": title, "body": body, "period": period or None})


def notes(limit: int = 10) -> list[dict[str, Any]]:
    return _call("GET", "/advisor/notes", params={"limit": limit}).get("notes", [])
