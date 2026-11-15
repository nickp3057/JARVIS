"""Voice satellites: iPads that lend the room voice their mic and speaker.

The nightstand iPad mini and the door iPad Air (both on by default) open
``WS /api/voice/satellite?name=bed`` while the companion app is on screen and
streams its mic as raw 16 kHz mono int16 PCM. It doesn't think at all: Core's
room voice (``speech/room_voice.py``) runs the wake word, voice ID, the
transcript, barge-in and hush on that audio exactly as it does on the desk
mic, and when the satellite heard "Hey JARVIS" best, the reply goes back to
the satellite's speaker instead of the desk's.

Wire format (one socket per iPad):

- iPad -> Core, binary: mic PCM, 16 kHz mono int16 little-endian, any size.
- iPad -> Core, text: ``{"type": "bye", "reason": "..."}`` before it hangs
  up on an error (logged, so a drop says why); anything else is ignored.
- iPad -> Core, text: ``{"type": "volume", "level": 0-10}`` when Nick
  presses its volume buttons: the room's level (desk and every iPad).
- Core -> iPad, text: ``{"type": "hello", "rate": 16000, "level": 0-10}`` on
  connect; ``{"type": "volume", "level": 0-10}`` whenever the room's level
  changes (the iPad sets its own volume to level/10);
  ``{"type": "play", "rate": 22050, "gain": 0.4}`` before a clip,
  ``{"type": "end"}`` after it, ``{"type": "stop"}`` to cut it off now
  (barge-in, hush), ``{"type": "off"}`` when the room voice isn't running,
  ``{"type": "ping"}`` every 20 s (ignore it; it keeps the socket alive).
- Core -> iPad, binary: speaker PCM at the last ``play`` rate, mono int16.

Speakers: everything the room voice says or plays goes to the desk speaker
and every satellite at once (``play_everywhere``), from one synthesis, so
the room hears one voice. ``SATELLITE_SYNC_MS`` lines them up: positive
holds the desk back that long, negative holds the iPads back (default 60:
at 0 the iPads trailed the desk just enough to hear, 2026-11-15; the desk's
aplay runs a 100 ms buffer, ``DESK_BUFFER_MS``). Nick tunes it by ear from
the iPad (Room settings › Mic and speaker › Desk earlier / later,
``POST /api/voice/satellites/sync``): saved in ``memory/satellite_sync.json``,
which wins over ``.env`` and applies from the next clip, no restart. The iPad plays at
the same loudness as the desk (``output_gain``: the desk's 0-10 level on
ALSA's mapped curve, which is 60 dB wide, so amplitude = (level/10)^3).

A ``Satellite`` doubles as a mic stream for the room voice (``read``,
``drain``, ``get_recent_audio``, like ``speech.audio.MicStream``). Frames are
re-cut to the room voice's 80 ms frames; the queue is bounded so a satellite
nobody is reading can't grow without limit.
"""
from __future__ import annotations

import collections
import json
import os
import queue
import subprocess
import sys
import re
import threading
import time
import wave
from collections.abc import Iterable
from pathlib import Path

import numpy as np

from jarvis_logger import get_logger
from speech.audio import FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE, rms

log = get_logger(__name__)

_NAME = re.compile(r"^[a-z0-9_-]{1,20}$")
_QUEUE_FRAMES = 250          # 20 s of audio waiting to be read, then the oldest goes
_BUFFER_FRAMES = int(5000 / FRAME_MS)  # the rolling 5 s for the wake word's preroll
_PLAY_TAIL_S = 0.25          # after the last sample: the iPad's own output latency


def traffic_node(name: str) -> str:
    """The link map's node for a satellite: ``bed`` is the nightstand mini."""
    return "nightstand" if name == "bed" else "ipad"


def _signal(source: str, target: str, label: str) -> None:
    try:
        from brain import traffic
        traffic.record(source, target, label)
    except Exception:  # noqa: BLE001 - the map must never break the voice
        pass


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
        _signal("core", traffic_node(self.name), "voice")
        self.send_json({"type": "play", "rate": int(rate), "gain": round(output_gain() if gain is None else gain, 3)})
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

    def play_sound(self, name: str, wait: bool = False, gain: float | None = None) -> None:
        """One of the room voice's sounds (``speech/room_sounds.py``) on this iPad alone."""
        try:
            pcm, rate = _sound_pcm(name)
        except Exception:  # noqa: BLE001
            log.debug("Satellite sound %s unavailable", name, exc_info=True)
            return
        if wait:
            self.play_pcm([pcm], rate, gain=gain)
        else:
            threading.Thread(target=self.play_pcm, args=([pcm], rate), kwargs={"gain": gain}, daemon=True,
                             name=f"satellite-{self.name}-sound").start()


_sound_cache: dict[str, tuple[bytes, int]] = {}


def _sound_pcm(name: str) -> tuple[bytes, int]:
    if name not in _sound_cache:
        from speech import room_sounds
        with wave.open(str(room_sounds._render(name)), "rb") as clip:  # noqa: SLF001
            _sound_cache[name] = (clip.readframes(clip.getnframes()), clip.getframerate())
    return _sound_cache[name]


def output_gain(level: int | None = None) -> float:
    """How loud the iPad plays (amplitude 0-1, on top of its own volume, best
    left at full): the same loudness as the desk speaker at ``level`` (the
    room's current 0-10 level, night or day, when not given). The desk uses
    ALSA's mapped scale (``ROOM_VOLUME_MAPPED``), 60 dB wide: level 8 is
    about -6 dB, so amplitude = (level/10)^3; raw steps are close to linear."""
    if level is None:
        try:
            from speech import room_volume
            level = room_volume.level()
        except Exception:  # noqa: BLE001
            log.debug("Satellite gain: room volume unreadable", exc_info=True)
            level = None
    if level is None:
        return 1.0
    fraction = max(0, min(10, int(level))) / 10
    mapped = os.getenv("ROOM_VOLUME_MAPPED", "1").strip().lower() not in ("0", "off", "false", "no")
    return round(fraction ** 3 if mapped else fraction, 3)


def broadcast_volume(level: int) -> None:
    """Every iPad sets its own volume to the room's level (``{"type":
    "volume", "level": 0-10}``), so the room is one volume knob while the
    app is open; an iPad's volume buttons send the same message back."""
    for sat in connected():
        sat.send_json({"type": "volume", "level": int(level)})


_linked = False


def link_room_volume() -> None:
    """Once: room volume changes (voice, the slider, quiet hours, an iPad's
    buttons) go out to every iPad."""
    global _linked
    if _linked:
        return
    from speech import room_volume
    room_volume.on_change(broadcast_volume)
    _linked = True


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
    _signal(traffic_node(name), "core", "voice satellite")
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


def ring_all(name: str = "alarm", level: int | None = None) -> None:
    """A sound on every satellite at once, at the desk's ``level`` (the
    wake-up backup ring rings at ``ROOM_ALARM_VOLUME``: the nightstand iPad
    is right by the bed)."""
    gain = output_gain(level)
    for sat in connected():
        sat.play_sound(name, gain=gain)


# --- the whole room at once ----------------------------------------------------

_SYNC_FILE = Path(__file__).resolve().parents[1] / "memory" / "satellite_sync.json"
_SYNC_LIMIT_MS = 1000


def sync_ms() -> int:
    """Desk-vs-iPad offset in ms: > 0 the desk waits, < 0 the iPads wait.
    The iPad's saved nudge if there is one, else ``SATELLITE_SYNC_MS`` (60)."""
    try:
        saved = json.loads(_SYNC_FILE.read_text()).get("sync_ms")
        if isinstance(saved, (int, float)):
            return int(max(-_SYNC_LIMIT_MS, min(_SYNC_LIMIT_MS, saved)))
    except (OSError, ValueError, AttributeError):
        pass
    try:
        return int(max(-_SYNC_LIMIT_MS, min(_SYNC_LIMIT_MS, float(os.getenv("SATELLITE_SYNC_MS", "60")))))
    except ValueError:
        return 0


def set_sync_ms(value: float | None) -> int:
    """Save the offset (None forgets it: back to ``.env``). Returns the live value."""
    if value is None:
        _SYNC_FILE.unlink(missing_ok=True)
    else:
        clamped = int(max(-_SYNC_LIMIT_MS, min(_SYNC_LIMIT_MS, round(value))))
        _SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SYNC_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"sync_ms": clamped}))
        tmp.replace(_SYNC_FILE)
    log.info("Speaker sync: desk %+d ms", sync_ms())
    return sync_ms()


def _sync_seconds() -> float:
    """Desk-vs-iPad offset: > 0 the desk waits, < 0 the iPads wait (seconds)."""
    return sync_ms() / 1000


def _desk_buffer_us() -> int:
    """aplay's buffer: its default (about half a second) made the desk lag
    the iPads, since aplay waits for a full buffer before it starts."""
    try:
        return int(max(20, min(1000, float(os.getenv("DESK_BUFFER_MS", "100")))) * 1000)
    except ValueError:
        return 100_000


def _open_desk(rate: int):  # noqa: ANN202
    """The desk speaker as a raw PCM pipe (ALSA ``aplay``), registered as
    TTS's current player so ``tts.stop_playback`` (barge-in, hush, "stop")
    cuts it off too. None off Linux (the Mac dev box) or without aplay."""
    if not sys.platform.startswith("linux"):
        return None
    from speech import tts
    try:
        proc = subprocess.Popen(["aplay", "-q", *tts.alsa_device_args(), "-t", "raw", "-f", "S16_LE",
                                 "-r", str(int(rate)), "-c", "1", f"--buffer-time={_desk_buffer_us()}"],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        log.warning("Desk speaker: aplay unavailable")
        return None
    with tts._player_lock:  # noqa: SLF001
        tts._current_player = proc  # noqa: SLF001
    return proc


def _close_desk(proc, cut: bool) -> None:  # noqa: ANN001
    from speech import tts
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except OSError:
        pass
    if cut:
        if proc.poll() is None:
            proc.terminate()
    else:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
    with tts._player_lock:  # noqa: SLF001
        if tts._current_player is proc:  # noqa: SLF001
            tts._current_player = None  # noqa: SLF001


def play_everywhere(chunks: Iterable[bytes], rate: int, interrupt: threading.Event | None = None,
                    gain: float | None = None) -> bool:
    """Play one clip (mono int16 PCM at ``rate``) on the desk speaker and
    every satellite at the same time, at the same loudness. Blocks about as
    long as the clip lasts. An interrupt, ``tts.stop_playback`` or
    ``stop_all`` cuts every speaker off. True if any audio went out."""
    sats = connected()
    gain = output_gain() if gain is None else gain
    for sat in sats:
        sat._halt.clear()  # noqa: SLF001
        _signal("core", traffic_node(sat.name), "voice")
        sat.send_json({"type": "play", "rate": int(rate), "gain": round(gain, 3)})
    desk = _open_desk(rate)
    offset = _sync_seconds() if sats else 0.0
    if desk is not None and offset > 0:
        try:  # the desk waits for the iPads
            desk.stdin.write(b"\0\0" * int(rate * offset))
        except OSError:
            desk = None
    if offset < 0:  # the iPads wait for the desk
        for sat in sats:
            sat.send_bytes(b"\0\0" * int(rate * -offset))
    started: float | None = None
    samples = 0
    cut = False

    def halted() -> bool:
        return (interrupt is not None and interrupt.is_set()) or any(s._halt.is_set() for s in sats)  # noqa: SLF001

    try:
        for chunk in chunks:
            if halted():
                cut = True
                break
            if not chunk:
                continue
            if started is None:
                started = time.monotonic()
            for sat in sats:
                sat.send_bytes(chunk)
            if desk is not None:
                try:
                    desk.stdin.write(chunk)  # paces the loop at real time
                except (OSError, ValueError):  # stop_playback killed it: that's a stop
                    cut = True
                    break
            samples += len(chunk) // 2
    except Exception:  # noqa: BLE001 - the voice failed mid-clip: play what we have
        log.exception("Speech failed mid-clip")
    for sat in sats:
        sat.send_json({"type": "end"})
    if started is None:
        if desk is not None:
            _close_desk(desk, cut=True)
        return False
    if desk is not None:
        _close_desk(desk, cut)  # without a cut: waits for the desk to finish
    until = started + samples / max(1, rate) + _PLAY_TAIL_S
    while not cut and sats and time.monotonic() < until:
        if halted():
            cut = True
            break
        time.sleep(0.03)
    if cut:
        for sat in sats:
            sat.send_json({"type": "stop"})
    return True


def say_everywhere(text: str, interrupt: threading.Event | None = None) -> bool:
    """Speak ``text`` on every speaker at once in JARVIS's voice (synthesized
    once: the phone's voice pipeline, ElevenLabs PCM with Piper behind it)."""
    from speech import remote_voice
    text = (text or "").strip()
    if not text:
        return False
    stopped = threading.Event()

    def chunks():
        try:
            yield from remote_voice.pcm_stream(text, stopped)
        except RuntimeError:
            log.warning("No voice for %r", text[:60])

    try:
        return play_everywhere(chunks(), remote_voice.SAMPLE_RATE, interrupt)
    finally:
        stopped.set()


def say_stream_everywhere(fragments: Iterable[str], interrupt: threading.Event | None = None) -> bool:
    """A reply still being written, spoken a sentence at a time everywhere."""
    from speech.tts import _split_ready_sentences
    buffer, spoke = "", False
    for fragment in fragments:
        if interrupt is not None and interrupt.is_set():
            return spoke
        buffer += fragment or ""
        ready, buffer = _split_ready_sentences(buffer)
        for sentence in ready:
            if interrupt is not None and interrupt.is_set():
                return spoke
            spoke = say_everywhere(sentence, interrupt) or spoke
    if buffer.strip() and not (interrupt is not None and interrupt.is_set()):
        spoke = say_everywhere(buffer, interrupt) or spoke
    return spoke


def sound_everywhere(name: str, wait: bool = False) -> None:
    """A room-voice sound on every speaker at once."""
    try:
        pcm, rate = _sound_pcm(name)
    except Exception:  # noqa: BLE001
        log.debug("Sound %s unavailable", name, exc_info=True)
        return
    if wait:
        play_everywhere([pcm], rate)
    else:
        threading.Thread(target=play_everywhere, args=([pcm], rate), daemon=True, name=f"sound-{name}").start()


def status() -> list[dict[str, object]]:
    return [{"name": s.name, "connected_at": s.connected_at} for s in connected()]


def reset_for_tests() -> None:
    with _lock:
        _satellites.clear()
