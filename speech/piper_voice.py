"""A local Piper voice, loaded once and kept in memory.

``TTS_PROVIDER=piper`` makes this JARVIS's voice (room speaker via
``speech/tts.py``, phone via ``speech/remote_voice.py``); ElevenLabs then
becomes the fallback. ``PIPER_VOICE_MODEL`` names the ``.onnx`` model (its
``.onnx.json`` config sits beside it): an absolute path, a path relative to
the project, or a bare voice name looked up in ``voices/``. Everything is
read live from the environment, so a ``.env`` edit swaps the voice without a
restart; each model is loaded once per process and cached by path.

Knobs (all optional; empty = the model's own defaults):
``PIPER_LENGTH_SCALE`` (>1 slower, <1 faster), ``PIPER_NOISE_SCALE``
(expressiveness), ``PIPER_NOISE_W`` (phoneme-length variation),
``PIPER_SPEAKER_ID`` (multi-speaker models only).

Supports both piper-tts APIs: 1.2.x (``synthesize_stream_raw``) and 1.3+
(``synthesize`` yielding ``AudioChunk``s with a ``SynthesisConfig``). If the
package is missing or the model can't be found, ``PiperUnavailable`` is
raised and callers fall back to another engine.

Not done yet: an optional light EQ for the films' digital sheen would go in
``pcm_chunks`` (filter each chunk before it is yielded).
"""
from __future__ import annotations

import os
import threading
import time
import wave
from collections.abc import Iterator
from pathlib import Path

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VOICE_DIR = _PROJECT_ROOT / "voices"

# The stock voice the old fallback used; only used when PIPER_VOICE_MODEL is empty.
FALLBACK_VOICE = "en_US-ryan-high"


class PiperUnavailable(RuntimeError):
    """Piper can't speak right now (not installed, no model, bad config)."""


_voices: dict[str, object] = {}
_load_lock = threading.Lock()
_warned: set[str] = set()
_warned_lock = threading.Lock()


def selected() -> bool:
    """True when Piper is JARVIS's main voice (``TTS_PROVIDER=piper``)."""
    return os.getenv("TTS_PROVIDER", "elevenlabs").strip().lower() == "piper"


def model_path(default: str = "") -> Path | None:
    """Where the configured ``.onnx`` model is (it may not exist)."""
    raw = os.getenv("PIPER_VOICE_MODEL", "").strip() or default
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.name.endswith(".onnx"):
        path = path.with_name(path.name + ".onnx")
    if path.is_absolute():
        return path
    if len(path.parts) == 1:  # a bare voice name: voices/<name>.onnx
        return _VOICE_DIR / path
    return _PROJECT_ROOT / path


def warn_once(reason: str) -> None:
    """Log a Piper failure loudly the first time, quietly after that."""
    with _warned_lock:
        first = reason not in _warned
        _warned.add(reason)
    if first:
        log.warning("Piper voice unavailable, using the fallback voice: %s", reason)
    else:
        log.debug("Piper voice unavailable: %s", reason)


def load(path: Path):  # noqa: ANN201 - a piper.PiperVoice
    """The ``PiperVoice`` for ``path``, loaded on first use and then cached."""
    key = str(path)
    voice = _voices.get(key)
    if voice is not None:
        return voice
    with _load_lock:
        voice = _voices.get(key)
        if voice is not None:
            return voice
        config = Path(f"{path}.json")
        if not path.is_file():
            raise PiperUnavailable(f"model not found: {path}")
        if not config.is_file():
            raise PiperUnavailable(f"model config not found: {config}")
        try:
            from piper import PiperVoice  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PiperUnavailable(f"piper-tts is not installed ({exc})") from None
        started = time.perf_counter()
        try:
            voice = PiperVoice.load(str(path), config_path=str(config))
        except Exception as exc:  # noqa: BLE001 - onnxruntime/json errors vary
            raise PiperUnavailable(f"could not load {path.name}: {exc}") from None
        log.info("Loaded Piper voice %s in %.0f ms.", path.name, (time.perf_counter() - started) * 1000)
        _voices[key] = voice
        return voice


def _float_env(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        log.debug("Ignoring %s=%r (not a number).", name, raw)
        return None


def settings() -> dict[str, float | int | None]:
    """The tuning knobs, read live. None leaves the model's default."""
    speaker = _float_env("PIPER_SPEAKER_ID")
    return {
        "speaker_id": int(speaker) if speaker is not None else None,
        "length_scale": _float_env("PIPER_LENGTH_SCALE"),
        "noise_scale": _float_env("PIPER_NOISE_SCALE"),
        "noise_w": _float_env("PIPER_NOISE_W"),
    }


def sample_rate(voice) -> int:  # noqa: ANN001
    return int(voice.config.sample_rate)


def pcm_chunks(voice, text: str) -> Iterator[bytes]:  # noqa: ANN001
    """16-bit mono PCM for ``text``, one chunk per sentence as it is made."""
    knobs = settings()
    stream_raw = getattr(voice, "synthesize_stream_raw", None)
    if callable(stream_raw):  # piper-tts 1.2.x
        kwargs = {name: value for name, value in knobs.items() if value is not None}
        for chunk in stream_raw(text, **kwargs):
            if chunk:
                yield bytes(chunk)
        return
    # piper-tts 1.3+: synthesize() yields AudioChunk objects.
    config = None
    try:
        import piper  # type: ignore[import-not-found]
        config_class = getattr(piper, "SynthesisConfig", None)
    except ImportError:
        config_class = None
    if config_class is not None:
        config = config_class(
            speaker_id=knobs["speaker_id"],
            length_scale=knobs["length_scale"],
            noise_scale=knobs["noise_scale"],
            noise_w_scale=knobs["noise_w"],
        )
    chunks = voice.synthesize(text, syn_config=config) if config is not None else voice.synthesize(text)
    for chunk in chunks:
        audio = getattr(chunk, "audio_int16_bytes", chunk)
        if audio:
            yield bytes(audio)


def open_stream(text: str, default_model: str = "") -> tuple[int, Iterator[bytes]]:
    """``(sample_rate, pcm chunks)`` for ``text`` with the configured voice.

    Raises ``PiperUnavailable`` before any audio if Piper can't speak."""
    path = model_path(default_model)
    if path is None:
        raise PiperUnavailable("PIPER_VOICE_MODEL is not set")
    voice = load(path)
    return sample_rate(voice), pcm_chunks(voice, text)


def write_wav(text: str, out: Path, default_model: str = "") -> int:
    """Synthesize the whole line into a WAV file; returns the sample rate."""
    rate, chunks = open_stream(text, default_model)
    try:
        pcm = b"".join(chunks)
    except Exception as exc:  # noqa: BLE001 - onnxruntime errors vary
        raise PiperUnavailable(f"synthesis failed: {exc}") from None
    if not pcm:
        raise PiperUnavailable("synthesis produced no audio")
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return rate


def resample_pcm16(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Linear resample of 16-bit mono PCM (the phone always gets 22.05 kHz)."""
    if source_rate == target_rate or not pcm:
        return pcm
    import numpy as np

    samples = np.frombuffer(pcm[: len(pcm) & ~1], dtype="<i2").astype(np.float32)
    if samples.size == 0:
        return b""
    count = max(1, round(samples.size * target_rate / source_rate))
    positions = np.linspace(0, samples.size - 1, count)
    out = np.interp(positions, np.arange(samples.size), samples)
    return np.clip(np.round(out), -32768, 32767).astype("<i2").tobytes()
