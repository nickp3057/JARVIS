"""Soft acknowledgment chime played when the wake word fires.

Generates a short two-tone beep procedurally (no asset file needed) on
first use, caches it as ``chime.wav`` at the project root, and plays it
via the stdlib ``winsound`` module for instant start.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CHIME_PATH = _PROJECT_ROOT / "chime.wav"

_SAMPLE_RATE = 16000
_TONE_DURATION_S = 0.07
_FADE_S = 0.015
_AMPLITUDE = 14000  # ~0.43 of int16 max - audible but not jarring


def _generate_tone(freq_hz: float, duration_s: float, fade_s: float) -> list[int]:
    samples_total = int(_SAMPLE_RATE * duration_s)
    fade_samples = int(_SAMPLE_RATE * fade_s)
    out: list[int] = []
    for i in range(samples_total):
        t = i / _SAMPLE_RATE
        value = math.sin(2.0 * math.pi * freq_hz * t)
        # Fade in/out to remove clicks at boundaries.
        if i < fade_samples:
            value *= i / fade_samples
        elif i > samples_total - fade_samples:
            value *= (samples_total - i) / fade_samples
        out.append(int(value * _AMPLITUDE))
    return out


def _ensure_chime_file() -> Path:
    if _CHIME_PATH.exists():
        return _CHIME_PATH

    # Two-tone rising beep: 880 Hz then 1320 Hz, each ~70ms.
    pcm = _generate_tone(880.0, _TONE_DURATION_S, _FADE_S) + _generate_tone(
        1320.0, _TONE_DURATION_S, _FADE_S
    )

    try:
        with wave.open(str(_CHIME_PATH), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(_SAMPLE_RATE)
            wav.writeframes(b"".join(struct.pack("<h", s) for s in pcm))
        log.info("Generated chime at %s", _CHIME_PATH)
    except OSError:
        log.exception("Failed to write chime WAV")

    return _CHIME_PATH


def play_chime() -> None:
    """Play the wake-word acknowledgment chime. Silent on failure."""
    path = _ensure_chime_file()
    if not path.exists():
        return

    try:
        import winsound

        # SND_ASYNC so we don't block while the chime plays - the user can
        # start talking immediately after the beep starts.
        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception:  # noqa: BLE001
        log.debug("Chime playback skipped (winsound unavailable or failed).")
