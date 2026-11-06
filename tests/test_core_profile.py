"""The core profile: imports distilled into what JARVIS always knows."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from brain import knowledge_vault, memory, memory_extractor


class CoreProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.vault = Path(tmp.name)
        patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": str(self.vault),
                                "OBSIDIAN_INDEX_FOLDERS": "Jarvis Memory"}).start()
        self.facts: list[dict] = []
        patch.object(memory, "list_facts", side_effect=lambda: self.facts).start()
        patch.object(memory_extractor, "list_facts", side_effect=lambda: self.facts).start()
        self.facts_file = self.vault / "profile.json"
        self.facts_file.write_text("{}")
        patch.object(memory, "_MEMORY_FILE", self.facts_file).start()
        self.addCleanup(patch.stopall)

    def _client(self, text: str) -> MagicMock:
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
        return client

    def test_nothing_without_imports(self) -> None:
        self.assertFalse(knowledge_vault.core_profile_stale())
        self.assertFalse(memory_extractor.refresh_core_profile(self._client("x")))
        self.assertEqual(memory._core_profile_block(refresh=True), "")

    def test_import_is_distilled_and_shown_every_turn(self) -> None:
        knowledge_vault.import_note("From Claude", "## People\nNick's sister is Kendra, born April 15, 2008.")
        self.assertTrue(knowledge_vault.core_profile_stale())
        client = self._client("## People\n- Sister: Kendra (b. Apr 15 2008)")
        with patch.object(memory_extractor, "_core_model", return_value="claude-sonnet-5-5"):
            self.assertTrue(memory_extractor.refresh_core_profile(client))
        sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Kendra", sent)
        self.assertFalse(knowledge_vault.core_profile_stale())
        # Shown on an unrelated question, unlike a searched note.
        block = memory.render_memory_block("what's the weather")
        self.assertIn("Core profile", block)
        self.assertIn("### People", block)
        self.assertIn("Sister: Kendra", block)
        # And it isn't returned again as a search hit.
        sources = {hit["source"] for hit in knowledge_vault.search_vault("Kendra sister")}
        self.assertNotIn("Jarvis Memory/Core Profile.md", sources)

    def test_newer_import_marks_it_stale_and_a_turn_rebuilds(self) -> None:
        knowledge_vault.import_note("A", "Nick drives a Tesla.")
        knowledge_vault.write_core_profile("- Drives a Tesla")
        self.assertFalse(knowledge_vault.core_profile_stale())
        old = time.time() - 60
        os.utime(knowledge_vault.core_profile_path(), (old, old))
        self.assertTrue(knowledge_vault.core_profile_stale())
        os.utime(self.facts_file, (old - 60, old - 60))
        with patch.object(memory_extractor, "refresh_core_profile_async") as rebuild:
            self.assertIn("Drives a Tesla", memory.render_memory_block("hi"))
            rebuild.assert_called_once()
            memory.render_memory_block()  # the UI's no-query view never triggers a rebuild
            rebuild.assert_called_once()

    def test_profile_facts_are_merged_in_with_dates(self) -> None:
        self.facts = [{"id": "a", "category": "work", "content": "Shift Lead at Chick-fil-A.",
                       "added_at": "2026-09-01T00:00:00+00:00"}]
        client = self._client("## Work and money\n- Assistant Director, Chick-fil-A")
        self.assertTrue(memory_extractor.refresh_core_profile(client))  # facts alone are enough
        sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("(2026-09-01) [work] Shift Lead", sent)
        self.assertIn("newer date wins", client.messages.create.call_args.kwargs["system"])

    def test_new_facts_rebuild_at_most_once_a_day(self) -> None:
        knowledge_vault.write_core_profile("- Drives a Tesla")
        now = time.time()
        os.utime(self.facts_file, (now, now))  # learned something after the build...
        hour_ago = now - 3600
        os.utime(knowledge_vault.core_profile_path(), (hour_ago, hour_ago))
        self.assertFalse(memory_extractor.core_profile_due())  # ...but it's only an hour old
        day_ago = now - 25 * 3600
        os.utime(knowledge_vault.core_profile_path(), (day_ago, day_ago))
        self.assertTrue(memory_extractor.core_profile_due())
        os.utime(self.facts_file, (day_ago - 60, day_ago - 60))
        self.assertFalse(memory_extractor.core_profile_due())  # nothing new since

    def test_overlong_profile_is_cut_at_a_line(self) -> None:
        knowledge_vault.import_note("A", "x")
        body = "\n".join(f"- fact number {i} " + "y" * 60 for i in range(200))
        self.assertTrue(memory_extractor.refresh_core_profile(self._client(body)))
        saved = knowledge_vault.read_core_profile()
        self.assertLessEqual(len(saved), 6000)
        self.assertTrue(saved.endswith("y"))  # a whole bullet, not half of one

    def test_failed_call_keeps_the_old_profile(self) -> None:
        knowledge_vault.import_note("A", "Nick drives a Tesla.")
        knowledge_vault.write_core_profile("- Drives a Tesla")
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("overloaded")
        self.assertFalse(memory_extractor.refresh_core_profile(client))
        self.assertEqual(knowledge_vault.read_core_profile(), "- Drives a Tesla")


if __name__ == "__main__":
    unittest.main()
