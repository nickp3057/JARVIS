"""What the room voice is doing right now, for screens that show it.

The room voice (``speech/room_voice.py``) moves through phases:
``idle`` -> ``listening`` (wake word heard; ``transcript`` fills in live)
-> ``thinking`` (final transcript) -> ``speaking`` (``reply``) or ``done``
(a quick home command: a jingle, no words) -> ``idle``.

Deliberately not the event bus: live captions change several times a second
and would push real alerts out of its replay history. Here there is only the
latest state, and ``wait`` blocks until it changes, which is all the
``GET /api/voice/stream`` SSE endpoint (the iPad) needs.
"""
from __future__ import annotations

import threading
import time
from typing import Any

PHASES = ("idle", "listening", "thinking", "speaking", "done", "error")

_cond = threading.Condition()
_state: dict[str, Any] = {"seq": 0, "phase": "idle", "transcript": "", "reply": "", "at": 0.0}


def update(phase: str | None = None, *, transcript: str | None = None, reply: str | None = None) -> None:
    """Change any of phase / transcript / reply; a new ``listening`` clears both texts."""
    with _cond:
        if phase is not None:
            if phase not in PHASES:
                raise ValueError(f"unknown voice phase {phase!r}")
            if phase == "listening" and _state["phase"] != "listening":
                _state["transcript"], _state["reply"] = "", ""
            _state["phase"] = phase
        if transcript is not None:
            _state["transcript"] = transcript
        if reply is not None:
            _state["reply"] = reply
        _state["seq"] += 1
        _state["at"] = time.time()
        _cond.notify_all()


def current() -> dict[str, Any]:
    with _cond:
        return dict(_state)


def wait(after_seq: int, timeout: float) -> dict[str, Any]:
    """The state once its ``seq`` passes ``after_seq``, or as is after ``timeout``."""
    with _cond:
        _cond.wait_for(lambda: _state["seq"] > after_seq, timeout=timeout)
        return dict(_state)


def reset_for_tests() -> None:
    with _cond:
        _state.update(seq=0, phase="idle", transcript="", reply="", at=0.0)
