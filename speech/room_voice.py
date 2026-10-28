"""The room voice: "Hey JARVIS" on the Optiplex's mic and speakers.

Runs inside JARVIS Core (``python -m app.main --server``) when ``ROOM_VOICE=1``,
on the same runtime the apps use, so the iPad can show it live
(``brain/voice_presence.py`` -> ``GET /api/voice/stream``).

Built for smart-speaker speed:

1. openWakeWord ``hey_jarvis`` on every 80 ms frame; a soft "listen" sound.
2. Record until ~0.55 s of silence (``VOICE_SILENCE_END_MS``). While you
   talk, a tiny Whisper model (``VOICE_LIVE_MODEL``, tiny.en) captions it
   live for the iPad; the main model (``WHISPER_MODEL``; base.en suits the
   Optiplex) makes the final transcript.
3. Plain home commands (scenes, on/off, brightness) run directly
   (``speech/quick_commands.py``) and answer with a jingle, no words.
4. Everything else is a normal turn with a "spoken reply" hint: one short
   sentence. A reply of just "Done." after a home action becomes the jingle.
5. After a spoken answer, a short follow-up window (``VOICE_FOLLOWUP_S``)
   takes the next question without the wake word.

The wake word works at the end too ("game time, Jarvis"): a little audio
from before the wake word is kept and used when nothing follows it.
"""
from __future__ import annotations

import os
import threading
import time

import numpy as np

from brain import voice_presence
from jarvis_logger import get_logger
from speech import room_sounds
from speech.audio import FRAME_MS, RMS_THRESHOLD, SAMPLE_RATE, MicStream, rms
from speech.wake_words import strip_wake_word

log = get_logger(__name__)

_JINGLE_REPLIES = {"done", "yes sir", "yes, sir", "ok", "okay"}


def enabled() -> bool:
    return os.getenv("ROOM_VOICE", "").strip().lower() in ("1", "true", "yes", "on")


def _seconds(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class RoomVoice:
    """The listen -> transcribe -> act loop. Detector and transcribers can be
    passed in (tests); by default they are openWakeWord and faster-whisper."""

    def __init__(self, runtime, *, detector=None, final=None, live=None) -> None:  # noqa: ANN001
        from speech.stt import Transcriber
        from speech.wake import WakeDetector
        self.runtime = runtime  # JarvisRuntime (not imported: avoids a cycle)
        self.detector = detector or WakeDetector()
        self.final = final or Transcriber()
        if live is None:
            live_model = os.getenv("VOICE_LIVE_MODEL", "tiny.en").strip()
            live = Transcriber(model_size=live_model) if live_model and live_model != "off" else None
        self.live = live
        self.stop = threading.Event()

    # --- lifecycle -----------------------------------------------------------

    def preload(self) -> None:
        """Load models up front so the first "Hey JARVIS" isn't slow."""
        self.detector._ensure_loaded()  # noqa: SLF001
        silence = np.zeros(SAMPLE_RATE // 2, dtype=np.int16)
        self.final.transcribe(silence)
        if self.live is not None:
            self.live.transcribe(silence)
        for name in ("listen", "done", "error"):
            room_sounds._render(name)  # noqa: SLF001

    def run_forever(self) -> None:
        """Keeps going through a missing or unplugged mic (retries every 30 s)."""
        while not self.stop.is_set():
            try:
                self.preload()
                with MicStream() as stream:
                    log.info("Room voice listening for 'hey jarvis'")
                    self._loop(stream)
            except Exception:  # noqa: BLE001
                log.exception("Room voice stopped; retrying in 30 s")
                voice_presence.update("idle")
                self.stop.wait(30)

    # --- the loop ------------------------------------------------------------

    def _loop(self, stream: MicStream) -> None:
        self.stream = stream  # routines (goodnight) listen for an answer on it
        need_wake = True
        while not self.stop.is_set():
            stream.drain()
            if need_wake:
                if not self._wait_for_wake(stream):
                    return
                preroll = stream.get_recent_audio(_seconds("VOICE_PREROLL_S", 2.0))
                voice_presence.update("listening")
                room_sounds.play("listen")
                audio = self._capture(stream, preroll, start_timeout=_seconds("VOICE_START_TIMEOUT_S", 4.0))
            else:  # follow-up: no wake word, a shorter wait
                voice_presence.update("listening")
                audio = self._capture(stream, None, start_timeout=_seconds("VOICE_FOLLOWUP_S", 5.0))
            if audio is None:
                voice_presence.update("idle")
                need_wake = True
                continue
            need_wake = not self._handle(audio)

    def _wait_for_wake(self, stream: MicStream) -> bool:
        from speech.wake import WAKE_THRESHOLD
        self.detector.reset()
        while not self.stop.is_set():
            frame = stream.read(timeout=1.0)
            if frame is None:
                continue
            try:
                scores = self.detector._model.predict(frame)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                log.exception("openWakeWord predict() failed; skipping frame")
                continue
            if any(score >= WAKE_THRESHOLD for score in scores.values()):
                return True
        return False

    def _capture(self, stream: MicStream, preroll: np.ndarray | None, start_timeout: float) -> np.ndarray | None:
        """One utterance, captioned live; None if nobody spoke."""
        silence_end = int(_seconds("VOICE_SILENCE_END_MS", 550) / FRAME_MS)
        max_frames = int(_seconds("VOICE_MAX_UTTERANCE_S", 15) * 1000 / FRAME_MS)
        start_frames = int(start_timeout * 1000 / FRAME_MS)
        # Speech before the wake word ("game time, Jarvis"): the preroll minus the wake word itself.
        before_wake = None
        if preroll is not None and preroll.size > SAMPLE_RATE:
            head = preroll[: -int(SAMPLE_RATE * 0.9)]
            if any(rms(head[i:i + 1280]) > RMS_THRESHOLD for i in range(0, head.size, 1280)):
                before_wake = preroll
        if before_wake is not None:
            start_frames = min(start_frames, int(1200 / FRAME_MS))  # it was said already: don't wait long

        frames: list[np.ndarray] = []
        started, quiet, waited = False, 0, 0
        captions = _LiveCaptions(self.live, before_wake)
        try:
            for _ in range(max_frames):
                if self.stop.is_set():
                    return None
                frame = stream.read(timeout=0.5)
                if frame is None:
                    continue
                voiced = rms(frame) > RMS_THRESHOLD
                if not started:
                    if voiced:
                        started = True
                        frames.append(frame)
                    else:
                        waited += 1
                        if waited >= start_frames:
                            break
                    continue
                frames.append(frame)
                captions.feed(frames)
                quiet = 0 if voiced else quiet + 1
                if quiet >= silence_end:
                    break
        finally:
            captions.stop()
        if frames:
            audio = np.concatenate(frames)
            return np.concatenate([before_wake, audio]) if before_wake is not None else audio
        return before_wake

    def _handle(self, audio: np.ndarray) -> bool:
        """Transcribe and act. True keeps a follow-up window open."""
        from speech import goodnight, quick_commands
        from speech.tts import speak
        voice_presence.update("thinking")
        started = time.perf_counter()
        text = strip_wake_word(self.final.transcribe(audio)).strip()
        log.info("Room voice heard %r (%.0f ms)", text, (time.perf_counter() - started) * 1000)
        voice_presence.update(transcript=text)
        if not text:
            voice_presence.update("idle")
            return False

        if goodnight.is_goodnight(text):
            outcome = goodnight.run(self._say, self._listen_for_answer, self._lights_out)
            log.info("Goodnight routine: alarm %s", outcome)
            voice_presence.update("idle")
            return False

        try:
            quick = quick_commands.run(text)
        except Exception:  # noqa: BLE001
            log.exception("Quick command failed for %r", text)
            quick = None
        if quick is not None:
            log.info("Room voice quick command: %s ok=%s %s", quick.action, quick.ok, quick.error)
            self._finish("done" if quick.ok else "error", "" if quick.ok else "That didn't work, sir.")
            return False

        try:
            reply = self.runtime.respond(text, spoken=True)
        except Exception:  # noqa: BLE001
            log.exception("Room voice turn failed")
            self._finish("error", "Something went wrong, sir.")
            return False
        if self.runtime.last_used_tools and reply.strip().lower().rstrip(".!") in _JINGLE_REPLIES:
            self._finish("done", "")
            return False
        voice_presence.update("speaking", reply=reply)
        speak(reply)
        voice_presence.update("idle")
        # A question or a chat keeps listening briefly; a finished task doesn't.
        return not self.runtime.last_used_tools

    # --- for routines that hold a short conversation (goodnight) ---------------

    def _say(self, text: str) -> None:
        from speech.tts import speak
        voice_presence.update("speaking", reply=text)
        speak(text, cache=True)  # routine lines repeat: instant from the second time

    def _listen_for_answer(self) -> str | None:
        """One reply, no wake word needed; None if Nick says nothing."""
        self.stream.drain()  # what's queued is JARVIS's own question, not the answer
        voice_presence.update("listening")
        audio = self._capture(self.stream, None, start_timeout=_seconds("VOICE_FOLLOWUP_S", 5.0))
        if audio is None:
            return None
        voice_presence.update("thinking")
        text = strip_wake_word(self.final.transcribe(audio)).strip()
        voice_presence.update(transcript=text)
        log.info("Room voice answer %r", text)
        return text

    def _lights_out(self) -> None:
        """HA's Goodnight scene, in the background so "Sleep well" isn't kept waiting."""
        from app import status_hub

        def run() -> None:
            try:
                scenes = status_hub.home_scenes().get("scenes", [])
                scene = next((s for s in scenes if s["entity_id"] == "scene.goodnight"
                              or s["name"].lower().replace(" ", "") == "goodnight"), None)
                if scene is None:
                    log.warning("Goodnight routine: no Goodnight scene in Home Assistant")
                    return
                status_hub.activate_scene(scene["entity_id"])
            except Exception:  # noqa: BLE001
                log.exception("Goodnight scene failed")

        threading.Thread(target=run, name="goodnight-scene", daemon=True).start()

    def _finish(self, phase: str, reply: str) -> None:
        voice_presence.update(phase, reply=reply)
        room_sounds.play("done" if phase == "done" else "error", wait=True)
        time.sleep(0.6)  # long enough for a screen to show the tick
        voice_presence.update("idle")


class _LiveCaptions:
    """Re-transcribes the utterance so far with the tiny model, at most one
    pass at a time, and publishes it as the live transcript."""

    def __init__(self, model, before: np.ndarray | None) -> None:  # noqa: ANN001
        self.model, self.before = model, before
        self.busy = threading.Lock()
        self.last_len = 0
        self.stopped = False

    def feed(self, frames: list[np.ndarray]) -> None:
        if self.model is None or self.stopped or len(frames) - self.last_len < 6:  # ~0.5 s of new audio
            return
        if not self.busy.acquire(blocking=False):
            return
        self.last_len = len(frames)
        audio = np.concatenate(frames)
        if self.before is not None:
            audio = np.concatenate([self.before, audio])
        threading.Thread(target=self._run, args=(audio,), daemon=True).start()

    def _run(self, audio: np.ndarray) -> None:
        try:
            text = strip_wake_word(self.model.transcribe(audio)).strip()
            # A late pass must not overwrite the final transcript.
            if text and not self.stopped and voice_presence.current()["phase"] == "listening":
                voice_presence.update(transcript=text)
        except Exception:  # noqa: BLE001
            log.debug("live caption failed", exc_info=True)
        finally:
            self.busy.release()

    def stop(self) -> None:
        self.stopped = True


_voice: RoomVoice | None = None


def start(runtime) -> RoomVoice | None:  # noqa: ANN001
    """Start the room voice thread once, if ROOM_VOICE is on."""
    global _voice
    if _voice is not None or not enabled():
        return _voice
    _voice = RoomVoice(runtime)
    threading.Thread(target=_voice.run_forever, name="room-voice", daemon=True).start()
    return _voice


def stop() -> None:
    if _voice is not None:
        _voice.stop.set()
