"""Live phone transcripts -> one committed runtime turn -> text and PCM.

Partials are ephemeral and never call the brain. After commit, disconnecting
stops audio delivery, not an action already running: the runtime still saves
its request receipt and transcript for ordinary history/retry recovery.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import threading
import time

from fastapi import WebSocket, WebSocketDisconnect

from brain import history, voice_trust
from brain.agent import ROUND_BREAK, TOOL_STATUS, stream_round_breaks
from jarvis_logger import get_logger
from speech import remote_voice
from speech.tts import _split_ready_sentences

log = get_logger(__name__)
_WORKERS = threading.BoundedSemaphore(2)
_MAX_TEXT = 8000


class SpeechBuffer:
    """Remove protocol-only markers even when they span text fragments."""

    def __init__(self):
        self.pending = ""
        self.status = False

    def feed(self, text: str) -> list[str]:
        for char in text:
            if char == TOOL_STATUS:
                self.status = not self.status
            elif not self.status:
                self.pending += " " if char == ROUND_BREAK else char
        ready, self.pending = _split_ready_sentences(self.pending)
        # Speak bounded phrases even if the model hasn't supplied punctuation.
        while len(self.pending) > 300:
            end = self.pending.rfind(" ", 0, 300)
            end = end if end > 0 else 300
            ready.append(self.pending[:end].strip())
            self.pending = self.pending[end:].lstrip()
        return ready

    def finish(self) -> str:
        return self.pending.strip()


async def _message(socket: WebSocket, timeout: float) -> dict:
    envelope = await asyncio.wait_for(socket.receive(), timeout=timeout)
    if envelope["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(envelope.get("code", 1000))
    raw = envelope.get("text")
    if not isinstance(raw, str):
        raise ValueError("Voice input uses live transcripts")
    if len(raw) > _MAX_TEXT + 2000:
        raise ValueError("Voice message too long")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected a voice message")
    return data


def _text(data: dict) -> str:
    text = data.get("text", "")
    if not isinstance(text, str) or len(text) > _MAX_TEXT:
        raise ValueError("Invalid transcript")
    return text.strip()


async def serve(socket: WebSocket, runtime_factory) -> None:
    """Called only after the HTTP edge authenticates the companion token."""
    await socket.accept()
    disconnected = threading.Event()
    try:
        start = await _message(socket, 15)
        session_id, request_id = start.get("session_id"), start.get("request_id")
        if start.get("type") != "start" or not isinstance(session_id, str) or not history.session_exists(session_id):
            raise ValueError("Choose an existing conversation before starting voice")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("A request ID is required")
        await socket.send_json({"type": "ready", "sample_rate": remote_voice.SAMPLE_RATE,
                                "encoding": "pcm_s16le", "channels": 1})
        deadline = time.monotonic() + 120
        transcript = ""
        for _ in range(1000):
            if time.monotonic() >= deadline:
                raise ValueError("Voice capture timed out")
            message = await _message(socket, max(0.01, deadline - time.monotonic()))
            kind = message.get("type")
            if kind == "cancel":
                return
            if kind == "partial":
                transcript = _text(message)
                await socket.send_json({"type": "transcript", "text": transcript})
            elif kind == "commit":
                transcript = _text(message)
                if not transcript:
                    raise ValueError("No speech was heard")
                break
            else:
                raise ValueError("Expected a transcript, release, or cancel")
        else:
            raise ValueError("Too many voice updates")
        if not _WORKERS.acquire(blocking=False):
            raise ValueError("Voice is busy. Please try again shortly")
        try:
            await socket.send_json({"type": "committed", "session_id": session_id,
                                    "request_id": request_id, "text": transcript})
        except BaseException:
            _WORKERS.release()
            raise
        loop = asyncio.get_running_loop()
        outgoing: asyncio.Queue = asyncio.Queue(maxsize=128)
        sentences: queue.Queue = queue.Queue(maxsize=32)
        audio_failed = threading.Event()

        def enqueue(value):
            if disconnected.is_set():
                return
            try:
                outgoing.put_nowait(value)
            except asyncio.QueueFull:
                # Drop transport, never block a turn holding the runtime lock.
                disconnected.set()

        def publish(value):
            if not disconnected.is_set():
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(enqueue, value)

        def speech():
            previous = ""
            try:
                while True:
                    sentence = sentences.get()
                    if sentence is None:
                        break
                    if disconnected.is_set() or audio_failed.is_set():
                        continue
                    try:
                        for pcm in remote_voice.pcm_stream(sentence, disconnected, previous):
                            publish(pcm)
                        previous = (previous + " " + sentence)[-1000:]
                    except Exception:
                        audio_failed.set()
                        log.warning("Companion speech unavailable", exc_info=True)
                        publish({"type": "audio_error", "message": "Voice playback is unavailable. The text reply will continue."})
            finally:
                publish({"type": "done"})
                _WORKERS.release()

        def queue_sentence(sentence):
            if sentence and not disconnected.is_set() and not audio_failed.is_set():
                try:
                    sentences.put_nowait(sentence)
                except queue.Full:
                    audio_failed.set()
                    publish({"type": "audio_error", "message": "Voice fell behind. The text reply will continue."})

        def turn():
            stream_round_breaks.set(True)
            buffer = SpeechBuffer()
            threading.Thread(target=speech, name="companion-speech", daemon=True).start()
            try:
                with voice_trust.speaking(voice_trust.OWNER):
                    for fragment in runtime_factory().respond_streaming_in_session(
                        session_id, transcript, request_id=request_id, spoken=True
                    ):
                        publish({"type": "text", "text": fragment})
                        for sentence in buffer.feed(fragment):
                            queue_sentence(sentence)
                queue_sentence(buffer.finish())
            except Exception:
                log.exception("Companion voice turn failed")
                publish({"type": "error", "message": "The voice connection ended. Check Chat history before retrying; HQ may have completed the turn."})
            finally:
                # The speech worker always drains, including after disconnect.
                sentences.put(None)

        threading.Thread(target=turn, name="companion-voice-turn", daemon=True).start()

        async def send():
            while not disconnected.is_set():
                try:
                    item = await asyncio.wait_for(outgoing.get(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                if isinstance(item, bytes):
                    await socket.send_bytes(item)
                else:
                    await socket.send_json(item)
                    if item["type"] in {"done", "error"}:
                        return

        async def watch():
            while True:
                message = await _message(socket, 180)
                if message.get("type") == "cancel":
                    return

        sender, receiver = asyncio.create_task(send()), asyncio.create_task(watch())
        try:
            await asyncio.wait({sender, receiver}, timeout=180, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sender, receiver):
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    except (ValueError, asyncio.TimeoutError, json.JSONDecodeError):
        with contextlib.suppress(Exception):
            await socket.send_json({"type": "error", "message": "Voice could not start or the transcript was invalid. Please try again."})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        disconnected.set()
        with contextlib.suppress(Exception):
            await socket.close()
