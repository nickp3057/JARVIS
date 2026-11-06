"""No actions before release, receipt-safe retries, and independent speech."""
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.server as server
from app.companion_voice import SpeechBuffer
from brain import history, voice_trust
from brain.runtime import JarvisRuntime
from speech import remote_voice


class LiveVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(history, "_DB_PATH", Path(self.tmp.name) / "history.db"),
                  patch.dict(os.environ, {"JARVIS_API_TOKEN": "voice-test"}),
                  patch.object(remote_voice, "pcm_stream", side_effect=lambda *args: iter([b"\x01\x00" * 100]))):
            p.start()
            self.addCleanup(p.stop)
        self.session = history.start_new_session()
        self.calls = []
        self.core = JarvisRuntime.__new__(JarvisRuntime)
        self.core._turn_lock = threading.RLock()
        self.core.agent = SimpleNamespace(switch_session=lambda session: None)
        self.completed = threading.Event()

        def stream(text, *, spoken=False):
            self.calls.append((text, spoken, voice_trust.current().verified))
            yield "Hello, "
            yield "sir. "
            history.append_turn(text, "Hello, sir.", self.session)
            self.completed.set()
        self.core.respond_streaming = stream
        runtime_patch = patch.object(server, "_runtime", self.core)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer voice-test"}

    def connect(self, request="voice-one"):
        return self.client.websocket_connect("/api/voice/live", headers=self.headers)

    def start(self, ws, request="voice-one"):
        ws.send_json({"type": "start", "session_id": self.session, "request_id": request})
        self.assertEqual(ws.receive_json()["type"], "ready")

    def collect(self, ws):
        events, audio = [], []
        for _ in range(100):
            item = ws.receive()
            if "bytes" in item:
                audio.append(item["bytes"])
            else:
                import json
                event = json.loads(item["text"])
                events.append(event)
                if event["type"] in {"done", "error"}:
                    return events, audio
        self.fail("Voice stream never finished")

    def test_partials_never_execute_and_release_streams_text_and_audio(self):
        with self.connect() as ws:
            self.start(ws)
            ws.send_json({"type": "partial", "text": "Turn the"})
            self.assertEqual(ws.receive_json(), {"type": "transcript", "text": "Turn the"})
            self.assertEqual(self.calls, [])
            self.assertEqual(history.load_session_messages(self.session), [])
            ws.send_json({"type": "commit", "text": "Turn the lamp on"})
            events, audio = self.collect(ws)
        self.assertEqual([e["type"] for e in events], ["committed", "text", "text", "done"])
        self.assertTrue(audio)
        self.assertEqual(self.calls, [("Turn the lamp on", True, True)])
        self.assertEqual(history.load_session_messages(self.session)[0]["content"], "Turn the lamp on")

    def test_cancel_before_release_has_no_actions_or_history(self):
        with self.connect() as ws:
            self.start(ws)
            ws.send_json({"type": "partial", "text": "Delete something"})
            ws.receive_json()
            ws.send_json({"type": "cancel"})
        self.assertEqual(self.calls, [])
        self.assertEqual(history.load_session_messages(self.session), [])

    def test_retry_of_committed_request_does_not_repeat_tools(self):
        for _ in range(2):
            with self.connect() as ws:
                self.start(ws)
                ws.send_json({"type": "commit", "text": "Hello"})
                events, _ = self.collect(ws)
                self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(history.load_session_messages(self.session)), 2)

    def test_disconnect_after_commit_still_saves_receipt(self):
        release = threading.Event()
        def stream(text, *, spoken=False):
            self.calls.append(text)
            yield "Working. "
            release.wait(3)
            history.append_turn(text, "Done.", self.session)
            self.completed.set()
        self.core.respond_streaming = stream
        try:
            with self.connect() as ws:
                self.start(ws)
                ws.send_json({"type": "commit", "text": "Do it"})
                self.assertEqual(ws.receive_json()["type"], "committed")
                self.assertEqual(ws.receive_json()["type"], "text")
        finally:
            release.set()
        self.assertTrue(self.completed.wait(3))
        # Acquire the same turn lock: append_turn has committed the receipt.
        self.assertEqual(self.core.respond_in_session(self.session, "Do it", "voice-one"), "Done.")
        self.assertEqual(len(self.calls), 1)

    def test_speech_failure_keeps_text_and_history(self):
        with patch.object(remote_voice, "pcm_stream", side_effect=RuntimeError("offline")):
            with self.connect() as ws:
                self.start(ws)
                ws.send_json({"type": "commit", "text": "Hello"})
                events, audio = self.collect(ws)
        self.assertIn("audio_error", [e["type"] for e in events])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(audio, [])
        self.assertEqual(len(history.load_session_messages(self.session)), 2)

    def test_authentication_is_required(self):
        with self.assertRaises(WebSocketDisconnect) as denied:
            with self.client.websocket_connect("/api/voice/live"):
                pass
        self.assertEqual(denied.exception.code, 1008)
        self.assertEqual(self.calls, [])

    def test_invalid_or_binary_capture_is_refused(self):
        for invalid in ({"type": "commit", "text": " "}, {"type": "partial", "text": "x" * 8001}):
            with self.subTest(invalid=invalid["type"]), self.connect() as ws:
                self.start(ws)
                ws.send_json(invalid)
                self.assertEqual(ws.receive_json()["type"], "error")
        with self.connect() as ws:
            self.start(ws)
            ws.send_bytes(b"not a transcript")
            self.assertEqual(ws.receive_json()["type"], "error")
        self.assertEqual(self.calls, [])

    def test_status_markers_split_across_fragments_are_not_spoken(self):
        buffer = SpeechBuffer()
        self.assertEqual(buffer.feed("\x1dChecking"), [])
        self.assertEqual(buffer.feed(" the NAS\x1dHello, sir. "), ["Hello, sir."])
        self.assertEqual(buffer.feed("\x1eDone"), [])
        self.assertEqual(buffer.finish(), "Done")


class SpeechTransportTests(unittest.TestCase):
    def test_elevenlabs_pcm_is_incremental_and_frame_aligned(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = iter([b"\x01", b"\x00\x02", b"\x00"])
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test", "ELEVENLABS_VOICE_ID": "voice"}), \
             patch.object(remote_voice.requests, "post", return_value=response) as post:
            chunks = list(remote_voice.pcm_stream("Hello.", threading.Event()))
        self.assertEqual(chunks, [b"\x01\x00", b"\x02\x00"])
        self.assertTrue(post.call_args.kwargs["stream"])

    def test_partial_provider_failure_never_replays_the_sentence(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        def broken(**kwargs):
            yield b"\x01\x00"
            raise remote_voice.requests.ConnectionError("lost")
        response.iter_content.side_effect = broken
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test", "ELEVENLABS_VOICE_ID": "voice"}), \
             patch.object(remote_voice.requests, "post", return_value=response), \
             patch.object(remote_voice, "_piper") as piper:
            with self.assertRaises(RuntimeError):
                list(remote_voice.pcm_stream("Hello.", threading.Event()))
            piper.assert_not_called()
