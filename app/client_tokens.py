"""Per-client API tokens with limited scopes (TV, dashboards, future clients).

``JARVIS_API_TOKEN`` stays the admin token for the iOS companion and keeps
full access. Every other client gets its own token here, limited to scopes:

- ``events:read``  live alert stream (``GET /api/clients/events``), answer
                   cards included (``event: card``, brain/answer_cards.py)
- ``status:read``  read-only dashboard/status endpoints
- ``chat``         talk to JARVIS (``POST /chat``), e.g. the TV remote's mic.
                   JARVIS's own confirmation rules still apply to risky tools.
- ``settings:write`` change shared look-and-feel (``PUT /api/theme``), e.g.
                   the TV's Settings page. Nothing else.
- ``wake:write``   I'm up on the TV's wake screen (``POST /api/wake/awake``),
                   which also turns on the good-morning lights.
- ``playback:write`` the TV's Plex position for light sync
                   (``POST /api/tv/playback``). Nothing else.
- ``money:write``  the iPhone's Apple Pay Shortcut: one payment
                   (``POST /api/money/transaction``). Nothing else, not even reads.

Only a SHA-256 of each token is stored (``memory/client_tokens.json``,
gitignored); the token itself is printed once at creation. Manage from the
Surface shell::

    .venv/bin/python -m app.client_tokens create living-room-tv events:read status:read
    .venv/bin/python -m app.client_tokens grant nicks-room-tv wake:write
    .venv/bin/python -m app.client_tokens list
    .venv/bin/python -m app.client_tokens revoke living-room-tv
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sys
import threading
import time

SCOPES = frozenset({"events:read", "status:read", "chat", "settings:write", "wake:write", "playback:write",
                    "money:write"})
_FILE = Path(__file__).resolve().parents[1] / "memory" / "client_tokens.json"
_lock = threading.Lock()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load() -> dict[str, dict]:
    try:
        data = json.loads(_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, dict]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.chmod(0o600)
    temporary.replace(_FILE)


def create(name: str, scopes: list[str]) -> str:
    """Create (or rotate) ``name``'s token; returns the token once."""
    name = name.strip()
    unknown = set(scopes) - SCOPES
    if not name or not scopes or unknown:
        raise ValueError(f"need a name and scopes from {sorted(SCOPES)}; unknown: {sorted(unknown)}")
    token = "jc_" + secrets.token_urlsafe(32)
    with _lock:
        data = _load()
        data[name] = {"sha256": _digest(token), "scopes": sorted(set(scopes)), "created_at": time.time()}
        _save(data)
    return token


def revoke(name: str) -> bool:
    with _lock:
        data = _load()
        removed = data.pop(name.strip(), None) is not None
        if removed:
            _save(data)
    return removed


def grant(name: str, scopes: list[str]) -> bool:
    """Add scopes to an existing client, keeping its token. False if no such client."""
    unknown = set(scopes) - SCOPES
    if not scopes or unknown:
        raise ValueError(f"need scopes from {sorted(SCOPES)}; unknown: {sorted(unknown)}")
    with _lock:
        data = _load()
        if name not in data:
            return False
        data[name]["scopes"] = sorted(set(data[name].get("scopes", [])) | set(scopes))
        _save(data)
    return True


def listing() -> dict[str, list[str]]:
    return {name: entry.get("scopes", []) for name, entry in _load().items()}


def scopes_for(token: str) -> tuple[str, frozenset[str]] | None:
    """``(client name, scopes)`` for a valid client token, else None."""
    if not token:
        return None
    digest = _digest(token)
    for name, entry in _load().items():
        if hmac.compare_digest(str(entry.get("sha256", "")), digest):
            return name, frozenset(entry.get("scopes", [])) & SCOPES
    return None


def _main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "create":
        token = create(argv[1], argv[2:])
        print(f"Token for {argv[1]} (shown once, store it on the client):\n{token}")
        return 0
    if len(argv) >= 3 and argv[0] == "grant":
        print("granted" if grant(argv[1], argv[2:]) else "no such client")
        return 0
    if argv[:1] == ["list"]:
        for name, scopes in listing().items():
            print(f"{name}: {', '.join(scopes)}")
        return 0
    if len(argv) == 2 and argv[0] == "revoke":
        print("revoked" if revoke(argv[1]) else "no such client")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
