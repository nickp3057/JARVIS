"""The profile is parsed once and re-read only when the file changes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import memory


class MemoryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "profile.json"
        self.path.write_text(json.dumps({"facts": [{"id": "a", "content": "likes tea"}]}), encoding="utf-8")
        for p in (patch.object(memory, "_MEMORY_FILE", self.path), patch.object(memory, "_cache", None)):
            p.start()
            self.addCleanup(p.stop)

    def test_unchanged_file_is_not_reparsed(self) -> None:
        memory._load()
        with patch.object(memory.json, "load", side_effect=AssertionError("re-read")):
            self.assertEqual(memory._load()["facts"][0]["content"], "likes tea")

    def test_callers_get_their_own_copy(self) -> None:
        memory._load()["facts"].clear()
        self.assertEqual(len(memory._load()["facts"]), 1)

    def test_an_outside_edit_is_picked_up(self) -> None:
        memory._load()
        self.path.write_text(json.dumps({"facts": [{"id": "b", "content": "likes coffee, now"}]}), encoding="utf-8")
        stat = self.path.stat()
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.assertEqual(memory._load()["facts"][0]["content"], "likes coffee, now")

    def test_save_then_load_sees_the_write(self) -> None:
        data = memory._load()
        data["facts"].append({"id": "c", "content": "plays guitar"})
        with patch("brain.knowledge_vault.sync_profile_note"):
            self.assertTrue(memory._save(data))
        self.assertEqual([f["id"] for f in memory._load()["facts"]], ["a", "c"])

    def test_missing_file_is_empty(self) -> None:
        self.path.unlink()
        self.assertEqual(memory._load(), {"facts": []})


if __name__ == "__main__":
    unittest.main()
