"""Keep tests off the real memory/tool_health.json (and alert_repeats.json).

``tools.execute_tool`` records every call's health, so any test that runs a
tool would otherwise write Nick's live file. Tests that need the file patch
``tool_health._FILE`` themselves (``tests/test_tool_health.py``).
"""
import tempfile
from pathlib import Path

from brain import event_bus, memory_extractor, tool_health

_TMP = tempfile.mkdtemp(prefix="jarvis-tests-")
tool_health._FILE = Path(_TMP) / "tool_health.json"
event_bus._REPEATS_FILE = Path(_TMP) / "alert_repeats.json"  # and off memory/alert_repeats.json
memory_extractor._core_client = lambda: None  # an import never reaches the real API to distil the core profile
