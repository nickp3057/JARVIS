"""The room voice's sounds on the Optiplex's speakers (ALSA ``aplay``).

Generated once into ``memory/sounds/`` (no asset files in git):

- ``listen``: a soft rising two-note when the wake word is heard.
- ``done``: a quick three-note jingle for a home command that worked (no
  words, like a smart speaker's confirmation).
- ``error``: a low falling two-note when something didn't work.

``AUDIO_OUTPUT_DEVICE`` picks the ALSA device (see ``speech/tts.py``).
"""
from __future__ import annotations

import math
from pathlib import Path
import struct
import subprocess
import sys
import wave

from jarvis_logger import get_logger
from speech.tts import alsa_device_args

log = get_logger(__name__)

_DIR = Path(__file__).resolve().parents[1] / "memory" / "sounds"
_RATE = 22050

# (frequency Hz, seconds) per note; soft sine with short fades, no clicks.
_SOUNDS = {
    "listen": [(660.0, 0.07), (990.0, 0.09)],
    "done": [(784.0, 0.07), (988.0, 0.07), (1319.0, 0.12)],
    "error": [(494.0, 0.10), (330.0, 0.16)],
}


def _render(name: str) -> Path:
    path = _DIR / f"{name}.wav"
    if path.exists():
        return path
    _DIR.mkdir(parents=True, exist_ok=True)
    samples: list[int] = []
    for freq, seconds in _SOUNDS[name]:
        count, fade = int(_RATE * seconds), int(_RATE * 0.012)
        for i in range(count):
            envelope = min(1.0, i / fade, (count - i) / fade)
            samples.append(int(math.sin(2 * math.pi * freq * i / _RATE) * envelope * 11000))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(_RATE)
        out.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return path


def play(name: str, wait: bool = False) -> None:
    """Play one of the sounds; never raises (a missing speaker is not fatal)."""
    try:
        path = _render(name)
        player = ["aplay", "-q", *alsa_device_args()] if sys.platform.startswith("linux") else ["afplay"]
        proc = subprocess.Popen([*player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait:
            proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        log.debug("room sound %s failed", name, exc_info=True)
