"""Background voice loop runner for the JARVIS UI.

Mirrors ``speech.voice_loop`` but inside a QThread, emitting Qt signals
for state changes and transcripts so the orb and chat widget can react.

Smart follow-up: closes quickly after a tool-driven reply, stays open
after conversational replies. Reads runtime tool-use state.

Interruption: while JARVIS is speaking, watches the mic for deliberate
voice activity above a high threshold; if detected, kills playback and
goes straight back to listening.
"""

from __future__ import annotations

import os
import re
import threading
import time

import numpy as np

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from brain.runtime import JarvisRuntime
from jarvis_logger import get_logger
from speech.audio import MicStream, record_until_silence, rms
from speech.chime import play_chime
from speech.stt import Transcriber
from speech.tts import speak
from speech.wake import WAKE_THRESHOLD, WakeDetector

log = get_logger(__name__)


_EXIT_PHRASES = {
    "exit", "quit", "bye", "goodbye",
    "shut down", "shutdown",
    "go to sleep", "that's all", "thats all",
}

FOLLOWUP_CONVERSATIONAL_S = float(os.getenv("FOLLOWUP_TIMEOUT_S", "10.0"))
FOLLOWUP_TASK_S = float(os.getenv("FOLLOWUP_TASK_S", "2.5"))
MAX_UTTERANCE_S = float(os.getenv("MAX_UTTERANCE_S", "20.0"))
PREROLL_SECONDS = float(os.getenv("PREROLL_SECONDS", "4.0"))
POSTROLL_START_TIMEOUT_S = float(os.getenv("POSTROLL_START_TIMEOUT_S", "1.5"))
POSTROLL_MAX_S = float(os.getenv("POSTROLL_MAX_S", "10.0"))

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


class VoiceRunner(QThread):
    """Runs the voice-mode loop in the background and emits Qt signals."""

    state_idle = pyqtSignal()
    state_listening = pyqtSignal()
    state_thinking = pyqtSignal()
    state_speaking = pyqtSignal()

    user_heard = pyqtSignal(str)
    jarvis_replied = pyqtSignal(str)

    # Emits a normalized [0, 1] RMS level so the orb can react to audio.
    # Driven by the WAV envelope while JARVIS speaks, and by the mic while
    # listening for the wake word.
    rms_level = pyqtSignal(float)

    notice = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, runtime: JarvisRuntime, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._stop_requested = False
        self._muted = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def set_muted(self, muted: bool) -> None:
        """When muted, voice replies are skipped (text still shown in chat)."""
        self._muted = muted

    # --- Loop body ---------------------------------------------------------

    def run(self) -> None:  # noqa: C901
        if not self._runtime.brain_online:
            self.notice.emit("Brain offline; voice mode requires Claude.")
            self.stopped.emit()
            return

        try:
            detector = WakeDetector()
            transcriber = Transcriber()
        except Exception as exc:  # noqa: BLE001
            log.exception("Voice runner init failed")
            self.notice.emit(f"Voice init failed: {exc}")
            self.stopped.emit()
            return

        self.state_idle.emit()

        try:
            with MicStream() as stream:
                while not self._stop_requested:
                    stream.drain()
                    self.state_idle.emit()

                    self._wait_for_wake(detector, stream)
                    if self._stop_requested:
                        break

                    # Snapshot rolling buffer BEFORE the chime so trailing
                    # wake-word audio isn't pushed out of the window.
                    preroll_snapshot = stream.get_recent_audio(PREROLL_SECONDS)
                    self.state_listening.emit()
                    play_chime()

                    audio = self._capture_utterance(stream, preroll=preroll_snapshot)
                    if audio is None or audio.size == 0:
                        continue

                    self.state_thinking.emit()
                    raw = self._transcribe_safe(transcriber, audio)
                    text = _strip_wake_word(raw).strip()

                    interrupted = False
                    if text:
                        self.user_heard.emit(text)
                        if _is_exit_phrase(text):
                            self._speak_interruptible(stream, "Shutting down. See you, sir.")
                            break
                        reply = self._agent_respond(text)
                        interrupted = self._respond_and_speak(stream, reply)
                    else:
                        # Wake word only - prompt and fall into follow-up window.
                        self._runtime.mark_no_tools_used()
                        interrupted = self._respond_and_speak(stream, "Yes, sir?")

                    # Follow-up: short after tools, long after chat / interrupt.
                    followup_timeout = (
                        FOLLOWUP_CONVERSATIONAL_S
                        if (interrupted or not self._runtime.last_used_tools)
                        else FOLLOWUP_TASK_S
                    )

                    while not self._stop_requested:
                        stream.drain()
                        self.state_listening.emit()
                        try:
                            follow = record_until_silence(
                                stream,
                                max_seconds=MAX_UTTERANCE_S,
                                start_timeout=followup_timeout,
                            )
                        except Exception:  # noqa: BLE001
                            log.exception("Follow-up record failed")
                            break

                        if follow is None or follow.size == 0:
                            break

                        self.state_thinking.emit()
                        follow_text = _strip_wake_word(self._transcribe_safe(transcriber, follow)).strip()
                        if not follow_text:
                            break

                        self.user_heard.emit(follow_text)
                        if _is_exit_phrase(follow_text):
                            self._speak_interruptible(stream, "Shutting down. See you, sir.")
                            self._stop_requested = True
                            break
                        reply = self._agent_respond(follow_text)
                        follow_interrupted = self._respond_and_speak(stream, reply)
                        followup_timeout = (
                            FOLLOWUP_CONVERSATIONAL_S
                            if (follow_interrupted or not self._runtime.last_used_tools)
                            else FOLLOWUP_TASK_S
                        )

        except Exception:  # noqa: BLE001
            log.exception("Voice runner crashed")
            self.notice.emit("Voice loop crashed; check jarvis.log.")

        self.state_idle.emit()
        self.stopped.emit()

    # --- Helpers -----------------------------------------------------------

    def _wait_for_wake(self, detector: WakeDetector, stream: MicStream) -> None:
        detector._ensure_loaded()  # noqa: SLF001
        detector.reset()
        _rms_emit_counter = 0

        while not self._stop_requested:
            frame = stream.read(timeout=0.5)
            if frame is None:
                for t in self._runtime.pop_due_reminders():
                    msg = f"Reminder, sir. {t.message}."
                    self.state_speaking.emit()
                    self.jarvis_replied.emit(msg)
                    self._speak_interruptible(stream, msg)
                    self.state_idle.emit()
                continue

            # Emit mic RMS every ~8 frames (~640ms) so the orb subtly reacts
            # to ambient sound while waiting for the wake word.
            _rms_emit_counter += 1
            if _rms_emit_counter >= 8:
                _rms_emit_counter = 0
                mic_rms = rms(frame) / 3000.0  # normalize to ~0-1 range
                self.rms_level.emit(min(1.0, mic_rms))

            try:
                scores = detector._model.predict(frame)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                log.exception("Wake predict failed")
                continue
            for _, score in scores.items():
                if score >= WAKE_THRESHOLD:
                    return

    def _capture_utterance(
        self, stream: MicStream, preroll: np.ndarray | None = None
    ) -> np.ndarray | None:
        if preroll is None:
            preroll = stream.get_recent_audio(PREROLL_SECONDS)
        stream.drain()
        try:
            postroll = record_until_silence(
                stream,
                max_seconds=POSTROLL_MAX_S,
                start_timeout=POSTROLL_START_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001
            log.exception("Postroll record failed")
            return preroll if preroll is not None and preroll.size > 0 else None

        parts: list[np.ndarray] = []
        if preroll is not None and preroll.size > 0:
            parts.append(preroll)
        if postroll is not None and postroll.size > 0:
            parts.append(postroll)
        if not parts:
            return None
        return np.concatenate(parts)

    def _transcribe_safe(self, transcriber: Transcriber, audio: np.ndarray) -> str:
        try:
            return transcriber.transcribe(audio)
        except Exception:  # noqa: BLE001
            log.exception("Transcription failed")
            return ""

    def _agent_respond(self, text: str) -> str:
        try:
            return self._runtime.respond(text)
        except Exception:  # noqa: BLE001
            log.exception("Agent failed mid-conversation")
            return "Something went wrong, sir."

    def _speak_interruptible(self, stream: MicStream, text: str) -> bool:
        if not text or not text.strip():
            return False

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

        envelope_received = threading.Event()

        def _on_envelope(envelope: list[float], frame_dur: float) -> None:
            """Transition orb to SPEAKING and drive RMS in sync with playback.

            Fires between synthesis and playback so the orb state change and
            audio start are as close as possible.  Uses monotonic clock targets
            instead of accumulating sleep() calls to avoid Windows timer drift.
            """
            envelope_received.set()
            self.state_speaking.emit()

            def _drive() -> None:
                start = time.monotonic()
                for i, level in enumerate(envelope):
                    if interrupt.is_set():
                        break
                    self.rms_level.emit(level)
                    target = start + (i + 1) * frame_dur
                    remaining = target - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                self.rms_level.emit(0.0)
            threading.Thread(target=_drive, daemon=True).start()

        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()
        try:
            speak(text, interrupt_event=interrupt, on_envelope=_on_envelope)
        finally:
            watcher_stop.set()
            watcher.join(timeout=0.3)

        # Piper fallback or very short audio may produce no envelope.
        # Emit SPEAKING now so the orb at least reflects the state briefly.
        if not envelope_received.is_set():
            self.state_speaking.emit()

        return interrupt.is_set()

    def _respond_and_speak(self, stream: MicStream, reply: str) -> bool:
        if not reply:
            self.state_idle.emit()
            return False
        self.jarvis_replied.emit(reply)
        if self._muted:
            self.state_idle.emit()
            return False
        try:
            interrupted = self._speak_interruptible(stream, reply)
        except Exception:  # noqa: BLE001
            log.exception("speak() failed")
            interrupted = False
        self.state_idle.emit()
        return interrupted
