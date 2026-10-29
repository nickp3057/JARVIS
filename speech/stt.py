"""Speech-to-text via faster-whisper.

A single :class:`Transcriber` instance lazy-loads the Whisper model on first
use (so import is cheap) and reuses it for every transcription. Audio in,
text out. Defaults to ``small.en`` at int8 on CPU - good accuracy, fast on
modern laptops, no GPU required. All knobs are env-overridable.
"""

from __future__ import annotations

import os
import time
from typing import Any

import numpy as np

from jarvis_logger import get_logger

log = get_logger(__name__)

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "small.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "1"))


def _temperature_fallback() -> bool:
    return os.getenv("WHISPER_TEMPERATURE_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}


def _decode_options() -> dict[str, Any]:
    """Decoding knobs for short voice commands.

    faster-whisper's default temperature schedule re-decodes the clip up to
    five more times whenever a pass looks low-confidence (compression ratio
    or log-prob thresholds), which short or noisy commands trip often. That
    is the main source of multi-second latency spikes on a CPU-only box, so
    the default is one deterministic pass; ``WHISPER_TEMPERATURE_FALLBACK=true``
    restores the library schedule. Timestamps and cross-segment conditioning
    are useless for a one-utterance command and cost decode time.
    """
    options: dict[str, Any] = {
        "without_timestamps": True,
        "condition_on_previous_text": False,
    }
    if not _temperature_fallback():
        options["temperature"] = 0.0
    return options


class Transcriber:
    """Wraps a faster-whisper model with lazy load and clean public API."""

    def __init__(self, model_size: str | None = None) -> None:
        self._model: Any | None = None
        # The room voice runs a tiny model for live captions beside the main one.
        self._model_size = model_size or WHISPER_MODEL_SIZE
        # Words Whisper should expect (the room's lights and scenes): a short
        # example sentence steers short commands toward them. WHISPER_PROMPT
        # overrides; "off" disables.
        self.prompt = ""

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # local import = cheap module load

        log.info(
            "Loading Whisper model %s on %s (%s)",
            self._model_size,
            WHISPER_DEVICE,
            WHISPER_COMPUTE_TYPE,
        )
        self._model = WhisperModel(
            self._model_size,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe an int16 or float32 numpy audio buffer to text."""
        if audio is None or audio.size == 0:
            return ""

        self._ensure_loaded()
        assert self._model is not None  # for type checkers

        # Whisper wants float32 in [-1.0, 1.0]
        if audio.dtype == np.int16:
            audio_f32 = audio.astype(np.float32) / 32768.0
        elif audio.dtype == np.float32:
            audio_f32 = audio
        else:
            audio_f32 = audio.astype(np.float32)
            if audio_f32.max() > 1.5 or audio_f32.min() < -1.5:
                audio_f32 = audio_f32 / 32768.0

        if sample_rate != 16000:
            log.warning("Whisper expects 16 kHz; got %d. Skipping resample.", sample_rate)

        started = time.monotonic()
        try:
            segments, _info = self._model.transcribe(
                audio_f32,
                language="en",
                beam_size=WHISPER_BEAM_SIZE,
                vad_filter=False,
                initial_prompt=self._initial_prompt(),
                **_decode_options(),
            )
            # segments is a lazy generator: decoding happens while joining.
            text = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception:  # noqa: BLE001
            log.exception("Whisper transcription failed")
            return ""

        # Per-utterance timing so latency can be measured on the real host.
        log.info(
            "STT %.2fs for %.1fs of audio (%s, %s)",
            time.monotonic() - started, audio_f32.size / 16000.0, self._model_size, WHISPER_COMPUTE_TYPE,
        )
        return text

    def _initial_prompt(self) -> str | None:
        override = os.getenv("WHISPER_PROMPT", "").strip()
        if override.lower() == "off":
            return None
        return override or self.prompt or None
