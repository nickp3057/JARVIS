"""Clipboard read/write for JARVIS.

Uses PowerShell Get-Clipboard / Set-Clipboard — no extra deps required.
Nick can copy anything to his clipboard and say 'summarize that' or
'translate what I copied' and JARVIS will read it.
"""

from __future__ import annotations

import subprocess
from jarvis_logger import get_logger

log = get_logger(__name__)

_MAX_CHARS = 8000   # Guard against enormous clipboard contents


def _run_ps(command: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0, (result.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def read_clipboard() -> str:
    """Return the current clipboard text content."""
    ok, text = _run_ps("Get-Clipboard")
    if not ok:
        return "Couldn't read the clipboard."
    if not text:
        return "The clipboard is empty."
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n[...truncated at {_MAX_CHARS} chars]"
    log.debug("Clipboard read: %d chars", len(text))
    return text


def write_clipboard(text: str) -> str:
    """Write text to the clipboard."""
    # Escape single quotes for PowerShell
    escaped = text.replace("'", "''")
    ok, _ = _run_ps(f"Set-Clipboard -Value '{escaped}'")
    if ok:
        log.debug("Clipboard written: %d chars", len(text))
        return "Copied to clipboard."
    return "Couldn't write to the clipboard."
