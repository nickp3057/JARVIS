"""Nick's voiceprint: is the voice on the room mic Nick?

Off unless ``VOICE_ID=1``. A speaker-embedding model (sherpa-onnx with NeMo
TitaNet-small, ``VOICE_ID_MODEL``) turns each room-voice command into a
192-number vector; the cosine similarity to Nick's enrolled average decides
(``VOICE_ID_THRESHOLD``, default 0.55; every verdict is logged with its
score so the threshold can be tuned). Audio is never stored; only the
vectors are, in ``memory/voiceprint.json``.

Verdicts (``identify``):

- ``VOICE_ID`` off, or nothing enrolled yet: Nick (same as before voice ID),
  except for money: trades by room voice need a real match.
- enrolled, but the model can't load or the clip is too short to judge:
  guest. Locked tools fail closed rather than open.
- otherwise: Nick when the score clears the threshold, else guest.

Enrolling: the ``voice_id`` tool (``action="enroll"``), best typed in the
phone app, arms ``start_enrollment``. The next ``VOICE_ID_SAMPLES`` (5) room
commands are taken as samples instead of commands (``take_sample``), each
answered with a count, with no wake word needed between them. Enrolling
again replaces the old print.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any

import numpy as np

from brain import voice_trust
from jarvis_logger import get_logger

log = get_logger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_PRINT = _ROOT / "memory" / "voiceprint.json"
_DEFAULT_MODEL = _ROOT / "models" / "speaker" / "nemo_en_titanet_small.onnx"
_SAMPLE_RATE = 16000
_MIN_SECONDS = 0.8
_ENROLL_TTL_S = 600

# Re-entrant: take_sample holds it and may call available() -> _load_extractor().
_lock = threading.RLock()
_extractor: Any = None
_extractor_path: str | None = None
_enrolling: dict[str, Any] | None = None  # {"needed": n, "samples": [vec], "until": t}


def enabled() -> bool:
    return os.getenv("VOICE_ID", "0").strip().lower() in ("1", "on", "true", "yes")


def _threshold() -> float:
    try:
        return float(os.getenv("VOICE_ID_THRESHOLD", "0.55"))
    except ValueError:
        return 0.55


def _sample_count() -> int:
    try:
        return max(2, min(12, int(os.getenv("VOICE_ID_SAMPLES", "5"))))
    except ValueError:
        return 5


def model_path() -> Path:
    configured = os.getenv("VOICE_ID_MODEL", "").strip()
    path = Path(configured).expanduser() if configured else _DEFAULT_MODEL
    return path if path.is_absolute() else _ROOT / path


def _load_extractor() -> Any:
    """The sherpa-onnx extractor, loaded once per model path; None if unavailable."""
    global _extractor, _extractor_path
    path = str(model_path())
    with _lock:
        if _extractor is not None and _extractor_path == path:
            return _extractor
        try:
            import sherpa_onnx
        except ImportError:
            log.warning("Voice ID: sherpa-onnx is not installed (pip install sherpa-onnx)")
            return None
        if not Path(path).is_file():
            log.warning("Voice ID: no speaker model at %s (see JARVIS.md, Voice ID)", path)
            return None
        try:
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=path, num_threads=1)
            _extractor, _extractor_path = sherpa_onnx.SpeakerEmbeddingExtractor(config), path
        except Exception:  # noqa: BLE001 - a bad model file must not take the room voice down
            log.exception("Voice ID: could not load %s", path)
            return None
        return _extractor


def available() -> bool:
    return _load_extractor() is not None


def embed(audio: np.ndarray) -> np.ndarray | None:
    """Unit-length voice vector for 16 kHz int16 (or float) audio; None if too short or no model."""
    samples = np.asarray(audio)
    if samples.size < _SAMPLE_RATE * _MIN_SECONDS:
        return None
    extractor = _load_extractor()
    if extractor is None:
        return None
    wave = samples.astype(np.float32)
    if samples.dtype == np.int16:
        wave /= 32768.0
    try:
        stream = extractor.create_stream()
        stream.accept_waveform(_SAMPLE_RATE, wave)
        stream.input_finished()
        vector = np.asarray(extractor.compute(stream), dtype=np.float32)
    except Exception:  # noqa: BLE001
        log.exception("Voice ID: embedding failed")
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


# --- the stored print ----------------------------------------------------------

def _load_print() -> dict[str, Any] | None:
    try:
        data = json.loads(_PRINT.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.exception("Voice ID: unreadable %s", _PRINT)
        return None
    if data.get("model") != model_path().name or not data.get("centroid"):
        return None  # enrolled with another model: vectors aren't comparable
    return data


def enrolled() -> bool:
    return _load_print() is not None


def _save_print(samples: list[np.ndarray]) -> None:
    centroid = np.mean(samples, axis=0)
    centroid /= np.linalg.norm(centroid)
    _PRINT.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PRINT.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "model": model_path().name,
        "samples": len(samples),
        "centroid": [round(float(x), 6) for x in centroid],
        "enrolled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }))
    tmp.replace(_PRINT)


def reset() -> bool:
    try:
        _PRINT.unlink()
        return True
    except FileNotFoundError:
        return False


# --- identify ------------------------------------------------------------------

def score(audio: np.ndarray) -> float | None:
    data = _load_print()
    vector = embed(audio) if data else None
    if data is None or vector is None:
        return None
    return float(vector @ np.asarray(data["centroid"], dtype=np.float32))


def identify(audio: np.ndarray) -> voice_trust.Speaker:
    """Nick or a guest, for one room-voice command. Every verdict is marked
    ``room``: an unmeasured "Nick" (voice ID off, nothing enrolled) still
    can't move money (``voice_trust.may_move_money``)."""
    if not enabled():
        return voice_trust.Speaker("nick", True, reason="voice ID off", room=True)
    if not enrolled():
        return voice_trust.Speaker("nick", True, reason="no voiceprint enrolled", room=True)
    value = score(audio)
    if value is None:
        reason = "clip too short" if np.asarray(audio).size < _SAMPLE_RATE * _MIN_SECONDS else "model unavailable"
        log.info("Voice ID: guest (%s)", reason)
        return voice_trust.guest(reason)
    verdict = value >= _threshold()
    log.info("Voice ID: %s (score %.2f, threshold %.2f)", "Nick" if verdict else "guest", value, _threshold())
    if verdict:
        return voice_trust.Speaker("nick", True, score=value, reason="voiceprint match", room=True)
    return voice_trust.guest("voiceprint mismatch", score=value)


# --- enrolment -----------------------------------------------------------------

def start_enrollment() -> int:
    """Arm enrolment: the next N room commands become samples. Returns N."""
    global _enrolling
    needed = _sample_count()
    with _lock:
        _enrolling = {"needed": needed, "samples": [], "until": time.monotonic() + _ENROLL_TTL_S}
    return needed


def cancel_enrollment() -> None:
    global _enrolling
    with _lock:
        _enrolling = None


def enrolling() -> bool:
    global _enrolling
    with _lock:
        if _enrolling and time.monotonic() > _enrolling["until"]:
            log.info("Voice ID: enrolment expired")
            _enrolling = None
        return _enrolling is not None


def take_sample(audio: np.ndarray) -> str:
    """Use one room command as an enrolment sample; returns what to say."""
    global _enrolling
    vector = embed(audio)
    with _lock:
        state = _enrolling
        if state is None:
            return ""
        if vector is None:
            if not available():
                _enrolling = None
                return "I can't learn voices yet, sir: the voice model isn't installed."
            return "That one was too short, sir. A full sentence, please."
        state["samples"].append(vector)
        left = state["needed"] - len(state["samples"])
        if left > 0:
            return f"Got it. {left} more."
        samples, _enrolling = state["samples"], None
    _save_print(samples)
    log.info("Voice ID: enrolled from %d samples", len(samples))
    return "Voiceprint saved, sir. I'll know it's you."


def status_text() -> str:
    parts = [f"Voice ID is {'on' if enabled() else 'off (VOICE_ID=0)'}."]
    data = _load_print()
    parts.append(f"Nick's voiceprint: {data['samples']} samples, enrolled {data['enrolled_at']}."
                 if data else "No voiceprint enrolled.")
    if not available():
        parts.append(f"The speaker model isn't loaded (sherpa-onnx and {model_path().name} needed).")
    if enrolling():
        parts.append("Enrolment is in progress on the room mic.")
    parts.append(f"Threshold {_threshold():.2f}.")
    return " ".join(parts)
