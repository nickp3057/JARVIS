"""Speech-to-text via faster-whisper, or a cloud service for the room's commands.

A single :class:`Transcriber` instance lazy-loads the Whisper model on first
use (so import is cheap) and reuses it for every transcription. Audio in,
text out. Defaults to ``small.en`` at int8 on CPU - good accuracy, fast on
modern laptops, no GPU required. All knobs are env-overridable.

``STT_PROVIDER=elevenlabs`` (Scribe, reuses ``ELEVENLABS_API_KEY``) or
``openai`` (``OPENAI_API_KEY``) sends the main transcriber's audio, which is
the command after "Hey JARVIS", to the cloud instead: much better than a small
Whisper model in a noisy room. The wake word and the live-caption model stay
local. A cloud failure or a slow reply (``STT_TIMEOUT_S``, 6 s) falls back to
local Whisper for that utterance. ``STT_MODEL`` overrides the cloud model
(defaults ``scribe_v1`` / ``gpt-4o-mini-transcribe``).
"""

from __future__ import annotations

import io
import os
import time
import wave
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


_CLOUD_DEFAULT_MODELS = {"elevenlabs": "scribe_v1", "openai": "gpt-4o-mini-transcribe"}


def cloud_provider() -> str:
    """The configured cloud STT provider with its key present, else ""."""
    provider = os.getenv("STT_PROVIDER", "local").strip().lower()
    key = {"elevenlabs": "ELEVENLABS_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
    return provider if key and os.getenv(key, "").strip() else ""


def _wav_bytes(audio_f32: np.ndarray) -> bytes:
    pcm = (np.clip(audio_f32, -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(pcm.tobytes())
    return buffer.getvalue()


def cloud_transcribe(audio_f32: np.ndarray, provider: str, prompt: str | None = None) -> str | None:
    """Text from the cloud provider, or None on any failure (caller falls back)."""
    import requests

    model = os.getenv("STT_MODEL", "").strip() or _CLOUD_DEFAULT_MODELS[provider]
    timeout = float(os.getenv("STT_TIMEOUT_S", "6"))
    files = {"file": ("command.wav", _wav_bytes(audio_f32), "audio/wav")}
    try:
        if provider == "elevenlabs":
            response = requests.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": os.getenv("ELEVENLABS_API_KEY", "").strip()},
                data={"model_id": model, "language_code": "en", "tag_audio_events": "false"},
                files=files, timeout=timeout,
            )
        else:
            data = {"model": model, "language": "en"}
            if prompt:
                data["prompt"] = prompt  # the room's words, as for local Whisper
            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '').strip()}"},
                data=data, files=files, timeout=timeout,
            )
        response.raise_for_status()
        return str(response.json().get("text", "")).strip()
    except Exception as exc:  # noqa: BLE001 - any failure means local Whisper
        log.warning("Cloud STT (%s) failed, using local Whisper: %s", provider, exc)
        return None


class Transcriber:
    """Wraps a faster-whisper model with lazy load and clean public API."""

    def __init__(self, model_size: str | None = None) -> None:
        self._model: Any | None = None
        # The room voice runs a tiny model for live captions beside the main one.
        self._model_size = model_size or WHISPER_MODEL_SIZE
        # Only the main transcriber goes to the cloud; live captions stay local.
        self._cloud_ok = model_size is None
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

        # Whisper wants float32 in [-1.0, 1.0]
        if audio.dtype == np.int16:
            audio_f32 = audio.astype(np.float32) / 32768.0
        elif audio.dtype == np.float32:
            audio_f32 = audio
        else:
            audio_f32 = audio.astype(np.float32)
            if audio_f32.max() > 1.5 or audio_f32.min() < -1.5:
                audio_f32 = audio_f32 / 32768.0

        provider = cloud_provider() if self._cloud_ok else ""
        if provider:
            if float(np.abs(audio_f32).max()) < 0.003:
                return ""  # silence (the warm-up clip): nothing to send
            started = time.monotonic()
            text = cloud_transcribe(audio_f32, provider, self._initial_prompt())
            if text is not None:
                log.info("STT %.2fs for %.1fs of audio (%s cloud)",
                         time.monotonic() - started, audio_f32.size / 16000.0, provider)
            if text or (text == "" and audio_f32.size < 1.5 * 16000):
                return text  # a short blip the cloud calls empty is just noise
            if text == "":
                # Cloud heard nothing in a real utterance: let local Whisper
                # try rather than ignore Nick.
                log.info("Cloud STT returned nothing; trying local Whisper")

        self._ensure_loaded()
        assert self._model is not None  # for type checkers

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
