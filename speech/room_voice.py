"""The room voice: "Hey JARVIS" on the Optiplex's mic and speakers.

Runs inside JARVIS Core (``python -m app.main --server``) when ``ROOM_VOICE=1``,
on the same runtime the apps use, so the iPad can show it live
(``brain/voice_presence.py`` -> ``GET /api/voice/stream``).

Built for smart-speaker speed:

1. openWakeWord ``hey_jarvis`` on every 80 ms frame; a soft "listen" sound.
2. Record until ~1 s of silence (``VOICE_SILENCE_END_MS``). While you
   talk, a tiny Whisper model (``VOICE_LIVE_MODEL``, tiny.en) captions it
   live for the iPad; the main model (``WHISPER_MODEL``; base.en suits the
   Optiplex) makes the final transcript.
3. Plain home commands (scenes, on/off, brightness) run directly
   (``speech/quick_commands.py``) and answer with a jingle, no words.
4. Everything else is a normal turn with a "spoken reply" hint: one short
   sentence. A reply of just "Done." after a home action becomes the jingle.
5. After a spoken answer, a short follow-up window (``VOICE_FOLLOWUP_S``)
   takes the next question without the wake word.

Smart-speaker extras:

- Instant answers with no model (``speech/quick_answers.py``): time, date,
  weather now, timers ("ten minute pasta timer", "how long's left",
  "cancel the timer"), volume ("volume 4", "louder"), "stop", "say that
  again"; "good morning" runs the morning routine (``speech/good_morning.py``).
- Timers ring on the speaker (the ``alarm`` beeps, then "Sir, your pasta
  timer is done") until "Hey JARVIS" (or stop, or the app) dismisses them,
  for at most ``ROOM_ALARM_RING_S`` (120). Reminders and broadcasts get a
  chime and are spoken; other announcements wait out ``ROOM_QUIET_HOURS``.
  Core's room voice owns ``runtime.pop_due_reminders()`` in server mode.
- Barge-in: "Hey JARVIS" while he's talking cuts him off and listens
  (``VOICE_BARGE_IN``, on by default).
- Night mode: ``speech/room_volume.py`` lowers the speaker in quiet hours.
- ``announce(text)``: speak a broadcast from the phone or a chat
  (``POST /api/voice/announce``, the ``announce_in_room`` tool).
- ``hush()``: tap the iPad while he's listening, thinking or talking and the
  whole exchange ends (``POST /api/voice/hush``): playback stops, a capture or
  follow-up window closes, a reply still being written is never spoken, and
  he waits for "Hey JARVIS" again. For a room full of people talking, where
  each overheard sentence kept the follow-up window open.

The wake word works at the end too ("game time, Jarvis"): a little audio
from before the wake word is kept and used when nothing follows it.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import os
import re
import threading
import time

import numpy as np

from brain import voice_presence, voice_shortcuts
from jarvis_logger import get_logger
from speech import room_sounds, room_volume
from speech.audio import FRAME_MS, SAMPLE_RATE, MicStream, rescan_devices, rms, rms_threshold
from speech.wake_words import strip_wake_word

log = get_logger(__name__)

_JINGLE_REPLIES = {"done", "yes sir", "yes, sir", "ok", "okay"}
# While a timer rings, any of these (with or without "Hey JARVIS") stops it.
_GUEST_ALARM_LINE = "That alarm is Nick's to stop, I'm afraid. He can end it from his phone."
_DISMISS = re.compile(r"\b(stop|cancel|dismiss|enough|off|okay|ok|thanks|thank you|got it|i'm up|shut up|quiet)\b")


def _streaming_enabled() -> bool:
    """STREAMING_TTS: speak a model reply a sentence at a time as it's written."""
    return os.getenv("STREAMING_TTS", "false").strip().lower() in ("1", "true", "yes", "on")


def _vocabulary_prompt() -> str:
    """A sentence of the room's own words (scene and device names, timers,
    volume) so Whisper hears "desk lights" rather than "this flights"."""
    names: list[str] = []
    try:
        from app import status_hub
        names += [s["name"] for s in status_hub.home_scenes().get("scenes", [])][:10]
        names += [e["friendly_name"] for e in status_hub.home_entities().get("entities", [])][:12]
    except Exception:  # noqa: BLE001 - Home Assistant down: the generic words still help
        log.debug("No HA names for the Whisper prompt", exc_info=True)
    listed = ", ".join(dict.fromkeys(n.replace("’", "'") for n in names if n))
    base = "Hey Jarvis, turn on the lights. Set a timer for 10 minutes. Volume up. What's the weather? Pause the music. Barn Door Protocol, stand down."
    return f"{base} {listed}." if listed else base


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
        self.stream = None
        self.last_reply = ""
        self._barged = False
        self._hushed = threading.Event()  # a screen tap ended this exchange (hush())
        self._broadcasts: deque[str] = deque(maxlen=10)
        self._ringing_since: float | None = None
        self._next_ring = 0.0
        self._next_duties = 0.0
        self._spoke = False
        self._noise = 0.0  # the room's background level (RMS), learned between commands
        self._wake_hits = 0  # consecutive frames over the threshold (debounce)

    # --- lifecycle -----------------------------------------------------------

    def preload(self) -> None:
        """Load models up front so the first "Hey JARVIS" isn't slow."""
        prompt = _vocabulary_prompt()
        for model in (self.final, self.live):
            if model is not None and hasattr(model, "prompt"):
                model.prompt = prompt
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
                rescan_devices()  # a mic plugged in since startup is invisible until PortAudio rescans

    # --- the loop ------------------------------------------------------------

    def _loop(self, stream: MicStream) -> None:
        self.stream = stream  # routines (goodnight) listen for an answer on it
        need_wake = True
        barged = False
        while not self.stop.is_set():
            stream.drain()
            if barged:  # "Hey JARVIS" cut him off: listen straight away
                voice_presence.update("listening")
                room_sounds.play("listen")
                audio = self._capture(stream, None, start_timeout=_seconds("VOICE_START_TIMEOUT_S", 4.0))
            elif need_wake:
                if not self._wait_for_wake(stream):
                    return
                self._hushed.clear()  # a tap while idle mustn't cut off the next "Hey JARVIS"
                self._warm_connections()  # handshakes happen while Nick is still talking
                preroll = stream.get_recent_audio(_seconds("VOICE_PREROLL_S", 2.0))
                voice_presence.update("listening")
                room_sounds.play("listen")
                audio = self._capture(stream, preroll, start_timeout=_seconds("VOICE_START_TIMEOUT_S", 4.0))
            else:  # follow-up: no wake word, a shorter wait
                voice_presence.update("listening")
                audio = self._capture(stream, None, start_timeout=_seconds("VOICE_FOLLOWUP_S", 8.0))
            if audio is None:
                voice_presence.update("idle")
                need_wake = True
                barged = False
                continue
            need_wake = not self._handle(audio)
            barged, self._barged = self._barged, False
            if self._hushed.is_set():  # tapped mid-exchange: no follow-up, no barge-in listen
                voice_presence.update("idle")
                need_wake, barged = True, False

    def _warm_connections(self) -> None:
        """Open the connections this exchange will need (transcript, model,
        speech) in the background; each skips its ~90 ms handshake later."""
        try:
            from speech import stt, tts
            stt.warm_cloud()
            tts.warm_cloud()
            self.runtime.warm()
        except Exception:  # noqa: BLE001 - only a head start; never worth a missed command
            log.debug("Connection warm-up failed", exc_info=True)

    def hush(self) -> bool:
        """End the current exchange (a tap on the iPad). True if there was one."""
        from speech.tts import stop_playback
        busy = voice_presence.current()["phase"] != "idle"
        if busy:
            log.info("Room voice hushed from a screen")
            self._hushed.set()
            stop_playback()
            voice_presence.update("idle", reply="")
        return busy

    def _heard_wake(self, frame: np.ndarray) -> bool:
        """True only after the wake word scores over the threshold on
        ``WAKE_CONSECUTIVE`` frames in a row (default 2). One stray frame of
        noise or TV chatter crossing the line no longer wakes him; a real
        "Hey JARVIS" clears several frames easily. Resetting the detector
        (after a fire, barge-in or dismissal) also resets the streak."""
        from speech.wake import wake_threshold
        try:
            scores = self.detector._model.predict(frame)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            log.exception("openWakeWord predict() failed; skipping frame")
            return False
        top = max(scores.values(), default=0.0)
        if top < wake_threshold():
            self._wake_hits = 0
            return False
        self._wake_hits += 1
        try:
            needed = max(1, int(os.getenv("WAKE_CONSECUTIVE", "2")))
        except ValueError:
            needed = 2
        if self._wake_hits < needed:
            return False
        log.info("Wake word fired (score=%.2f, %d frames)", top, self._wake_hits)
        self._wake_hits = 0
        return True

    def _wait_for_wake(self, stream: MicStream) -> bool:
        self._wake_hits = 0
        self.detector.reset()
        while not self.stop.is_set():
            self._duties(stream)
            frame = stream.read(timeout=0.5)
            if frame is None:
                continue
            self._learn_noise(frame)
            if self._heard_wake(frame):
                if self._ringing_since is not None or self._wake_ringing():  # "Hey JARVIS" while ringing: that's stop
                    self._dismiss_alarms()
                    self.detector.reset()
                    continue
                return True
        return False

    # --- alarms, reminders, broadcasts ------------------------------------------

    def _duties(self, stream: MicStream) -> None:
        """Between wake-word frames: ring timers, speak what's due."""
        now = time.monotonic()
        if now >= self._next_duties:
            self._next_duties = now + 1.0
            try:
                due = list(self.runtime.pop_due_reminders())
            except Exception:  # noqa: BLE001
                log.debug("pop_due_reminders failed", exc_info=True)
                due = []
            for item in due:
                self._deliver(item)
            while self._broadcasts:
                self._chime_and_say(self._broadcasts.popleft())
        if self._ringing_since is None and self._wake_ringing():
            # Wake-up backup (brain/wake_alarm.py): the TV didn't come on.
            if now >= self._next_ring:
                self._next_ring = now + 3.5
                room_volume.ring_alarm()  # ROOM_ALARM_VOLUME, never the night level
                room_sounds.play("alarm")
        if self._ringing_since is not None:
            if not self._alarming() or now - self._ringing_since > _seconds("ROOM_ALARM_RING_S", 120):
                self._ringing_since = None
                voice_presence.update("idle")
            elif now >= self._next_ring:
                self._next_ring = now + 3.5
                room_volume.prepare()
                room_sounds.play("alarm")
        if self._spoke:
            self._spoke = False
            stream.drain()  # don't hear ourselves as the wake word
            self.detector.reset()

    def _deliver(self, item) -> None:  # noqa: ANN001
        from brain.reminders import Reminder
        from brain.timers import Timer
        from speech import quick_answers
        if isinstance(item, Timer):
            label = quick_answers.timer_label(item.message)
            log.info("Timer ringing: %s", item.id)
            self._ringing_since = time.monotonic()
            self._next_ring = 0.0
            room_volume.prepare()
            room_sounds.play("alarm", wait=True)
            self._say(f"Sir, your {label} timer is done." if label else "Sir, your timer is done.")
            voice_presence.update("speaking", reply="Time's up")
        elif isinstance(item, Reminder):
            self._chime_and_say(f"Reminder, sir: {item.message}")
        elif not room_volume.quiet_now() and os.getenv("ROOM_ANNOUNCEMENTS", "1").strip().lower() not in (
                "0", "off", "false", "no"):
            self._chime_and_say(str(getattr(item, "message", "") or ""))

    def _chime_and_say(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        room_volume.prepare()
        room_sounds.play("chime", wait=True)
        self._say(text)
        voice_presence.update("idle")

    @staticmethod
    def _wake_ringing() -> bool:
        from brain import wake_alarm
        return wake_alarm.ringing()

    def _alarming(self) -> list:
        from brain import timers
        try:
            return [t for t in timers.active_timers() if t.alarming]
        except Exception:  # noqa: BLE001
            return []

    def _dismiss_alarms(self) -> bool:
        from brain import timers, wake_alarm
        if self._wake_ringing():
            wake_alarm.im_up()  # stops the ring, lights on, TV wake screen closes
            log.info("Dismissed the wake-up backup ring")
            room_sounds.play("done")
            voice_presence.update("idle")
            return True
        ringing = self._alarming()
        for t in ringing:
            timers.dismiss_timer(t.id)
        self._ringing_since = None
        if ringing:
            log.info("Dismissed %d ringing timer(s)", len(ringing))
            room_sounds.play("done")
            voice_presence.update("idle")
        return bool(ringing)

    def broadcast(self, text: str) -> None:
        self._broadcasts.append(text)

    # --- speaking (with barge-in) ------------------------------------------------

    def _speak(self, text: str, cache: bool | str = False) -> bool:
        """Say ``text``; True if Nick cut in with the wake word."""
        from speech.tts import speak
        self.last_reply = text
        return self._play(lambda _interrupt: speak(text, cache=cache))

    def _play(self, say) -> bool:  # noqa: ANN001
        """Run ``say(interrupt_event)`` while listening for a barge-in; True if
        Nick cut in with the wake word. The event is set by a barge-in or a
        hush, so a reply spoken a sentence at a time stops between sentences
        too, not only mid-clip."""
        from speech.tts import stop_playback
        room_volume.prepare()
        stream = self.stream
        if self._hushed.is_set():
            return False
        if stream is None or os.getenv("VOICE_BARGE_IN", "1").strip().lower() in ("0", "off", "false", "no"):
            say(self._hushed)  # a hush is the only way to stop it
            return False
        interrupt = threading.Event()
        done = threading.Event()

        def run() -> None:
            try:
                say(interrupt)
            finally:
                done.set()

        threading.Thread(target=run, name="room-voice-speak", daemon=True).start()
        self.detector.reset()
        stream.drain()
        while not done.is_set() and not self.stop.is_set():
            if self._hushed.is_set():  # synthesis may finish after the tap's stop: keep it quiet
                interrupt.set()
                stop_playback()
                done.wait(0.1)
                continue
            frame = stream.read(timeout=0.1)
            if frame is not None and self._heard_wake(frame):
                log.info("Barge-in: stopped speaking")
                interrupt.set()
                stop_playback()
                done.wait(2)
                self.detector.reset()
                return True
        return False

    def _learn_noise(self, frame: np.ndarray) -> None:
        """Track the room's background level while nobody's talking to JARVIS
        (a slow average, so a word now and then barely moves it)."""
        level = rms(frame)
        self._noise = level if self._noise == 0.0 else self._noise * 0.98 + min(level, self._noise * 3) * 0.02

    def _speech_level(self) -> float:
        """RMS that counts as speech: a few times the room's background, never
        stricter than MIC_RMS_THRESHOLD (a mic across the room is quiet)."""
        ceiling = rms_threshold()  # live: JARVIS may have retuned it
        if self._noise <= 0:
            return ceiling
        floor = _seconds("VOICE_MIN_RMS", 90)
        return min(ceiling, max(floor, self._noise * _seconds("VOICE_NOISE_RATIO", 2.5)))

    def _capture(self, stream: MicStream, preroll: np.ndarray | None, start_timeout: float) -> np.ndarray | None:
        """One utterance, captioned live; None if nobody spoke."""
        silence_end = int(_seconds("VOICE_SILENCE_END_MS", 1000) / FRAME_MS)
        min_frames = int(_seconds("VOICE_MIN_UTTERANCE_MS", 1000) / FRAME_MS)
        max_frames = int(_seconds("VOICE_MAX_UTTERANCE_S", 15) * 1000 / FRAME_MS)
        start_frames = int(start_timeout * 1000 / FRAME_MS)
        # Speech before the wake word ("game time, Jarvis"): the preroll minus the wake word itself.
        loud = self._speech_level()
        before_wake = None
        if preroll is not None and preroll.size > SAMPLE_RATE:
            head = preroll[: -int(SAMPLE_RATE * 0.9)]
            if any(rms(head[i:i + 1280]) > loud for i in range(0, head.size, 1280)):
                before_wake = preroll
        if before_wake is not None:
            start_frames = min(start_frames, int(1200 / FRAME_MS))  # it was said already: don't wait long

        frames: list[np.ndarray] = []
        lead_in: deque[np.ndarray] = deque(maxlen=3)  # the quiet start of the first word
        started, quiet, waited, peak = False, 0, 0, 0.0
        captions = _LiveCaptions(self.live, before_wake)
        try:
            for _ in range(max_frames):
                if self.stop.is_set() or self._hushed.is_set():
                    return None
                frame = stream.read(timeout=0.5)
                if frame is None:
                    continue
                level = rms(frame)
                peak = max(peak, level)
                voiced = level > loud
                if not started:
                    if voiced:
                        started = True
                        frames.extend(lead_in)
                        frames.append(frame)
                    else:
                        lead_in.append(frame)
                        waited += 1
                        if waited >= start_frames:
                            break
                    continue
                frames.append(frame)
                captions.feed(frames)
                quiet = 0 if voiced else quiet + 1
                if quiet >= silence_end and len(frames) >= min_frames:
                    break
        finally:
            captions.stop()
        if frames:
            log.info("Room voice captured %.1f s (peak %.0f, room %.0f, speech above %.0f)",
                     len(frames) * FRAME_MS / 1000, peak, self._noise, loud)
            audio = np.concatenate(frames)
            return np.concatenate([before_wake, audio]) if before_wake is not None else audio
        return before_wake

    def _handle(self, audio: np.ndarray) -> bool:
        """Transcribe and act. True keeps a follow-up window open."""
        from brain import speaker_id, voice_trust
        from speech import good_morning, goodnight, quick_answers, quick_commands
        voice_presence.update("thinking")
        if speaker_id.enrolling():  # "learn my voice": this sentence is a sample, not a command
            said = speaker_id.take_sample(audio)
            voice_presence.update("speaking", reply=said)
            self._speak(said)
            voice_presence.update("idle")
            return speaker_id.enrolling()  # keep listening without the wake word until done
        started = time.perf_counter()
        # Whose voice it is (40-80 ms of CPU) is worked out while the
        # transcript is on its way back from the cloud, not after it.
        verdict: list = []

        def who() -> None:
            try:
                verdict.append(speaker_id.identify(audio))
            except Exception:  # noqa: BLE001 - decided below: an unknown voice is a guest
                log.exception("Voice ID failed")

        identifying = threading.Thread(target=who, name="voice-id", daemon=True)
        identifying.start()
        text = strip_wake_word(self.final.transcribe(audio)).strip()
        log.info("Room voice heard %r (%.0f ms)", text, (time.perf_counter() - started) * 1000)
        identifying.join()
        if self._hushed.is_set():
            return False
        voice_presence.update(transcript=text)
        if not text:
            voice_presence.update("idle")
            return False
        # Nick, or a guest who can't use locked tools.
        speaker = verdict[0] if verdict else voice_trust.guest("voice ID failed")

        # A staged proposal (a trade, a fix) is waiting on a bare "yes": the
        # answer goes to the gate, never to a fast path that would leave it armed.
        routed = self._answer_to_proposal(text, speaker)
        if routed is not None:
            return routed

        if (self._alarming() or self._wake_ringing()) and _DISMISS.search(text.lower()):
            if self._wake_ringing() and not speaker.verified:
                # Nick's wake-up is his to end: a guest's "stop" doesn't count as him being up.
                log.info("Wake-up ring: ignored a guest's dismissal (%s)", speaker.reason)
                self._say(_GUEST_ALARM_LINE)
                voice_presence.update("idle")
                return False
            self._dismiss_alarms()
            voice_presence.update("idle")
            return False

        if good_morning.is_good_morning(text) and speaker.verified:
            good_morning.run(self._say)
            voice_presence.update("idle")
            return False

        try:
            answer = quick_answers.answer(text, self.last_reply)
        except Exception:  # noqa: BLE001
            log.exception("Quick answer failed for %r", text)
            answer = None
        if answer is not None:
            log.info("Room voice quick answer: %s", answer.kind)
            if answer.stop:
                from speech.tts import stop_playback
                self._dismiss_alarms()
                stop_playback()
                voice_presence.update("idle")
                return False
            if answer.say:
                voice_presence.update("speaking", reply=answer.say)
                self._barged = self._speak(answer.say, cache="auto")
                voice_presence.update("idle")
                return False
            self._finish(answer.sound or "done", "")
            return False

        if goodnight.is_goodnight(text):
            # A guest gets the Goodnight scene but not the alarm question: only
            # Nick moves or skips his wake-up (with voice ID off, everyone is Nick).
            outcome = goodnight.run(self._say, self._listen_for_answer, self._lights_out,
                                    ask_alarm=speaker.verified)
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

        shortcut = voice_shortcuts.lookup(text)
        if shortcut is not None:  # learned from how Claude handled this phrase before
            from brain import tool_health, tools
            with voice_trust.speaking(speaker):
                result = tools.execute_tool(*shortcut)
            worked = not tool_health.looks_like_failure(result) and not result.startswith("Tool error")
            voice_shortcuts.used(text, worked)
            log.info("Room voice shortcut %r -> %s: %s", text, shortcut[0], result[:120])
            self._finish("done" if worked else "error", "" if worked else "That didn't work, sir.")
            return False

        return self._converse(text, speaker)

    def _answer_to_proposal(self, text: str, speaker) -> bool | None:  # noqa: ANN001
        """While a confirm-gate "yes" is armed in the room session: a yes or a
        code goes to the model turn (the gate there checks who may approve,
        and money needs a voiceprint match); "cancel" / "never mind" / "forget
        it" cancels it out loud; anything else disarms it and carries on down
        the normal paths. None: nothing armed, or the utterance carries on."""
        from brain import confirm_gate, voice_trust
        session = getattr(self.runtime, "room_session_id", None)
        if not isinstance(session, str) or not confirm_gate.awaiting_yes(session):
            return None
        kind = confirm_gate.reply_kind(text)
        if kind in ("yes", "codes"):
            log.info("Room voice: %r answers a staged proposal", text)
            return self._converse(text, speaker)
        if kind == "cancel" and speaker.verified:
            with voice_trust.speaking(speaker):
                said = confirm_gate.conversation_action(text, session)
            if self._alarming() or self._wake_ringing():
                self._dismiss_alarms()
            if said:
                voice_presence.update("speaking", reply=said)
                self._speak(said)
            voice_presence.update("idle")
            return False
        # Anything else (or a guest's "cancel"): no longer armed, so a later
        # "okay" can't approve it; it stays open for "approve <code>" or the app.
        confirm_gate.conversation_action(text, session, handled=True)
        return None

    def _converse(self, text: str, speaker) -> bool:  # noqa: ANN001
        """A model turn in the room session; True keeps a follow-up window open."""
        from brain import voice_trust
        if _streaming_enabled() and getattr(self.runtime, "brain_online", False):
            return self._converse_streamed(text, speaker)
        try:
            with voice_trust.speaking(speaker):
                reply = self.runtime.respond(text, spoken=True, room=True)
        except Exception:  # noqa: BLE001
            log.exception("Room voice turn failed")
            self._finish("error", "Something went wrong, sir.")
            return False
        if self._hushed.is_set():  # tapped while he was thinking: keep the answer to himself
            log.info("Room voice reply dropped after a hush: %r", reply[:80])
            return False
        return self._answer(text, reply)

    def _answer(self, text: str, reply: str) -> bool:
        """Say a finished reply (or play the jingle for a bare "Done." after a
        tool); True keeps a follow-up window open."""
        if self.runtime.last_used_tools and reply.strip().lower().rstrip(".!") in _JINGLE_REPLIES:
            self._learn(text)
            self._finish("done", "")
            return False
        voice_presence.update("speaking", reply=reply)
        self._barged = self._speak(reply, cache="auto")  # a reply said before replays without synthesis
        voice_presence.update("idle")
        return self._wants_follow_up(reply)

    def _wants_follow_up(self, reply: str) -> bool:
        # A question or a chat keeps listening briefly; a finished task doesn't,
        # unless it asked something ("which one?") or waits on Nick's yes.
        from brain import confirm_gate
        asked = "?" in reply or confirm_gate.awaiting_yes(self.runtime.room_session_id)
        return (asked or not self.runtime.last_used_tools) and not self._barged

    def _converse_streamed(self, text: str, speaker) -> bool:  # noqa: ANN001
        """A model turn spoken sentence by sentence while Claude is still
        writing the rest (``STREAMING_TTS``), so a long answer starts as soon
        as its first sentence exists instead of after the last one.

        One worker thread runs the whole turn: the runtime's turn lock is
        re-entrant (owned by the thread that took it) and voice trust is a
        context variable, so neither may hop threads mid-turn. It always
        drains the reply, even after a hush or barge-in, so history keeps
        what was said. A reply that is one sentence (or "Done.") ends before
        anything is spoken and takes the normal path: jingle, cache, presence.
        """
        import queue

        from brain import voice_trust
        from speech.tts import _split_ready_sentences, speak_stream

        fragments: queue.Queue = queue.Queue()
        parts: list[str] = []
        end = object()
        failed = threading.Event()

        def produce() -> None:
            try:
                with voice_trust.speaking(speaker):
                    for fragment in self.runtime.respond_streaming(text, spoken=True, room=True):
                        if fragment:
                            parts.append(fragment)
                            fragments.put(fragment)
            except Exception:  # noqa: BLE001
                log.exception("Room voice turn failed")
                failed.set()
            finally:
                fragments.put(end)

        threading.Thread(target=produce, name="room-voice-turn", daemon=True).start()

        head, finished = "", False
        while True:  # thinking: wait for a whole first sentence (or the end)
            item = fragments.get()
            if item is end:
                finished = True
                break
            head += item
            if _split_ready_sentences(head)[0]:
                break

        if failed.is_set() and not head.strip():
            self._finish("error", "Something went wrong, sir.")
            return False
        if self._hushed.is_set():
            log.info("Room voice reply dropped after a hush: %r", head[:80])
            return False
        if finished:
            return self._answer(text, head) if head.strip() else False

        def rest():
            yield head
            while (item := fragments.get()) is not end:
                yield item
            voice_presence.update(reply="".join(parts).strip())

        log.info("Room voice streaming its reply: %r…", head[:60])
        voice_presence.update("speaking", reply=head.strip())
        self._barged = self._play(lambda interrupt: speak_stream(rest(), interrupt_event=interrupt))
        self.last_reply = "".join(parts).strip()
        voice_presence.update("idle")
        if self._barged or self._hushed.is_set():
            return False  # the turn finishes writing in the background
        return self._wants_follow_up(self.last_reply)

    def _learn(self, text: str) -> None:
        """A simple action Claude did for this phrase: maybe now a shortcut."""
        try:
            if voice_shortcuts.observe(text, self.runtime.last_turn_tools):
                log.info("Room voice learned a shortcut: %r", text)
                self.runtime.record_event(kind="notice", source="room-voice", title="Learned a voice shortcut",
                                          detail=f'"{voice_shortcuts.normalize(text)}" now runs instantly.')
        except Exception:  # noqa: BLE001
            log.exception("Shortcut learning failed")

    # --- for routines that hold a short conversation (goodnight) ---------------

    def _say(self, text: str) -> None:
        from speech.tts import speak
        if self._hushed.is_set():
            return
        voice_presence.update("speaking", reply=text)
        room_volume.prepare()
        self.last_reply = text
        speak(text, cache=True)  # routine lines repeat: instant from the second time
        self._spoke = True

    def _listen_for_answer(self) -> str | None:
        """One reply, no wake word needed; None if Nick says nothing."""
        self.stream.drain()  # what's queued is JARVIS's own question, not the answer
        voice_presence.update("listening")
        audio = self._capture(self.stream, None, start_timeout=_seconds("VOICE_FOLLOWUP_S", 8.0))
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
        room_volume.prepare()
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


def hush() -> bool:
    """End the room voice's current exchange; False when idle or not running."""
    return _voice.hush() if _voice is not None else False


def announce(text: str) -> bool:
    """Speak ``text`` on the room speaker (with a chime) at the next pause;
    False when the room voice isn't running here."""
    text = (text or "").strip()
    if _voice is None or not text:
        return False
    _voice.broadcast(text[:400])
    return True
