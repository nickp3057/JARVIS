"""Safe, voice-editable runtime settings.

Exposes a small whitelist of settings (no API keys, no secrets) that Nick
can change by voice or chat - "set speed to 1.1", "change humor to 65",
"default location is Herriman, Utah". JARVIS calls ``update_setting`` and
this module:

1. Validates the value against the schema (type + range).
2. Atomically rewrites ``.env`` preserving comments and ordering.
3. Updates ``os.environ`` so any module reading at request time picks up
   the change immediately.

For settings whose modules cache their value at import time (most of
them), the change takes effect on next launch. The personality module
re-reads HUMOR_PERCENT and ADDRESS on every request, so those tune live.
"""

from __future__ import annotations

import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


# --- Schema -----------------------------------------------------------------
# Each entry: type (float|int|string|choice|bool), constraints, description.
# Settings NOT in this dict cannot be changed via this interface.

SETTINGS_SCHEMA: dict[str, dict[str, Any]] = {
    # --- Personality ---
    "HUMOR_PERCENT": {
        "type": "int",
        "min": 0,
        "max": 100,
        "description": "How much dry humor JARVIS uses (0=none, 100=constant). Default 30.",
    },
    "ADDRESS": {
        "type": "string",
        "description": "What JARVIS calls Nick: 'sir', 'boss', 'Mr. Stark', etc.",
    },

    # --- Voice / TTS ---
    "ELEVENLABS_SPEED": {
        "type": "float",
        "min": 0.7,
        "max": 1.2,
        "description": "ElevenLabs speech speed. 1.0 normal, higher faster.",
    },

    # --- Weather ---
    "DEFAULT_LOCATION": {
        "type": "string",
        "description": "Default city for weather lookups. e.g. 'Herriman, Utah'.",
    },

    # --- Wake word + mic tuning ---
    "WAKE_THRESHOLD": {
        "type": "float",
        "min": 0.2,
        "max": 0.95,
        "description": "Wake word sensitivity. Lower = easier to trigger.",
    },
    "MIC_RMS_THRESHOLD": {
        "type": "float",
        "min": 100.0,
        "max": 3000.0,
        "description": "How loud counts as 'voice' for normal listening.",
    },
    "VOICE_SILENCE_END_MS": {
        "type": "int",
        "min": 250,
        "max": 2000,
        "description": "Room voice: how long a pause ends what Nick said. Raise if JARVIS cuts him off.",
    },
    "VOICE_FOLLOWUP_S": {
        "type": "float",
        "min": 0.0,
        "max": 15.0,
        "description": "Room voice: seconds JARVIS keeps listening for an answer or follow-up (no wake word).",
    },
    "VOICE_START_TIMEOUT_S": {
        "type": "float",
        "min": 1.0,
        "max": 10.0,
        "description": "Room voice: how long to wait after 'Hey JARVIS' for Nick to start talking.",
    },
    "VOICE_SHORTCUT_AFTER": {
        "type": "int",
        "min": 1,
        "max": 10,
        "description": "Room voice: same phrase + same simple action this many times becomes an instant shortcut.",
    },
    "MIC_SILENCE_END_MS": {
        "type": "int",
        "min": 200,
        "max": 3000,
        "description": "Milliseconds of silence before utterance is considered done.",
    },

    # --- Conversation flow ---
    "FOLLOWUP_TIMEOUT_S": {
        "type": "float",
        "min": 2.0,
        "max": 60.0,
        "description": "Follow-up window after conversational replies (seconds).",
    },
    "FOLLOWUP_TASK_S": {
        "type": "float",
        "min": 0.0,
        "max": 30.0,
        "description": "Follow-up window after task replies like turning lights on (seconds).",
    },
    "PREROLL_SECONDS": {
        "type": "float",
        "min": 1.0,
        "max": 8.0,
        "description": "How far back the rolling buffer reaches when wake word fires.",
    },
    "POSTROLL_START_TIMEOUT_S": {
        "type": "float",
        "min": 0.5,
        "max": 5.0,
        "description": "How long to wait for forward speech after wake word.",
    },
    "POSTROLL_MAX_S": {
        "type": "float",
        "min": 3.0,
        "max": 30.0,
        "description": "Max forward listening duration after wake word.",
    },

    # --- Interrupt detection ---
    "INTERRUPT_RMS_THRESHOLD": {
        "type": "float",
        "min": 500.0,
        "max": 5000.0,
        "description": "How loud user must be to interrupt JARVIS mid-reply.",
    },
    "INTERRUPT_CONSECUTIVE_FRAMES": {
        "type": "int",
        "min": 1,
        "max": 20,
        "description": "How many ~80ms frames of voice required to trigger interrupt.",
    },

    # --- STT model ---
    "WHISPER_MODEL": {
        "type": "choice",
        "choices": ["tiny.en", "base.en", "small.en", "medium.en"],
        "description": "Faster-whisper model. Bigger = more accurate, slower.",
    },

    # --- Spotify routing ---
    "SPOTIFY_PREFERRED_DEVICE": {
        "type": "string",
        "description": "Substring of preferred Spotify Connect device name.",
    },

    # --- Web search toggle ---
    "ANTHROPIC_MODEL": {
        "type": "string",
        "description": "Claude model id for conversation turns (read live). Empty or 'auto' follows the newest Haiku; 'auto:sonnet' follows the newest Sonnet; a full id pins it.",
    },
    "WEB_SEARCH_ENABLED": {
        "type": "bool",
        "description": "Whether Claude can use the web search tool.",
    },

    # --- Price watching ---
    "PRICE_WATCH_MONITOR_ENABLED": {
        "type": "bool",
        "description": "Whether JARVIS periodically checks saved public product URLs.",
    },
    "PRICE_WATCH_INTERVAL_S": {
        "type": "float",
        "min": 300.0,
        "max": 604800.0,
        "description": "Seconds between automatic price checks (minimum five minutes).",
    },
    "PRICE_WATCH_DROP_PERCENT": {
        "type": "float",
        "min": 0.1,
        "max": 100.0,
        "description": "Price decrease percentage that triggers an alert.",
    },

    # --- Companion media storage ---
    "MEDIA_STORAGE_PATH": {
        "type": "string",
        "description": "Mounted path containing video files for the companion storage card.",
    },
    "MEDIA_STORAGE_LABEL": {
        "type": "string",
        "description": "Friendly label for the media drive in the companion Hub.",
    },

    # --- Daily study brief (phone notification counting down to assessments) ---
    "STUDY_BRIEF_ENABLED": {
        "type": "bool",
        "description": "Whether the daily study brief notification is sent at all.",
    },
    "STUDY_BRIEF_HOUR": {
        "type": "int",
        "min": 0,
        "max": 23,
        "description": "Hour of the day (0-23, local) the study brief fires. Default 9.",
    },
    "STUDY_BRIEF_WINDOW_DAYS": {
        "type": "int",
        "min": 1,
        "max": 60,
        "description": "How many days ahead an assessment starts appearing in the brief. Default 14.",
    },
    "STUDY_BRIEF_KEYWORDS": {
        "type": "string",
        "description": (
            "Comma-separated calendar-title words that count as an assessment. "
            "Default: quiz,test,testlet,exam,midterm,final."
        ),
    },
    "STUDY_BRIEF_ASSIGNMENT_KEYWORDS": {
        "type": "string",
        "description": (
            "Comma-separated calendar-title words that count as work due. "
            "Default: assignment,homework,hw,lab,project,paper,essay,due."
        ),
    },
    "STUDY_BRIEF_ASSIGNMENT_WINDOW_DAYS": {
        "type": "int",
        "min": 1,
        "max": 60,
        "description": "How many days ahead an assignment starts appearing in the brief. Default 7.",
    },
    "STUDY_BRIEF_STYLE": {
        "type": "string",
        "description": (
            "Free-form note on how the brief should read (tone, what to emphasise, "
            "what to skip). Blank means JARVIS's default voice."
        ),
    },
}


# --- Validation -------------------------------------------------------------

def _coerce_value(key: str, raw: Any) -> tuple[bool, Any, str]:
    """Validate + coerce ``raw`` for ``key``. Returns (ok, coerced, error)."""
    spec = SETTINGS_SCHEMA[key]
    kind = spec["type"]

    if isinstance(raw, str):
        raw = raw.strip()

    try:
        if kind in ("int", "float"):
            value = int(float(raw)) if kind == "int" else float(raw)
            if not math.isfinite(value):  # nan slips past every range check
                return False, None, f"{key} must be a real number"
            if "min" in spec and value < spec["min"]:
                return False, None, f"{key} must be >= {spec['min']}"
            if "max" in spec and value > spec["max"]:
                return False, None, f"{key} must be <= {spec['max']}"
            return True, value, ""

        if kind == "string":
            value = str(raw)
            # A line break would write a second KEY=value line into .env.
            if any(ch in value for ch in "\r\n\x00"):
                return False, None, f"{key} must be a single line"
            return True, value, ""

        if kind == "choice":
            value = str(raw)
            if value not in spec["choices"]:
                return False, None, f"{key} must be one of {spec['choices']}"
            return True, value, ""

        if kind == "bool":
            value = str(raw).strip().lower()
            if value in ("1", "true", "yes", "on", "y"):
                return True, "true", ""
            if value in ("0", "false", "no", "off", "n"):
                return True, "false", ""
            return False, None, f"{key} must be true/false"

        return False, None, f"Unknown setting type: {kind}"

    except (ValueError, TypeError):
        return False, None, f"Could not parse {raw!r} as {kind}"


# --- .env reader / writer ---------------------------------------------------

_KEY_LINE_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=")


def _read_env_lines() -> list[str]:
    if not _ENV_PATH.exists():
        return []
    with _ENV_PATH.open("r", encoding="utf-8") as f:
        return f.readlines()


def _write_env_atomic(lines: list[str]) -> None:
    tmp = _ENV_PATH.with_suffix(".env.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.writelines(lines)
    # Best-effort backup (single-slot) so a bad write is recoverable.
    backup = _ENV_PATH.with_suffix(".env.bak")
    try:
        if _ENV_PATH.exists():
            shutil.copyfile(_ENV_PATH, backup)
    except OSError:
        pass
    tmp.replace(_ENV_PATH)


def _set_env_value(key: str, value: str) -> None:
    """Write ``KEY=value`` to .env (replace existing line or append)."""
    formatted_value = _format_value(value)
    new_line = f"{key}={formatted_value}\n"

    lines = _read_env_lines()
    out: list[str] = []
    replaced = False
    for line in lines:
        m = _KEY_LINE_RE.match(line)
        if m and m.group(1) == key:
            out.append(new_line)
            replaced = True
        else:
            out.append(line)

    if not replaced:
        # Append on a fresh line.
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(new_line)

    _write_env_atomic(out)


def _format_value(value: Any) -> str:
    """Render a value for .env without surprises."""
    s = str(value)
    if any(ch in s for ch in (' ', '#', '"')):
        # Strings with spaces, comments, or quotes get safely quoted.
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


# --- Public API -------------------------------------------------------------

def list_settings() -> str:
    """Return a spoken-friendly description of all editable settings."""
    parts: list[str] = []
    for key, spec in SETTINGS_SCHEMA.items():
        current = os.getenv(key, "")
        line = f"{key} = {current or '(unset)'} [{spec['type']}]"
        parts.append(line)
    return "Editable settings:\n" + "\n".join(parts)


def get_setting(key: str) -> str:
    key = (key or "").strip().upper()
    if key not in SETTINGS_SCHEMA:
        return f"'{key}' is not an editable setting."
    current = os.getenv(key, "")
    return f"{key} is currently {current or '(unset)'}."


def update_setting(key: str, value: Any) -> str:
    """Validate and persist a setting change. Returns a spoken confirmation."""
    key = (key or "").strip().upper()
    if key not in SETTINGS_SCHEMA:
        return (
            f"'{key}' isn't an editable setting. Try one of: "
            + ", ".join(SETTINGS_SCHEMA.keys())
        )

    ok, coerced, err = _coerce_value(key, value)
    if not ok:
        return f"Couldn't update {key}: {err}"

    coerced_str = str(coerced)
    try:
        _set_env_value(key, coerced_str)
    except OSError as exc:
        log.exception("Failed to write .env")
        return f"Couldn't write to .env: {exc}"

    # Update live env so request-time readers (personality) see it now.
    os.environ[key] = coerced_str
    log.info("Setting updated: %s=%s", key, coerced_str)

    return f"{key} set to {coerced}. (some settings only take full effect on next launch.)"
