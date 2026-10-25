"""Session-bound receipts for safe Radarr and Sonarr writes."""

from __future__ import annotations

import time
from threading import RLock
from typing import Any

_TTL_SECONDS = 15 * 60
_lock = RLock()
_receipts: dict[tuple[str, str], tuple[float, dict[int, dict[str, Any]]]] = {}


def record(service: str, session_id: str | None, candidates: list[dict[str, Any]], id_key: str) -> None:
    """Replace a session's current candidate set after a successful lookup."""
    if not session_id:
        return
    allowed = {
        int(item[id_key]): dict(item)
        for item in candidates
        if item.get(id_key) is not None
    }
    with _lock:
        _receipts[(service, session_id)] = (time.monotonic(), allowed)


def validate(service: str, session_id: str | None, media_id: int) -> tuple[bool, str]:
    """Allow only an unexpired ID returned by this session's latest lookup."""
    if not session_id:
        return False, "Safety check failed: run a fresh lookup in this conversation first, sir."
    with _lock:
        receipt = _receipts.get((service, session_id))
        if receipt is None:
            return False, "Safety check failed: run a fresh lookup in this conversation first, sir."
        created, candidates = receipt
        if time.monotonic() - created > _TTL_SECONDS:
            _receipts.pop((service, session_id), None)
            return False, "That lookup expired, sir. Run it again so I can verify the exact title."
        if media_id not in candidates:
            # Echo what was sent (and what was on offer) so a mistyped ID is
            # visible. A miss never consumes the receipt, so a retry with the
            # right ID still works without a fresh lookup.
            offered = ", ".join(str(key) for key in list(candidates)[:5])
            return False, (
                f"Safety check failed: ID {media_id} was not in the latest results I showed you, sir"
                f"{f' (those were {offered})' if offered else ''}."
            )
    return True, ""


def candidate_count(service: str, session_id: str | None) -> int:
    """How many titles the session's latest lookup offered (0 if none)."""
    if not session_id:
        return 0
    with _lock:
        receipt = _receipts.get((service, session_id))
    return len(receipt[1]) if receipt else 0


def consume(service: str, session_id: str | None) -> None:
    if session_id:
        with _lock:
            _receipts.pop((service, session_id), None)


def clear_for_tests() -> None:
    with _lock:
        _receipts.clear()
