"""Voice satellites: iPads that lend the room voice their mic and speaker.

The nightstand iPad mini (and the door iPad, if Nick switches it on) opens
``WS /api/voice/satellite?name=bed`` while the companion app is on screen and
streams its mic as raw 16 kHz mono int16 PCM. It doesn't think at all: Core's
room voice (``speech/room_voice.py``) runs the wake word, voice ID, the
transcript, barge-in and hush on that audio exactly as it does on the desk
mic, and when the satellite heard "Hey JARVIS" best, the reply goes back to
the satellite's speaker instead of the desk's.

Wire format (one socket per iPad):

- iPad -> Core, binary: mic PCM, 16 kHz mono int16 little-endian, any size.
- iPad -> Core, text: ``{"type": "ping"}`` (ignored; keeps proxies happy).
- Core -> iPad, text: ``{"type": "hello", "rate": 16000}`` on connect;
  ``{"type": "play", "rate": 22050, "gain": 0.4}`` before a clip,
  ``{"type": "end"}`` after it, ``{"type": "stop"}`` to cut it off now
  (barge-in, hush), ``{"type": "off"}`` when the room voice isn't running,
  ``{"type": "ping"}`` every 20 s (ignore it; it keeps the socket alive).
- Core -> iPad, binary: speaker PCM at the last ``play`` rate, mono int16.

A ``Satellite`` doubles as a mic stream for the room voice (``read``,
``drain``, ``get_recent_audio``, like ``speech.audio.MicStream``). Frames are
re-cut to the room voice's 80 ms frames; the queue is bounded so a satellite
nobody is reading can't grow without limit.
"""
from __future__ import annotations

import collections
import json
import queue
import re
import threading
import time
import wave
from collections.abc import Iterable

import numpy as np

from jarvis_logger import get_logger
from speech.audio import FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE, rms

log = get_logger(__name__)

_NAME = re.compile(r"^[a-z0-9_-]{1,20}$")
_QUEUE_FRAMES = 250          # 20 s of audio waiting to be read, then the oldest goes
_BUFFER_FRAMES = int(5000 / FRAME_MS)  # the rolling 5 s for the wake word's preroll
_PLAY_TAIL_S = 0.25          # after the last sample: the iPad's own output latency


def clean_name(raw: str | None) -> str | None:
    """A satellite's name as the iPad sends it (``bed``, ``door``); None if bad."""
    name = (raw or "").strip().lower()
    return name if _NAME.match(name) else None


class Satellite:
    """One connected iPad: its mic as a stream, its speaker as an output.

    ``send`` puts a message on the socket's outbox from any thread (the
    socket's writer task owns the actual send, so messages keep their order).
    """

    def __init__(self, name: str, send) -> None:  # noqa: ANN001 - send(str | bytes) -> None
        self.name = name
        self._send = send
        self.connected = True
        self.connected_at = time.time()
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=_QUEUE_FRAMES)
        self._buffer: collections.deque[np.ndarray] = collections.deque(maxlen=_BUFFER_FRAMES)
        self._buffer_lock = threading.Lock()
        self._pending = np.zeros(0, dtype=np.int16)
        self._halt = threading.Event()  # stop(): a clip being played returns at once
        # The room voice's per-mic state: its own wake detector, streak and
        # background level, so the iPad's mic is judged on its own terms.
        self.detector = None
        self.wake_hits = 0
        self.noise = 0.0

    # --- the mic --------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        """Raw PCM from the iPad, cut into 80 ms frames."""
        if len(data) % 2:
            data = data[:-1]
        if not data:
            return
        samples = np.frombuffer(data, dtype="<i2").astype(np.int16)
        pending = np.concatenate([self._pending, samples]) if self._pending.size else samples
        whole = pending.size // FRAME_SAMPLES * FRAME_SAMPLES
        for start in range(0, whole, FRAME_SAMPLES):
            frame = pending[start:start + FRAME_SAMPLES].copy()
            try:
                self._frames.put_nowait(frame)
            except queue.Full:  # nobody's reading: keep the newest 20 s
                try:
                    self._frames.get_nowait()
                except queue.Empty:
                    pass
                self._frames.put_nowait(frame)
            with self._buffer_lock:
                self._buffer.append(frame)
        self._pending = pending[whole:].copy()

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            if timeout is not None and timeout <= 0:
                return self._frames.get_nowait()
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        try:
            while True:
                self._frames.get_nowait()
        except queue.Empty:
            pass

    def get_recent_audio(self, seconds: float) -> np.ndarray:
        want = int(max(0.0, seconds) * 1000 / FRAME_MS)
        with self._buffer_lock:
            frames = list(self._buffer)[-want:] if want > 0 else []
        return np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)

    def learn_noise(self, frame: np.ndarray) -> None:
        level = rms(frame)
        self.noise = level if self.noise == 0.0 else self.noise * 0.98 + min(level, self.noise * 3) * 0.02

    # --- the speaker ----------------------------------------------------------

    def send_json(self, message: dict) -> None:
        if self.connected:
            self._send(json.dumps(message))

    def send_bytes(self, data: bytes) -> None:
        if self.connected and data:
            self._send(bytes(data))

    def stop(self) -> None:
        """Cut off whatever the iPad is saying (barge-in, hush)."""
        self._halt.set()
        self.send_json({"type": "stop"})

    def play_pcm(self, chunks: Iterable[bytes], rate: int,
                 interrupt: threading.Event | None = None, gain: float | None = None) -> bool:
        """Send a clip and wait about as long as it takes to play, so the room
        voice's speaking/listening rhythm matches what the room hears. Returns
        True if any audio went out. An interrupt or ``stop()`` ends it early
        and tells the iPad to drop what it has buffered."""
        self._halt.clear()
        self.send_json({"type": "play", "rate": int(rate), "gain": round(output_gain() if gain is None else gain, 2)})
        started: float | None = None
        samples = 0
        cut = False
        try:
            for chunk in chunks:
                if not self.connected or self._halt.is_set() or (interrupt is not None and interrupt.is_set()):
                    cut = True
                    break
                if not chunk:
                    continue
                if started is None:
                    started = time.monotonic()
                self.send_bytes(chunk)
                samples += len(chunk) // 2
        except Exception:  # noqa: BLE001 - the voice failed mid-clip: say what we have
            log.exception("Satellite %s: speech failed mid-clip", self.name)
        self.send_json({"type": "end"})
        if started is None:
            return False
        until = started + samples / max(1, rate) + _PLAY_TAIL_S
        while not cut and time.monotonic() < until:
            if not self.connected or self._halt.is_set() or (interrupt is not None and interrupt.is_set()):
                cut = True
                break
            time.sleep(0.03)
        if cut and self.connected:
            self.send_json({"type": "stop"})
        return True

    def say(self, text: str, interrupt: threading.Event | None = None) -> bool:
        """Speak ``text`` on the iPad in JARVIS's voice (the phone's voice
        pipeline: ElevenLabs PCM, Piper as the fallback)."""
        from speech import remote_voice
        text = (text or "").strip()
        if not text:
            return False
        stopped = threading.Event()

        def chunks():
            try:
                for chunk in remote_voice.pcm_stream(text, stopped):
                    yield chunk
            except RuntimeError:
                log.warning("Satellite %s: no voice for %r", self.name, text[:60])

        try:
            return self.play_pcm(chunks(), remote_voice.SAMPLE_RATE, interrupt)
        finally:
            stopped.set()

    def say_stream(self, fragments: Iterable[str], interrupt: threading.Event | None = None) -> bool:
        """A reply still being written, spoken a sentence at a time."""
        from speech.tts import _split_ready_sentences
        buffer, spoke = "", False
        for fragment in fragments:
            if interrupt is not None and interrupt.is_set():
                return spoke
            buffer += fragment or ""
            ready, buffer = _split_ready_sentences(buffer)
            for sentence in ready:
                if (interrupt is not None and interrupt.is_set()) or self._halt.is_set():
                    return spoke
                spoke = self.say(sentence, interrupt) or spoke
        if buffer.strip() and not (interrupt is not None and interrupt.is_set()):
            spoke = self.say(buffer, interrupt) or spoke
        return spoke

    def play_sound(self, name: str, wait: bool = False) -> None:
        """One of the room voice's sounds (``speech/room_sounds.py``)."""
        try:
            pcm, rate = _sound_pcm(name)
        except Exception:  # noqa: BLE001
            log.debug("Satellite sound %s unavailable", name, exc_info=True)
            return
        if wait:
            self.play_pcm([pcm], rate)
        else:
            threading.Thread(target=self.play_pcm, args=([pcm], rate), daemon=True,
                             name=f"satellite-{self.name}-sound").start()


_sound_cache: dict[str, tuple[bytes, int]] = {}


def _sound_pcm(name: str) -> tuple[bytes, int]:
    if name not in _sound_cache:
        from speech import room_sounds
        with wave.open(str(room_sounds._render(name)), "rb") as clip:  # noqa: SLF001
            _sound_cache[name] = (clip.readframes(clip.getnframes()), clip.getframerate())
    return _sound_cache[name]


def output_gain() -> float:
    """How loud the iPad plays, on top of its own volume buttons: full by
    day, the room's night level in quiet hours (``ROOM_NIGHT_VOLUME``)."""
    try:
        from speech import room_volume
        if room_volume.quiet_now():
            return max(0.15, min(1.0, room_volume.night_level() / 10))
    except Exception:  # noqa: BLE001
        log.debug("Satellite gain: room volume unreadable", exc_info=True)
    return 1.0


# --- the registry -------------------------------------------------------------

_lock = threading.Lock()
_satellites: dict[str, Satellite] = {}


def register(name: str, send) -> Satellite:  # noqa: ANN001
    """A satellite connected. One per name: a reconnect replaces the old one."""
    sat = Satellite(name, send)
    with _lock:
        old = _satellites.get(name)
        _satellites[name] = sat
    if old is not None:
        old.connected = False
        old._halt.set()  # noqa: SLF001
    log.info("Voice satellite '%s' connected", name)
    return sat


def unregister(sat: Satellite) -> None:
    sat.connected = False
    sat._halt.set()  # noqa: SLF001
    with _lock:
        if _satellites.get(sat.name) is sat:
            del _satellites[sat.name]
            log.info("Voice satellite '%s' disconnected", sat.name)


def connected() -> list[Satellite]:
    with _lock:
        return list(_satellites.values())


def get(name: str) -> Satellite | None:
    with _lock:
        return _satellites.get(name)


def stop_all() -> None:
    for sat in connected():
        sat.stop()


def ring_all(name: str = "alarm") -> None:
    """A sound on every satellite at once (the wake-up backup ring: the
    nightstand iPad is right by the bed)."""
    for sat in connected():
        sat.play_sound(name)


def status() -> list[dict[str, object]]:
    return [{"name": s.name, "connected_at": s.connected_at} for s in connected()]


def reset_for_tests() -> None:
    with _lock:
        _satellites.clear()
