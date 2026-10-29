"""Keep tests off the real memory/tool_health.json.

``tools.execute_tool`` records every call's health, so any test that runs a
tool would otherwise write Nick's live file. Tests that need the file patch
``tool_health._FILE`` themselves (``tests/test_tool_health.py``).
"""
import tempfile
from pathlib import Path

from brain import tool_health

_TMP = tempfile.mkdtemp(prefix="jarvis-tests-")
tool_health._FILE = Path(_TMP) / "tool_health.json"
