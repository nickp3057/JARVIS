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
RMS_THRESHOLD = float(os.getenv("MIC_RMS_THRESHOLD", "350"))
SILENCE_END_MS = int(os.getenv("MIC_SILENCE_END_MS", "700"))
SILENCE_END_FRAMES = max(1, SILENCE_END_MS // FRAME_MS)

# Rolling buffer length. Long enough to comfortably capture a sentence
# spoken before the wake word ("Set a timer for ten minutes, Jarvis.").
ROLLING_BUFFER_SECONDS = float(os.getenv("ROLLING_BUFFER_SECONDS", "5.0"))
ROLLING_BUFFER_FRAMES = int(ROLLING_BUFFER_SECONDS * 1000 / FRAME_MS)


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

        def _callback(indata, frames, time_info, status):  # type: ignore[no-untyped-def]
            if status:
                log.debug("audio status: %s", status)
            frame = indata.copy().reshape(-1).astype(np.int16)
            self._queue.put(frame)
            with self._buffer_lock:
                self._buffer.append(frame)

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_samples,
            callback=_callback,
        )
        self._stream.start()
        return self

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
