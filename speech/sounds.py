"""Procedural UI sounds for JARVIS (no asset files required).

Generates two short sounds on first use and caches them under ``sounds/``:
- ``boot.wav``  : an arc-reactor "power-up" swell with a rising sweep and a
  final ready-ping. Played at launch behind the boot sequence.
- ``blip.wav``  : a tiny high tick for UI feedback.

Playback uses winsound (Windows) asynchronously so it never blocks the UI.
On non-Windows hosts playback is a silent no-op.
"""

from __future__ import annotations

import wave
from pathlib import Path

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SOUND_DIR = _PROJECT_ROOT / "sounds"
_BOOT_PATH = _SOUND_DIR / "boot.wav"
_BLIP_PATH = _SOUND_DIR / "blip.wav"

_SAMPLE_RATE = 22050


def _write_wav(path: Path, samples) -> None:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(arr))) or 1.0
    pcm = np.int16(np.clip(arr / peak, -1.0, 1.0) * 30000)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())


def _generate_boot() -> None:
    import numpy as np

    dur = 1.4
    t = np.linspace(0, dur, int(_SAMPLE_RATE * dur), endpoint=False)

    # Rising frequency sweep (the spin-up).
    sweep_f = 58 + (210 - 58) * (t / dur) ** 1.6
    phase = 2 * np.pi * np.cumsum(sweep_f) / _SAMPLE_RATE
    hum = np.sin(phase) + 0.45 * np.sin(2 * phase) + 0.2 * np.sin(3 * phase)

    # Amplitude swell: attack, sustain, gentle release.
    env = np.minimum(t / 0.45, 1.0) * np.clip(1.0 - (t - 1.05) / 0.35, 0.25, 1.0)
    hum *= env

    # Ready-ping near the end (bright shimmer fading in then out).
    ping_env = np.clip((t - 0.95) / 0.15, 0, 1) * np.clip(1 - (t - 1.15) / 0.25, 0, 1)
    ping = 0.5 * np.sin(2 * np.pi * 1180 * t) * ping_env

    _write_wav(_BOOT_PATH, hum + ping)
    log.info("Generated boot sound at %s", _BOOT_PATH)


def _generate_blip() -> None:
    import numpy as np

    dur = 0.05
    t = np.linspace(0, dur, int(_SAMPLE_RATE * dur), endpoint=False)
    tone = np.sin(2 * np.pi * 1500 * t) * np.exp(-t / 0.012)
    _write_wav(_BLIP_PATH, tone)
    log.info("Generated blip sound at %s", _BLIP_PATH)


def _ensure(path: Path, gen) -> Path | None:
    if path.exists():
        return path
    try:
        gen()
        return path if path.exists() else None
    except Exception:  # noqa: BLE001
        log.debug("Sound generation failed for %s", path.name)
        return None


def _play_async(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    try:
        import winsound

        winsound.PlaySound(
            str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )
    except ImportError:
        pass  # Non-Windows: silent no-op.
    except Exception:  # noqa: BLE001
        log.debug("Sound playback failed for %s", path.name)


def play_boot() -> None:
    _play_async(_ensure(_BOOT_PATH, _generate_boot))


def play_blip() -> None:
    _play_async(_ensure(_BLIP_PATH, _generate_blip))
