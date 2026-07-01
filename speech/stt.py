"""Speech-to-text via faster-whisper.

A single :class:`Transcriber` instance lazy-loads the Whisper model on first
use (so import is cheap) and reuses it for every transcription. Audio in,
text out. Defaults to ``small.en`` at int8 on CPU - good accuracy, fast on
modern laptops, no GPU required. All knobs are env-overridable.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

from jarvis_logger import get_logger

log = get_logger(__name__)

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "small.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "1"))


class Transcriber:
    """Wraps a faster-whisper model with lazy load and clean public API."""

    def __init__(self) -> None:
        self._model: Any | None = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # local import = cheap module load

        log.info(
            "Loading Whisper model %s on %s (%s)",
            WHISPER_MODEL_SIZE,
            WHISPER_DEVICE,
            WHISPER_COMPUTE_TYPE,
        )
        self._model = WhisperModel(
            WHISPER_MODEL_SIZE,
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

        try:
            segments, _info = self._model.transcribe(
                audio_f32,
                language="en",
                beam_size=WHISPER_BEAM_SIZE,
                vad_filter=False,
            )
        except Exception:  # noqa: BLE001
            log.exception("Whisper transcription failed")
            return ""

        return " ".join(seg.text.strip() for seg in segments).strip()
