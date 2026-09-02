"""Automatic post-turn memory extraction."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import brain.habits as habits
import brain.memory as memory
import brain.memory_proposals as proposals
from brain import memory_extractor as extractor


class FakeClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"facts": [], "episode": ""}
        self.error = error
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        block = SimpleNamespace(type="tool_use", name="record_memories", input=self.payload)
        return SimpleNamespace(content=[block])


class MemoryExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        base = Path(self.tempdir.name)
        self.old = (memory._MEMORY_FILE, proposals._PROPOSAL_FILE, habits._HABITS_FILE)
        memory._MEMORY_FILE = base / "profile.json"
        proposals._PROPOSAL_FILE = base / "proposals.json"
        habits._HABITS_FILE = base / "habits.json"
        habits._cache_key = None
        self.env = patch.dict(os.environ, {
            "OBSIDIAN_VAULT_PATH": str(base / "vault"),
            "MEMORY_AUTO_EXTRACT": "true",
            "MEMORY_AUTO_CONFIDENCE": "0.75",
        })
        self.env.start()
        self.events = []
        self.listener = lambda title, detail: self.events.append((title, detail))
        extractor.add_listener(self.listener)

    def tearDown(self) -> None:
        extractor.remove_listener(self.listener)
        self.env.stop()
        memory._MEMORY_FILE, proposals._PROPOSAL_FILE, habits._HABITS_FILE = self.old
        habits._cache_key = None
        self.tempdir.cleanup()

    def _facts(self):
        return memory.list_facts()

    def test_explicit_fact_is_saved_and_announced(self) -> None:
        client = FakeClient({
            "facts": [{
                "category": "person", "content": "Nick's brother is Jake.",
                "key": "person.jake", "kind": "explicit", "confidence": 0.7,
            }],
            "episode": "",
        })
        changes = extractor.process_turn(
            client, "my brother Jake is coming over tonight", "Very good, sir.",
            [("control_device", {"command": "porch light on"})],
        )
        self.assertEqual(len(changes), 1)
        facts = self._facts()
        self.assertEqual(facts[0]["content"], "Nick's brother is Jake.")
        self.assertEqual(facts[0]["source"], "auto:explicit")
        self.assertGreaterEqual(facts[0]["confidence"], 0.9)
        self.assertEqual(facts[0]["key"], "person.jake")
        self.assertEqual(self.events[0][0], "Memory updated")
        # Habit signal recorded regardless of extraction outcome.
        self.assertEqual(len(json.loads(habits._HABITS_FILE.read_text())["turns"]), 1)
        # The model call is structured and sees the existing memories + tools.
        call = client.calls[0]
        self.assertEqual(call["tool_choice"], {"type": "tool", "name": "record_memories"})
        self.assertIn("control_device", call["messages"][0]["content"])

    def test_low_confidence_inference_is_staged_not_saved(self) -> None:
        client = FakeClient({
            "facts": [{
                "category": "preference", "content": "Nick may prefer jazz in the evening.",
                "kind": "inferred", "confidence": 0.5,
            }],
            "episode": "",
        })
        extractor.process_turn(client, "play some jazz please", "Playing jazz, sir.")
        self.assertEqual(self._facts(), [])
        staged = json.loads(proposals._PROPOSAL_FILE.read_text())["proposals"]
        self.assertEqual(staged[0]["status"], "pending")
        self.assertIn("jazz", staged[0]["evidence"])
        self.assertEqual(self.events, [])

    def test_supersedes_revises_existing_fact_in_place(self) -> None:
        memory.remember_fact("preference", "Nick drinks his coffee black.")
        old_id = self._facts()[0]["id"]
        client = FakeClient({
            "facts": [{
                "category": "preference", "content": "Nick now takes his coffee with oat milk.",
                "kind": "explicit", "confidence": 1.0, "supersedes_id": old_id,
            }],
            "episode": "",
        })
        extractor.process_turn(client, "I switched to oat milk in my coffee", "Noted, sir.")
        facts = self._facts()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["id"], old_id)
        self.assertIn("oat milk", facts[0]["content"])
        self.assertEqual(len(facts[0]["revisions"]), 1)
        self.assertIn(old_id, client.calls[0]["messages"][0]["content"])

    def test_episode_lands_in_todays_journal(self) -> None:
        client = FakeClient({"facts": [], "episode": "Added Dune to Radarr for Nick."})
        extractor.process_turn(client, "add dune to radarr", "Done, sir.")
        journal = Path(self.tempdir.name) / "vault" / "Jarvis Memory" / "Journal"
        today = journal / f"{datetime.now().astimezone().strftime('%Y-%m-%d')}.md"
        text = today.read_text(encoding="utf-8")
        self.assertIn("## Log", text)
        self.assertIn("Added Dune to Radarr for Nick.", text)

    def test_trivial_disabled_and_failing_calls_skip_quietly(self) -> None:
        client = FakeClient()
        self.assertEqual(extractor.process_turn(client, "yes", "Done."), [])
        self.assertEqual(extractor.process_turn(None, "my brother is Jake", "Noted."), [])
        with patch.dict(os.environ, {"MEMORY_AUTO_EXTRACT": "false"}):
            self.assertEqual(extractor.process_turn(client, "my brother is Jake", "Noted."), [])
        self.assertEqual(client.calls, [])
        broken = FakeClient(error=RuntimeError("api down"))
        self.assertEqual(extractor.process_turn(broken, "my brother is Jake", "Noted."), [])
        self.assertEqual(len(json.loads(habits._HABITS_FILE.read_text())["turns"]), 4)

    def test_garbage_payload_is_ignored(self) -> None:
        self.assertEqual(extractor.apply_extraction({"facts": ["nope", {"content": ""}, 5]}), [])
        self.assertEqual(extractor.apply_extraction("not a dict"), [])  # type: ignore[arg-type]
        self.assertEqual(self._facts(), [])

    def test_background_worker_processes_queued_turns(self) -> None:
        client = FakeClient({
            "facts": [{"category": "goal", "content": "Nick wants to finish JARVIS this year.",
                       "kind": "explicit", "confidence": 1.0}],
            "episode": "",
        })
        self.assertTrue(extractor.observe_turn(client, "I want to finish JARVIS this year", "Splendid."))
        extractor._queue.join()
        self.assertEqual(self._facts()[0]["category"], "goal")


if __name__ == "__main__":
    unittest.main()
