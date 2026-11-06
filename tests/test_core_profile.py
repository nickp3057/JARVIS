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
        patch.object(memory, "list_facts", return_value=[]).start()
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
        with patch.object(memory_extractor, "refresh_core_profile_async") as rebuild:
            self.assertIn("Drives a Tesla", memory.render_memory_block("hi"))
            rebuild.assert_called_once()
            memory.render_memory_block()  # the UI's no-query view never triggers a rebuild
            rebuild.assert_called_once()

    def test_failed_call_keeps_the_old_profile(self) -> None:
        knowledge_vault.import_note("A", "Nick drives a Tesla.")
        knowledge_vault.write_core_profile("- Drives a Tesla")
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("overloaded")
        self.assertFalse(memory_extractor.refresh_core_profile(client))
        self.assertEqual(knowledge_vault.read_core_profile(), "- Drives a Tesla")


if __name__ == "__main__":
    unittest.main()
