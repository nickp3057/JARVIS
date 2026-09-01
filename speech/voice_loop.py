"""The always-on voice mode for JARVIS (CLI / headless).

Wake word handling: rolling buffer + brief forward listen + wake-word
stripping, so "What time is it Jarvis?" works as well as "Hey Jarvis,
what time is it?".

Smart follow-up:
- After a TASK reply (Claude used a tool - turning lights, setting timers,
  Spotify, etc.), close the follow-up window quickly. Lets you talk to
  someone else without JARVIS catching unrelated chatter.
- After a CONVERSATIONAL reply (no tool use), keep follow-up open the
  full window so a flowing chat works.

Interruption: while JARVIS is speaking, a watcher thread monitors the mic
for voice activity above a high threshold. If the user starts talking
deliberately, playback is killed and we go straight to listening.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time

import numpy as np

from brain.runtime import JarvisRuntime
from jarvis_logger import get_logger
from speech.audio import MicStream, record_until_silence, rms
from speech.chime import play_chime
from speech.stt import Transcriber
from speech.tts import speak, speak_stream
from speech.wake import WAKE_THRESHOLD, WakeDetector

log = get_logger(__name__)


def _log_stage(label: str, seconds: float) -> None:
    """Print a stage timing line to stdout. Temporary profiling instrumentation."""
    print(f"[TIMING] {label}: {seconds * 1000:.0f}ms")

GREETING = "Pearson Stark JARVIS online. Standing by, sir."
GOODBYE = "Shutting down. See you, sir."

_EXIT_PHRASES = {
    "exit", "quit", "bye", "goodbye",
    "shut down", "shutdown",
    "go to sleep", "that's all", "thats all",
}

# Follow-up window: short after task completion, long after open-ended chat.
FOLLOWUP_CONVERSATIONAL_S = float(os.getenv("FOLLOWUP_TIMEOUT_S", "10.0"))
FOLLOWUP_TASK_S = float(os.getenv("FOLLOWUP_TASK_S", "2.5"))

MAX_UTTERANCE_S = float(os.getenv("MAX_UTTERANCE_S", "20.0"))
PREROLL_SECONDS = float(os.getenv("PREROLL_SECONDS", "4.0"))
POSTROLL_START_TIMEOUT_S = float(os.getenv("POSTROLL_START_TIMEOUT_S", "1.5"))
POSTROLL_MAX_S = float(os.getenv("POSTROLL_MAX_S", "10.0"))

# Interrupt detection during TTS playback.
INTERRUPT_RMS_THRESHOLD = float(os.getenv("INTERRUPT_RMS_THRESHOLD", "1500"))
INTERRUPT_CONSECUTIVE_FRAMES = int(os.getenv("INTERRUPT_CONSECUTIVE_FRAMES", "3"))


_WAKE_PATTERNS = [
    re.compile(r"^\s*(?:hey|hi|hay|hi,)\s+jarvis[\s,.!?:;-]*", re.IGNORECASE),
    re.compile(r"[\s,.!?:;-]+(?:hey\s+|hi\s+)?jarvis[\s,.!?:;-]*$", re.IGNORECASE),
    re.compile(r"^\s*jarvis[\s,.!?:;-]+", re.IGNORECASE),
    re.compile(r"^\s*jarvis\s*$", re.IGNORECASE),
]


def _strip_wake_word(text: str) -> str:
    cleaned = text
    for pattern in _WAKE_PATTERNS:
        while True:
            new = pattern.sub(" ", cleaned).strip()
            if new == cleaned:
                break
            cleaned = new
    return cleaned.strip()


def _is_exit_phrase(text: str) -> bool:
    cleaned = text.strip().lower().strip(".!?")
    return cleaned in _EXIT_PHRASES


def _say(message: str) -> None:
    print(f"Jarvis: {message}")
    speak(message)


def _streaming_enabled() -> bool:
    return os.getenv("STREAMING_TTS", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _capture_utterance(
    stream: MicStream,
    preroll: np.ndarray | None = None,
) -> np.ndarray | None:
    """Capture one utterance around the wake-word moment.

    ``preroll`` should be a snapshot taken *before* the chime played so that
    trailing-wake-word phrases ("turn on the lights, Jarvis") aren't pushed
    out of the rolling buffer by the chime audio.  If not supplied we fall
    back to reading from the live buffer (legacy behaviour).
    """
    if preroll is None:
        preroll = stream.get_recent_audio(PREROLL_SECONDS)
    stream.drain()
    postroll = record_until_silence(
        stream,
        max_seconds=POSTROLL_MAX_S,
        start_timeout=POSTROLL_START_TIMEOUT_S,
    )
    parts: list[np.ndarray] = []
    if preroll is not None and preroll.size > 0:
        parts.append(preroll)
    if postroll is not None and postroll.size > 0:
        parts.append(postroll)
    if not parts:
        return None
    return np.concatenate(parts)


def _speak_interruptible(stream: MicStream, text: str) -> bool:
    """Speak ``text`` while watching the mic for voice activity.

    Returns True if the user interrupted (so the caller should jump back
    to listening). False if playback completed normally.
    """
    if not text or not text.strip():
        return False

    print(f"Jarvis: {text}")
    return _speak_stream_interruptible(stream, iter((text,)), streaming=False)


def _speak_stream_interruptible(stream: MicStream, chunks, *, streaming: bool = True) -> bool:
    """Speak text fragments while preserving headless mic interruption."""
    interrupt = threading.Event()
    watcher_stop = threading.Event()

    def _watch() -> None:
        consecutive = 0
        while not watcher_stop.is_set() and not interrupt.is_set():
            frame = stream.read(timeout=0.1)
            if frame is None:
                continue
            if rms(frame) > INTERRUPT_RMS_THRESHOLD:
                consecutive += 1
                if consecutive >= INTERRUPT_CONSECUTIVE_FRAMES:
                    log.info("TTS interrupted by mic voice activity.")
                    interrupt.set()
                    return
            else:
                consecutive = 0

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    try:
        if streaming:
            speak_stream(chunks, interrupt_event=interrupt)
        else:
            speak(next(chunks), interrupt_event=interrupt)
    finally:
        watcher_stop.set()
        watcher.join(timeout=0.3)

    return interrupt.is_set()


def _stream_response_interruptible(
    runtime: JarvisRuntime,
    stream: MicStream,
    user_text: str,
) -> tuple[str, bool]:
    """Generate and speak concurrently, returning the complete persisted reply."""
    fragments: queue.Queue[str | None] = queue.Queue()
    reply_parts: list[str] = []

    def _produce() -> None:
        print("Jarvis: ", end="", flush=True)
        try:
            for fragment in runtime.respond_streaming(user_text):
                reply_parts.append(fragment)
                print(fragment, end="", flush=True)
                fragments.put(fragment)
        except Exception:  # noqa: BLE001
            log.exception("Agent respond_streaming() failed")
            fallback = "Something went wrong, sir."
            reply_parts.append(fallback)
            print(fallback, end="", flush=True)
            fragments.put(fallback)
        finally:
            print()
            fragments.put(None)

    producer = threading.Thread(target=_produce, daemon=True)
    producer.start()

    def _chunks():
        while True:
            fragment = fragments.get()
            if fragment is None:
                return
            yield fragment

    interrupted = _speak_stream_interruptible(stream, _chunks())
    # Always finish consuming the model stream so history and tool-use state
    # are finalized even when the user interrupts audio playback.
    producer.join()
    return "".join(reply_parts).strip(), interrupted


def _wait_for_wake_with_timers(
    runtime: JarvisRuntime,
    detector: WakeDetector,
    stream: MicStream,
) -> None:
    detector._ensure_loaded()  # noqa: SLF001
    detector.reset()
    log.info("Listening for wake word (with timer drain)...")

    while True:
        frame = stream.read(timeout=1.0)
        if frame is None:
            for t in runtime.pop_due_reminders():
                msg = f"Reminder, sir. {t.message}."
                log.info("Timer firing: id=%s", t.id)
                _speak_interruptible(stream, msg)
            continue

        try:
            scores = detector._model.predict(frame)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            log.exception("openWakeWord predict() raised; skipping frame")
            continue

        for name, score in scores.items():
            if score >= WAKE_THRESHOLD:
                log.info("Wake word fired: %s (score=%.2f)", name, score)
                return


def run_voice_loop(runtime: JarvisRuntime, *, greet: bool = True) -> int:
    """Run JARVIS in always-listening mode until an exit phrase is spoken."""
    detector = WakeDetector()
    transcriber = Transcriber()

    if greet:
        _say(GREETING)
        briefing = runtime.launch_briefing()
        if briefing:
            _say(briefing)

    print("(Voice mode active - say 'hey jarvis' or end a sentence with 'jarvis'.)")

    with MicStream() as stream:
        while True:
            stream.drain()

            t_wake = time.perf_counter()
            try:
                _wait_for_wake_with_timers(runtime, detector, stream)
            except KeyboardInterrupt:
                _say(GOODBYE)
                return 0

            # Snapshot BEFORE the chime so trailing-wake-word audio
            # ("turn on the lights, Jarvis") isn't pushed out of the
            # rolling buffer while the chime is playing.
            preroll_snapshot = stream.get_recent_audio(PREROLL_SECONDS)
            play_chime()

            audio = _capture_utterance(stream, preroll=preroll_snapshot)
            _log_stage("1. wake detected -> transcription start (chime+capture)", time.perf_counter() - t_wake)
            if audio is None or audio.size == 0:
                log.debug("No audio captured around wake word.")
                continue

            t0 = time.perf_counter()
            try:
                raw_text = transcriber.transcribe(audio)
            except Exception:  # noqa: BLE001
                log.exception("Transcription failed")
                _speak_interruptible(stream, "I missed that, sir.")
                continue
            _log_stage("2. STT (Whisper)", time.perf_counter() - t0)

            text = _strip_wake_word(raw_text).strip()

            if text:
                print(f"You: {text}")
                if _is_exit_phrase(text):
                    _speak_interruptible(stream, GOODBYE)
                    return 0
                t0 = time.perf_counter()
                if _streaming_enabled() and runtime.brain_online:
                    _, interrupted = _stream_response_interruptible(runtime, stream, text)
                    _log_stage("3. Claude + streamed TTS", time.perf_counter() - t0)
                else:
                    try:
                        reply = runtime.respond(text)
                    except Exception:  # noqa: BLE001
                        log.exception("Agent respond() failed")
                        reply = "Something went wrong, sir."
                    _log_stage("3. Claude (tool loop)", time.perf_counter() - t0)
                    interrupted = _speak_interruptible(stream, reply)
            else:
                # Wake word only - no command stripped out.  Prompt briefly
                # then fall into the follow-up window so the user can speak.
                log.debug("Wake word fired but no command detected; prompting.")
                interrupted = _speak_interruptible(stream, "Yes, sir?")
                runtime.mark_no_tools_used()  # keep follow-up window long

            # --- Follow-up window (length depends on what just happened) ----
            # Task replies (used tools) -> short window; chat replies -> long.
            # If user interrupted, treat like an open conversation.
            followup_timeout = (
                FOLLOWUP_CONVERSATIONAL_S
                if (interrupted or not runtime.last_used_tools)
                else FOLLOWUP_TASK_S
            )

            while True:
                stream.drain()
                t0 = time.perf_counter()
                try:
                    follow = record_until_silence(
                        stream,
                        max_seconds=MAX_UTTERANCE_S,
                        start_timeout=followup_timeout,
                    )
                except KeyboardInterrupt:
                    _say(GOODBYE)
                    return 0
                _log_stage("1. follow-up capture (start-wait + record)", time.perf_counter() - t0)

                if follow is None or follow.size == 0:
                    break

                t0 = time.perf_counter()
                try:
                    follow_text = transcriber.transcribe(follow)
                except Exception:  # noqa: BLE001
                    log.exception("Follow-up transcription failed")
                    break
                _log_stage("2. STT (Whisper)", time.perf_counter() - t0)

                follow_text = _strip_wake_word(follow_text).strip()
                if not follow_text:
                    break

                print(f"You: {follow_text}")
                if _is_exit_phrase(follow_text):
                    _speak_interruptible(stream, GOODBYE)
                    return 0

                t0 = time.perf_counter()
                if _streaming_enabled() and runtime.brain_online:
                    _, follow_interrupted = _stream_response_interruptible(
                        runtime, stream, follow_text,
                    )
                    _log_stage("3. Claude + streamed TTS", time.perf_counter() - t0)
                else:
                    try:
                        reply = runtime.respond(follow_text)
                    except Exception:  # noqa: BLE001
                        log.exception("Agent respond() failed")
                        reply = "Something went wrong, sir."
                    _log_stage("3. Claude (tool loop)", time.perf_counter() - t0)
                    follow_interrupted = _speak_interruptible(stream, reply)
                followup_timeout = (
                    FOLLOWUP_CONVERSATIONAL_S
                    if (follow_interrupted or not runtime.last_used_tools)
                    else FOLLOWUP_TASK_S
                )
