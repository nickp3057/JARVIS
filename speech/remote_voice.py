"""Per-request speech bytes for remote clients; never touches the room speaker.

ElevenLabs streams PCM as it is generated. Piper is a bounded local fallback
using a private temporary WAV, so concurrent room/phone speech cannot collide.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import wave
from collections.abc import Iterator

import requests

SAMPLE_RATE = 22050
CHUNK_BYTES = 4096


def pcm_stream(text: str, stopped: threading.Event, previous_text: str = "") -> Iterator[bytes]:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    sent = False
    if key and voice:
        try:
            speed = max(0.7, min(1.2, float(os.getenv("ELEVENLABS_SPEED", "1.05"))))
            with requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream",
                headers={"xi-api-key": key, "Accept": "application/octet-stream"},
                params={"output_format": f"pcm_{SAMPLE_RATE}"},
                json={"text": text, "model_id": os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
                      "previous_text": previous_text[-1000:],
                      "voice_settings": {"stability": 0.5, "similarity_boost": 0.75,
                                         "style": 0.0, "use_speaker_boost": True, "speed": speed}},
                stream=True, timeout=(5, 15),
            ) as response:
                response.raise_for_status()
                pending = b""
                for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                    if stopped.is_set():
                        return
                    pending += chunk
                    size = len(pending) & ~1  # PCM frames may cross HTTP chunks
                    if size:
                        sent = True
                        yield pending[:size]
                        pending = pending[size:]
                if not sent or pending:
                    raise RuntimeError("Incomplete speech audio")
                return
        except (requests.RequestException, ValueError, RuntimeError):
            if sent:
                # Never repeat the beginning of a sentence in another voice.
                raise RuntimeError("The voice stream was interrupted") from None
    if stopped.is_set():
        return
    yield from _piper(text, stopped)


def _piper(text: str, stopped: threading.Event) -> Iterator[bytes]:
    voice_dir = Path(__file__).resolve().parents[1] / "voices"
    with tempfile.TemporaryDirectory(prefix="jarvis-phone-voice-") as directory:
        path = Path(directory) / "speech.wav"
        try:
            subprocess.run([sys.executable, "-m", "piper", "--data-dir", str(voice_dir),
                            "-m", "en_US-ryan-high", "-f", str(path), "--", text],
                           check=True, capture_output=True, timeout=30)
            with wave.open(str(path), "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, SAMPLE_RATE):
                    raise RuntimeError("Unsupported fallback audio format")
                while not stopped.is_set():
                    chunk = audio.readframes(CHUNK_BYTES // 2)
                    if not chunk:
                        return
                    yield chunk
        except (OSError, subprocess.SubprocessError, wave.Error):
            raise RuntimeError("Speech is unavailable; the text reply is still available") from None
