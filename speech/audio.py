"""Microphone capture and silence detection for voice mode.

Audio runs at 16 kHz mono int16 throughout the voice stack - that's what
both openWakeWord and faster-whisper expect. We capture in 80ms frames
(1280 samples) which matches openWakeWord's expected input chunk.

``MicStream`` is a context manager that opens the default input device and
makes incoming frames available two ways:

* ``read()`` - pulls frames off a queue, one at a time (consumed by the
  wake-word detector and ``record_until_silence``).
* ``get_recent_audio(seconds)`` - returns the last N seconds of audio from
  a rolling buffer that captures *every* frame the mic produces, so we can
  reach back in time when the wake word is spoken at the END of an
  utterance ("What time is it, Jarvis?").
"""

from __future__ import annotations

import collections
import os
import queue
import threading
from typing import Any

import numpy as np

from jarvis_logger import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 80
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)  # 1280

# Tunables (env-overridable so you can adjust on the fly without code changes).
RMS_THRESHOLD = float(os.getenv("MIC_RMS_THRESHOLD", "350"))  # at import; rms_threshold() is live


def rms_threshold() -> float:
    """MIC_RMS_THRESHOLD read now, so JARVIS (update_setting) can retune
    "how loud is speech" without a restart."""
    try:
        return float(os.getenv("MIC_RMS_THRESHOLD", "350"))
    except ValueError:
        return RMS_THRESHOLD
SILENCE_END_MS = int(os.getenv("MIC_SILENCE_END_MS", "700"))
SILENCE_END_FRAMES = max(1, SILENCE_END_MS // FRAME_MS)

# Rolling buffer length. Long enough to comfortably capture a sentence
# spoken before the wake word ("Set a timer for ten minutes, Jarvis.").
ROLLING_BUFFER_SECONDS = float(os.getenv("ROLLING_BUFFER_SECONDS", "5.0"))
ROLLING_BUFFER_FRAMES = int(ROLLING_BUFFER_SECONDS * 1000 / FRAME_MS)


def to_mono_16k(block: np.ndarray, rate: int, target: int = SAMPLE_RATE) -> np.ndarray:
    """One captured block -> mono int16 at ``target`` Hz (first channel;
    whole-number ratios like 48 kHz are averaged, others interpolated)."""
    samples = block.reshape(len(block), -1)[:, 0].astype(np.float32) if block.ndim > 1 else block.astype(np.float32)
    if rate != target and samples.size:
        if rate % target == 0:
            step = rate // target
            samples = samples[: samples.size // step * step].reshape(-1, step).mean(axis=1)
        else:
            count = int(round(samples.size * target / rate))
            samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples)
    return np.clip(np.round(samples), -32768, 32767).astype(np.int16)


class MicStream:
    """Context-managed input stream with both a frame queue and a rolling buffer."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        frame_samples: int = FRAME_SAMPLES,
        rolling_buffer_frames: int = ROLLING_BUFFER_FRAMES,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Any | None = None
        self._buffer: collections.deque[np.ndarray] = collections.deque(
            maxlen=rolling_buffer_frames
        )
        self._buffer_lock = threading.Lock()

    def __enter__(self) -> "MicStream":
        import sounddevice as sd  # local import keeps module loadable without the dep

        device = os.getenv("MIC_DEVICE", "").strip() or None  # sounddevice name or index
        device = int(device) if device and device.isdigit() else device
        # Raw ALSA devices (hw:0,0, like the Optiplex's CMTECK USB mic) may
        # refuse 16 kHz mono; then record at the device's own rate and
        # channel count and convert each block here.
        attempts = [(self.sample_rate, 1)]
        try:
            info = sd.query_devices(device, "input")
            native = int(info["default_samplerate"])
            attempts += [(native, 1), (native, max(1, int(info["max_input_channels"])))]
        except Exception:  # noqa: BLE001
            info = None
        error: Exception | None = None
        for rate, channels in dict.fromkeys(attempts):
            try:
                self._stream = sd.InputStream(
                    device=device,
                    samplerate=rate,
                    channels=channels,
                    dtype="int16",
                    blocksize=int(round(self.frame_samples * rate / self.sample_rate)),
                    callback=self._callback_for(rate),
                )
                break
            except Exception as exc:  # noqa: BLE001
                error = exc
        else:
            raise error  # type: ignore[misc]
        self._stream.start()
        name = info["name"] if info else str(device or "default input")
        log.info("Mic open: %s at %d Hz, %d channel(s)", name, rate, channels)
        return self

    def _callback_for(self, rate: int):  # noqa: ANN202
        def _callback(indata, frames, time_info, status):  # type: ignore[no-untyped-def]
            if status:
                log.debug("audio status: %s", status)
            frame = to_mono_16k(np.asarray(indata), rate, self.sample_rate)
            self._queue.put(frame)
            with self._buffer_lock:
                self._buffer.append(frame)
        return _callback

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                log.exception("Error closing mic stream")
            self._stream = None

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        """Return the next audio frame from the queue, or None on timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        """Throw away any frames buffered while we weren't reading."""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def get_recent_audio(self, seconds: float) -> np.ndarray:
        """Return the last ``seconds`` of audio from the rolling buffer.

        Reads atomically; safe to call from the main thread while the
        callback is appending. Returns an empty array if there's nothing.
        """
        seconds = max(0.0, seconds)
        want_frames = int(seconds * 1000 / FRAME_MS)
        with self._buffer_lock:
            if want_frames <= 0 or not self._buffer:
                return np.zeros(0, dtype=np.int16)
            slice_ = list(self._buffer)[-want_frames:]
        return np.concatenate(slice_) if slice_ else np.zeros(0, dtype=np.int16)


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of an int16 frame."""
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def record_until_silence(
    stream: MicStream,
    max_seconds: float = 15.0,
    start_timeout: float = 10.0,
) -> np.ndarray | None:
    """Record one utterance from ``stream``.

    Waits up to ``start_timeout`` seconds for the user to start speaking, then
    keeps recording until ~``SILENCE_END_MS`` of silence after speech, or
    ``max_seconds`` of total speech (safety cap). Returns int16 audio or None
    if the user never spoke.
    """
    started = False
    silence_count = 0
    frames: list[np.ndarray] = []

    start_frames_remaining = int(start_timeout * 1000 / FRAME_MS)
    max_frames = int(max_seconds * 1000 / FRAME_MS)

    for _ in range(max_frames):
        frame = stream.read(timeout=0.5)
        if frame is None:
            continue

        is_voice = rms(frame) > RMS_THRESHOLD

        if not started:
            if is_voice:
                started = True
                frames.append(frame)
            else:
                start_frames_remaining -= 1
                if start_frames_remaining <= 0:
                    return None
        else:
            frames.append(frame)
            if is_voice:
                silence_count = 0
            else:
                silence_count += 1
                if silence_count >= SILENCE_END_FRAMES:
                    break

    if not frames:
        return None
    return np.concatenate(frames)
