"""Voice-editable settings: validation, and .env rewrites that keep the file intact."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import settings

ENV = """# JARVIS
ANTHROPIC_API_KEY=sk-secret
HUMOR_PERCENT=30
ADDRESS=sir   # how he's addressed
"""


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = Path(tmp.name) / ".env"
        self.env.write_text(ENV)
        p = patch.object(settings, "_ENV_PATH", self.env)
        p.start()
        self.addCleanup(p.stop)
        environ = patch.dict(os.environ, {}, clear=False)
        environ.start()
        self.addCleanup(environ.stop)

    def test_update_replaces_in_place_and_goes_live(self) -> None:
        reply = settings.update_setting("humor_percent", "65")
        self.assertIn("HUMOR_PERCENT set to 65", reply)
        text = self.env.read_text()
        self.assertIn("HUMOR_PERCENT=65\n", text)
        self.assertIn("ANTHROPIC_API_KEY=sk-secret", text)  # untouched
        self.assertTrue(text.startswith("# JARVIS\n"))       # comments kept, order kept
        self.assertEqual(os.environ["HUMOR_PERCENT"], "65")
        self.assertTrue(self.env.with_suffix(".env.bak").exists())

    def test_new_keys_append_and_spaces_are_quoted(self) -> None:
        settings.update_setting("ADDRESS", 'Mr. "Stark"')
        settings.update_setting("WHISPER_MODEL", "base.en")
        text = self.env.read_text()
        self.assertIn('ADDRESS="Mr. \\"Stark\\""\n', text)
        self.assertTrue(text.endswith("WHISPER_MODEL=base.en\n"))

    def test_validation(self) -> None:
        for key, value, error in (("HUMOR_PERCENT", "101", "<= 100"), ("HUMOR_PERCENT", "-1", ">= 0"),
                                  ("ELEVENLABS_SPEED", "fast", "Could not parse"),
                                  ("ELEVENLABS_SPEED", "nan", "real number"),
                                  ("WHISPER_MODEL", "huge", "must be one of")):
            self.assertIn(error, settings.update_setting(key, value), (key, value))
        self.assertEqual(self.env.read_text(), ENV)  # nothing written for a refusal

    def test_a_line_break_cannot_smuggle_in_another_setting(self) -> None:
        reply = settings.update_setting("ADDRESS", "sir\nANTHROPIC_API_KEY=stolen")
        self.assertIn("single line", reply)
        self.assertNotIn("stolen", self.env.read_text())

    def test_only_whitelisted_keys(self) -> None:
        self.assertIn("isn't an editable setting", settings.update_setting("ANTHROPIC_API_KEY", "x"))
        self.assertIn("not an editable setting", settings.get_setting("ANTHROPIC_API_KEY"))
        os.environ["HUMOR_PERCENT"] = "30"
        self.assertEqual(settings.get_setting("humor_percent"), "HUMOR_PERCENT is currently 30.")
        self.assertIn("HUMOR_PERCENT = 30 [int]", settings.list_settings())

    def test_repair_command_registry_is_admin_only(self) -> None:
        with patch.dict(os.environ, {"SELF_REPAIR_COMMANDS": "restart_home_assistant=echo original"}):
            reply = settings.update_setting("SELF_REPAIR_COMMANDS", "restart_home_assistant=echo injected")
            self.assertIn("isn't an editable setting", reply)
            self.assertEqual(os.environ["SELF_REPAIR_COMMANDS"], "restart_home_assistant=echo original")
            self.assertEqual(self.env.read_text(), ENV)

    def test_bools(self) -> None:
        bool_keys = [k for k, s in settings.SETTINGS_SCHEMA.items() if s["type"] == "bool"]
        if not bool_keys:
            self.skipTest("no bool settings in the schema")
        key = bool_keys[0]
        for raw, stored in (("yes", "true"), ("Off", "false"), ("1", "true")):
            self.assertEqual(settings._coerce_value(key, raw), (True, stored, ""))
        self.assertFalse(settings._coerce_value(key, "maybe")[0])


if __name__ == "__main__":
    unittest.main()
