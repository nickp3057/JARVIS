"""Screen capture + Claude vision for JARVIS.

Takes a screenshot using PyQt6 (already installed), encodes it as PNG/base64,
then makes a separate Claude API call with the image so JARVIS can answer
questions about what's currently on screen.

This gives JARVIS actual eyes.
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from jarvis_logger import get_logger

log = get_logger(__name__)

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

# Vision model is configurable; default to a current vision-capable model.
_VISION_MODEL = os.getenv("VISION_MODEL", "claude-sonnet-4-6")
_SCREEN_PNG = Path(__file__).resolve().parent.parent / "memory" / "_screen_cap.png"


def _capture_via_qt() -> bytes | None:
    """Capture primary screen using PyQt6 QScreen (no extra deps)."""
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QBuffer, QIODevice
        app = QApplication.instance()
        if app is None:
            return None
        screen = app.primaryScreen()
        if screen is None:
            return None
        all_monitors = os.getenv("VISION_ALL_MONITORS", "true").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if all_monitors:
            geo = screen.virtualGeometry()
            pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        else:
            pixmap = screen.grabWindow(0)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "PNG")
        data = bytes(buf.data())
        buf.close()
        return data if data else None
    except Exception as exc:  # noqa: BLE001
        log.debug("Qt screen capture failed: %s", exc)
        return None


def _capture_via_powershell() -> bytes | None:
    """Fallback: capture screen via PowerShell + System.Drawing."""
    try:
        _SCREEN_PNG.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            'Add-Type -AssemblyName System.Windows.Forms,System.Drawing;'
            '$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;'
            '$bmp=New-Object System.Drawing.Bitmap($b.Width,$b.Height);'
            '$g=[System.Drawing.Graphics]::FromImage($bmp);'
            '$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);'
            f'$bmp.Save("{str(_SCREEN_PNG).replace(chr(92), chr(92)*2)}",'
            '[System.Drawing.Imaging.ImageFormat]::Png);'
            '$g.Dispose();$bmp.Dispose()'
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            log.warning("PowerShell screenshot failed: %s", result.stderr)
            return None
        if _SCREEN_PNG.exists():
            data = _SCREEN_PNG.read_bytes()
            _SCREEN_PNG.unlink(missing_ok=True)
            return data
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("PowerShell screen capture failed: %s", exc)
        return None


def capture_screen_b64() -> str | None:
    """Return the primary screen as a base64-encoded PNG string, or None on failure."""
    data = _capture_via_qt() or _capture_via_powershell()
    if not data:
        return None
    return base64.standard_b64encode(data).decode()


def describe_screen(question: str = "What's on the screen right now?") -> str:
    """Take a screenshot and ask Claude to answer a question about it."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "I can't use vision — ANTHROPIC_API_KEY is not set."

    b64 = capture_screen_b64()
    if b64 is None:
        return "I couldn't capture the screen. Try again in a moment."

    try:
        from anthropic import Anthropic  # type: ignore
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_VISION_MODEL,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"{question}\n\n"
                                "Respond in 1-3 sentences, as if you're JARVIS speaking aloud. "
                                "No markdown, no lists. Plain spoken English."
                            ),
                        },
                    ],
                }
            ],
        )
        from brain import usage as _usage
        _usage.record_response('jarvis', response)
        text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]
        return "".join(getattr(b, "text", "") for b in text_blocks).strip() or "I see it but have nothing to add."
    except Exception as exc:  # noqa: BLE001
        log.exception("Vision API call failed")
        return f"Vision failed: {exc}"
