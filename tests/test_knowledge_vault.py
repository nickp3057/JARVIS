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

    def test_update_fact_preserves_id_and_revision_history(self) -> None:
        memory.remember_fact("preferences", "Favorite color is red.")
        fact_id = memory.list_facts()[0]["id"]
        result = memory.update_fact(fact_id, "Favorite color is cyan.", "identity")
        updated = memory.list_facts()[0]
        self.assertIn("Updated", result)
        self.assertEqual(updated["id"], fact_id)
        self.assertEqual(updated["category"], "identity")
        self.assertEqual(updated["content"], "Favorite color is cyan.")
        self.assertEqual(updated["revisions"][0]["content"], "Favorite color is red.")
        self.assertIn("updated_at", updated)
        self.assertIn("Favorite color is cyan.", (
            self.root / "Jarvis Memory" / "Profile.md"
        ).read_text(encoding="utf-8"))

    def test_keyed_fact_automatically_revises_conflicting_value(self) -> None:
        memory.remember_fact(
            "preferences",
            "Favorite color is red.",
            key=" Preferences.Favorite Color ",
            confidence=0.7,
            source="inferred",
        )
        original = memory.list_facts()[0]
        result = memory.remember_fact(
            "identity",
            "Favorite color is cyan.",
            key="preferences.favorite_color",
            confidence=1,
            source="Nick said so",
        )
        facts = memory.list_facts()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["id"], original["id"])
        self.assertIn("Revised conflicting memory", result)
        self.assertEqual(facts[0]["category"], "identity")
        self.assertEqual(facts[0]["content"], "Favorite color is cyan.")
        self.assertEqual(facts[0]["confidence"], 1.0)
        self.assertEqual(facts[0]["source"], "Nick said so")
        self.assertEqual(facts[0]["revisions"][0]["confidence"], 0.7)

    def test_memory_metadata_is_normalized_rendered_and_mirrored(self) -> None:
        memory.remember_fact(
            "projects",
            "Building JARVIS.",
            key=" Projects/JARVIS Status ",
            confidence=4,
            source=" imported\nnotes ",
        )
        fact = memory.list_facts()[0]
        self.assertEqual(fact["key"], "projects_jarvis_status")
        self.assertEqual(fact["confidence"], 1.0)
        self.assertEqual(fact["source"], "imported notes")
        listing = memory.describe_facts(query="JARVIS")
        self.assertIn("key=projects_jarvis_status", listing)
        self.assertIn("source=imported notes", listing)
        note = (self.root / "Jarvis Memory" / "Profile.md").read_text(encoding="utf-8")
        self.assertIn("confidence=1.0", note)
        self.assertIn("source=imported notes", note)

    def test_update_fact_validates_and_bounds_revision_history(self) -> None:
        memory.remember_fact("general", "Version 0")
        fact_id = memory.list_facts()[0]["id"]
        for number in range(1, 23):
            memory.update_fact(fact_id, f"Version {number}")
        updated = memory.list_facts()[0]
        self.assertEqual(len(updated["revisions"]), 20)
        self.assertEqual(updated["revisions"][0]["content"], "Version 2")
        self.assertIn("missing fact id", memory.update_fact("", "value"))
        self.assertIn("empty fact", memory.update_fact(fact_id, ""))
        self.assertIn("No fact found", memory.update_fact("missing", "value"))

    def test_list_memory_facts_exposes_ids_and_filters(self) -> None:
        memory.remember_fact("people", "Morgan is a lab partner.")
        memory.remember_fact("projects", "Build JARVIS.")
        fact_id = memory.list_facts()[0]["id"]
        listing = memory.describe_facts(category="people")
        self.assertIn(fact_id, listing)
        self.assertIn("Morgan", listing)
        self.assertNotIn("Build JARVIS", listing)
        self.assertIn("No remembered facts", memory.describe_facts(query="missing"))

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
        self.assertIn("update_fact", names)
        self.assertIn("list_memory_facts", names)
        self.assertIn("missing 'query'", execute_tool("search_memory", {}))

    def test_typed_profile_record_uses_stable_key_and_revision_history(self) -> None:
        first = memory.remember_profile_record(
            "project", "JARVIS", "Desktop assistant prototype", confidence=0.8,
        )
        second = memory.remember_profile_record(
            "project", "JARVIS", "Daily-driver home assistant", confidence=1.0,
        )
        self.assertIn("Remembered", first)
        self.assertIn("Revised conflicting memory", second)
        facts = memory.list_facts()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["key"], "project.jarvis")
        self.assertEqual(facts[0]["content"], "JARVIS: Daily-driver home assistant")
        self.assertEqual(facts[0]["revisions"][0]["content"], "JARVIS: Desktop assistant prototype")

    def test_typed_profile_record_validates_shape_and_tool_registration(self) -> None:
        self.assertIn("record type", memory.remember_profile_record("secret", "x", "y"))
        self.assertIn("requires", memory.remember_profile_record("goal", "", "Ship it"))
        names = {spec.get("name") for spec in get_tool_specs()}
        self.assertIn("remember_profile_record", names)
        result = execute_tool("remember_profile_record", {
            "record_type": "routine", "name": "Morning", "detail": "Checks the briefing.",
        })
        self.assertIn("Remembered", result)
        self.assertEqual(memory.list_facts()[0]["key"], "routine.morning")

    def test_unkeyed_contradiction_detection_is_advisory_and_category_scoped(self) -> None:
        memory.remember_fact("preference", "Nick prefers blue ambient lighting.")
        memory.remember_fact("location", "Nick lives in Herriman Utah.")
        before = json.loads(memory._MEMORY_FILE.read_text(encoding="utf-8"))
        candidates = memory.find_contradiction_candidates(
            "preference", "Nick prefers red ambient lighting.",
        )
        self.assertEqual(len(candidates), 1)
        self.assertIn("blue", candidates[0]["content"])
        after = json.loads(memory._MEMORY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(before, after)

    def test_keyed_exact_and_unrelated_facts_are_not_contradiction_candidates(self) -> None:
        memory.remember_fact("preference", "Nick prefers blue lighting.", key="preference.lighting")
        memory.remember_fact("preference", "Nick likes chemistry labs.")
        self.assertEqual(
            memory.find_contradiction_candidates("preference", "Nick prefers red lighting."),
            [],
        )
        names = {spec.get("name") for spec in get_tool_specs()}
        self.assertIn("find_memory_contradictions", names)

    def test_confidence_reinforcement_decay_clamps_and_audits(self) -> None:
        memory.remember_fact("general", "A tentative fact.", confidence=0.5)
        fact_id = memory.list_facts()[0]["id"]
        self.assertIn("0.50 → 0.70", memory.adjust_fact_confidence(fact_id, "reinforce", 0.2))
        self.assertIn("0.70 → 0.00", memory.adjust_fact_confidence(fact_id, "decay", 1.0))
        fact = memory.list_facts()[0]
        self.assertEqual(fact["confidence"], 0.0)
        self.assertIn("last_reinforced_at", fact)
        self.assertIn("last_decayed_at", fact)
        self.assertIn("minimum", memory.adjust_fact_confidence(fact_id, "decay"))

    def test_confidence_adjustment_validates_and_tool_is_registered(self) -> None:
        self.assertIn("signal", memory.adjust_fact_confidence("id", "boost"))
        self.assertIn("amount", memory.adjust_fact_confidence("id", "decay", 0))
        self.assertIn("No fact", memory.adjust_fact_confidence("missing", "decay"))
        names = {spec.get("name") for spec in get_tool_specs()}
        self.assertIn("adjust_fact_confidence", names)


if __name__ == "__main__":
    unittest.main()
