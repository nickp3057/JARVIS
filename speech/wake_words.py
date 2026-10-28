"""Taking "Hey JARVIS" out of what was heard, wherever it was said.

Shared by both voice front ends (``speech/voice_loop.py`` and
``speech/room_voice.py``): "Hey Jarvis, game time", "game time, Jarvis" and
"Jarvis game time" all become "game time".
"""
from __future__ import annotations

import re

_WAKE_PATTERNS = [
    re.compile(r"^\s*(?:hey|hi|hay|hi,)\s+jarvis[\s,.!?:;-]*", re.IGNORECASE),
    re.compile(r"[\s,.!?:;-]+(?:hey\s+|hi\s+)?jarvis[\s,.!?:;-]*$", re.IGNORECASE),
    re.compile(r"^\s*jarvis[\s,.!?:;-]+", re.IGNORECASE),
    re.compile(r"^\s*jarvis\s*$", re.IGNORECASE),
]


def strip_wake_word(text: str) -> str:
    cleaned = text or ""
    for pattern in _WAKE_PATTERNS:
        while True:
            new = pattern.sub(" ", cleaned).strip()
            if new == cleaned:
                break
            cleaned = new
    return cleaned.strip()
