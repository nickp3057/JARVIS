import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import brain.knowledge_vault as vault
import brain.memory as memory
from brain.tools import execute_tool, get_tool_specs


class KnowledgeVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "vault"
        self.allowed = self.root / "Notes"
        self.allowed.mkdir(parents=True)
        self.old_memory_file = memory._MEMORY_FILE
        memory._MEMORY_FILE = Path(self.tempdir.name) / "profile.json"
        self.env = patch.dict(os.environ, {
            "OBSIDIAN_VAULT_PATH": str(self.root),
            "OBSIDIAN_INDEX_FOLDERS": "Notes",
            "OBSIDIAN_MAX_RESULTS": "4",
            "OBSIDIAN_MAX_CHARS": "4000",
            "MEMORY_RELEVANT_FACTS": "4",
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        memory._MEMORY_FILE = self.old_memory_file
        self.tempdir.cleanup()

    def test_search_ranks_title_and_returns_provenance(self) -> None:
        (self.allowed / "Project Apollo.md").write_text(
            "# Apollo\nThe launch checklist uses a silver key.\n", encoding="utf-8"
        )
        (self.allowed / "Misc.md").write_text("Apollo was mentioned once.\n", encoding="utf-8")
        results = vault.search_vault("What is the Apollo launch checklist?")
        self.assertEqual(results[0]["source"], "Notes/Project Apollo.md")
        self.assertIn("silver key", results[0]["excerpt"])
        self.assertIn("modified_at", results[0])

    def test_search_only_reads_explicitly_authorized_folders(self) -> None:
        private = self.root / "Private"
        private.mkdir()
        (private / "Secrets.md").write_text("apollo password swordfish", encoding="utf-8")
        (self.allowed / "Safe.md").write_text("apollo public launch plan", encoding="utf-8")
        results = vault.search_vault("apollo password launch")
        self.assertEqual([item["source"] for item in results], ["Notes/Safe.md"])

    def test_folder_traversal_outside_vault_is_ignored(self) -> None:
        outside = Path(self.tempdir.name) / "outside"
        outside.mkdir()
        (outside / "Secret.md").write_text("apollo secret", encoding="utf-8")
        with patch.dict(os.environ, {"OBSIDIAN_INDEX_FOLDERS": "../outside"}):
            self.assertEqual(vault.search_vault("apollo"), [])

    def test_results_obey_count_and_character_limits(self) -> None:
        for number in range(3):
            (self.allowed / f"Note {number}.md").write_text(
                "# Apollo\n" + "apollo details " * 100, encoding="utf-8"
            )
        results = vault.search_vault("apollo", max_results=2, max_chars=240)
        self.assertEqual(len(results), 2)
        self.assertLessEqual(sum(len(item["excerpt"]) for item in results), 240)

    def test_empty_and_stop_word_queries_return_nothing(self) -> None:
        (self.allowed / "Note.md").write_text("anything", encoding="utf-8")
        self.assertEqual(vault.search_vault(""), [])
        self.assertEqual(vault.search_vault("the and of"), [])

    def test_oversized_and_non_utf8_notes_are_skipped(self) -> None:
        (self.allowed / "Huge.md").write_text("apollo " * 1000, encoding="utf-8")
        (self.allowed / "Broken.md").write_bytes(b"apollo \xff")
        with patch.dict(os.environ, {"OBSIDIAN_MAX_FILE_BYTES": "1024"}):
            self.assertEqual(vault.search_vault("apollo"), [])

    def test_remember_and_forget_mirror_profile_atomically(self) -> None:
        result = memory.remember_fact("preferences", "Nick likes quiet mornings.")
        fact_id = json.loads(memory._MEMORY_FILE.read_text(encoding="utf-8"))["facts"][0]["id"]
        note = self.root / "Jarvis Memory" / "Profile.md"
        self.assertIn("Remembered", result)
        self.assertIn("Nick likes quiet mornings.", note.read_text(encoding="utf-8"))
        self.assertIn(f"jarvis:{fact_id}", note.read_text(encoding="utf-8"))
        memory.forget_fact(fact_id)
        self.assertNotIn("quiet mornings", note.read_text(encoding="utf-8"))

    def test_vault_failure_does_not_lose_structured_fact(self) -> None:
        with patch("brain.knowledge_vault.sync_profile_note", side_effect=OSError("offline")):
            result = memory.remember_fact("projects", "Build JARVIS.")
        self.assertIn("Remembered", result)
        self.assertEqual(memory.list_facts()[0]["content"], "Build JARVIS.")

    def test_existing_json_memory_gets_a_first_use_mirror(self) -> None:
        memory._MEMORY_FILE.write_text(json.dumps({"facts": [
            {"id": "legacy", "category": "people", "content": "Sam is a friend."}
        ]}), encoding="utf-8")
        memory.render_memory_block("Who is Sam?")
        note = self.root / "Jarvis Memory" / "Profile.md"
        self.assertIn("Sam is a friend.", note.read_text(encoding="utf-8"))

    def test_query_aware_memory_keeps_relevant_and_recent_facts(self) -> None:
        facts = [
            {"id": "old", "category": "food", "content": "Favorite pizza is mushroom."},
            {"id": "other", "category": "sports", "content": "Likes skiing."},
            {"id": "new", "category": "school", "content": "Has class on Monday."},
        ]
        memory._MEMORY_FILE.write_text(json.dumps({"facts": facts}), encoding="utf-8")
        with patch.dict(os.environ, {"MEMORY_RELEVANT_FACTS": "2"}):
            block = memory.render_memory_block("What pizza do I like?")
        self.assertIn("mushroom", block)
        self.assertLessEqual(block.count("- ("), 2)

    def test_rendered_context_includes_note_source(self) -> None:
        (self.allowed / "School.md").write_text(
            "# Chemistry\nThe lab partner is Morgan.\n", encoding="utf-8"
        )
        block = memory.render_memory_block("Who is my chemistry lab partner?")
        self.assertIn("Source: Notes/School.md", block)
        self.assertIn("Morgan", block)

    def test_search_memory_tool_is_registered_and_validates_query(self) -> None:
        names = {spec.get("name") for spec in get_tool_specs()}
        self.assertIn("search_memory", names)
        self.assertIn("missing 'query'", execute_tool("search_memory", {}))


if __name__ == "__main__":
    unittest.main()
