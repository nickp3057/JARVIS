"""Regression coverage for remote turn identity and stream ownership."""
import asyncio
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.server as server
import brain.history as history
from brain.runtime import JarvisRuntime
from integrations import qbittorrent


class CompanionReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(history, "_DB_PATH", Path(self.temp.name) / "history.db")
        self.path_patch.start()
        self.session = history.start_new_session()
        self.calls = []
        self.core = JarvisRuntime.__new__(JarvisRuntime)
        self.core._lock = threading.RLock()
        self.core._turn_lock = threading.RLock()
        self.core.agent = SimpleNamespace(switch_session=lambda session: None)

        def reply(text):
            self.calls.append(text)
            answer = f"Answer {len(self.calls)}"
            history.append_turn(text, answer, self.session)
            return answer
        self.core.respond = reply

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def test_repeated_text_has_distinct_receipts_and_retry_does_not_execute_again(self):
        first = self.core.respond_in_session(self.session, "yes", "request-one")
        second = self.core.respond_in_session(self.session, "yes", "request-two")
        retry = self.core.respond_in_session(self.session, "yes", "request-one")
        self.assertNotEqual(first, second)
        self.assertEqual(first, retry)
        self.assertEqual(len(self.calls), 2)
        messages = history.load_session_messages(self.session, include_timestamps=True)
        self.assertEqual([m["request_id"] for m in messages],
                         ["request-one", "request-one", "request-two", "request-two"])
        self.assertEqual(len({m["id"] for m in messages}), 4)
        self.assertIsNone(history.request_context.get())

    def test_receipt_cannot_be_reused_for_different_input_or_session(self):
        self.core.respond_in_session(self.session, "yes", "one")
        with self.assertRaises(ValueError):
            self.core.respond_in_session(self.session, "no", "one")
        other = history.start_new_session()
        with self.assertRaises(ValueError):
            self.core.respond_in_session(other, "yes", "one")
        self.assertEqual(len(self.calls), 1)

    def test_uncertain_turn_is_not_silently_reexecuted(self):
        history.begin_companion_request("interrupted", self.session, "turn it on")
        with self.assertRaisesRegex(ValueError, "interrupted"):
            self.core.respond_in_session(self.session, "turn it on", "interrupted")
        self.assertEqual(self.calls, [])

    def test_delete_removes_receipt_with_transcript(self):
        self.core.respond_in_session(self.session, "hello", "one")
        self.assertTrue(self.core.delete_companion_session(self.session))
        with sqlite3.connect(history._DB_PATH) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM companion_requests").fetchone()[0], 0)

    def test_stream_finishes_on_same_worker_after_client_disconnect(self):
        release = threading.Event()
        completed = threading.Event()
        thread_ids = []

        def stream(text):
            thread_ids.append(threading.get_ident())
            yield "First "
            release.wait(3)
            thread_ids.append(threading.get_ident())
            yield "second"
            history.append_turn(text, "First second", self.session)
            completed.set()
        self.core.respond_streaming = stream

        async def consume():
            response = server.chat_stream(server.ChatRequest(
                message="hello", session_id=self.session, request_id="stream-one"))
            self.assertEqual(await anext(response.body_iterator), b"First ")
            await response.body_iterator.aclose()
            release.set()
            self.assertTrue(await asyncio.to_thread(completed.wait, 3))

        with patch.object(server, "_runtime", self.core):
            asyncio.run(consume())
        self.assertEqual(len(set(thread_ids)), 1)
        self.assertNotEqual(thread_ids[0], threading.get_ident())
        self.assertEqual(self.core.respond_in_session(self.session, "hello", "stream-one"), "First second")
        self.assertEqual(self.calls, [])

    def test_paused_and_stopped_torrents_remain_available_to_resume(self):
        records = [{"hash": str(i), "name": state, "state": state, "dlspeed": 0, "upspeed": 0}
                   for i, state in enumerate(["pausedDL", "pausedUP", "stoppedDL", "stoppedUP"])]
        with patch.object(qbittorrent, "torrent_info", return_value=(True, records)), \
             patch.object(qbittorrent, "transfer_info", return_value=(True, {})):
            ok, result = qbittorrent.list_transfers()
        self.assertTrue(ok)
        self.assertEqual(len(result["torrents"]), 4)
