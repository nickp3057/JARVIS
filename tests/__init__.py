"""Keep tests off the real memory/tool_health.json (and alert_repeats.json, usage.db, watches.json).

``tools.execute_tool`` records every call's health, so any test that runs a
tool would otherwise write Nick's live file. Tests that need the file patch
``tool_health._FILE`` themselves (``tests/test_tool_health.py``).
"""
import tempfile
from pathlib import Path

from brain import auto_heal, event_bus, lessons, memory_extractor, movie_exit, tool_health, usage, watches

_TMP = tempfile.mkdtemp(prefix="jarvis-tests-")
tool_health._FILE = Path(_TMP) / "tool_health.json"
event_bus._REPEATS_FILE = Path(_TMP) / "alert_repeats.json"  # and off memory/alert_repeats.json
auto_heal._FILE = Path(_TMP) / "auto_repairs.json"  # and off memory/auto_repairs.json
lessons._FILE = Path(_TMP) / "lessons.json"  # and off memory/lessons.json
watches._FILE = Path(_TMP) / "watches.json"  # and off memory/watches.json
movie_exit._DIR = Path(_TMP)  # a faked Movie scene never snapshots into memory/movie_before*.json
movie_exit._SNAPSHOTS = False  # ...nor asks the real HA for one (tests/test_movie_exit.py turns it on)
usage._DB_PATH = Path(_TMP) / "usage.db"  # every faked model response would otherwise hit the real ledger
memory_extractor._core_client = lambda: None  # an import never reaches the real API to distil the core profile

# The room voice streams a reply only when STREAMING_TTS is on; the dev .env
# may turn it on, so tests that fake runtime.respond stay on the whole-reply
# path unless they ask for streaming themselves.
import os  # noqa: E402

os.environ["STREAMING_TTS"] = "false"
# Likewise TTS_PROVIDER: ElevenLabs stays the voice under test unless a test
# picks Piper itself (tests/test_piper_voice.py).
os.environ["TTS_PROVIDER"] = "elevenlabs"
# Autopilot (brain/auto_heal.py) would run a staged fix for real from any test
# that stages one (a download retry against the dev .env's Radarr, say).
# Off for every test; tests/test_auto_heal.py turns it on with faked fixes.
os.environ["AUTO_REPAIR_ENABLED"] = "false"
