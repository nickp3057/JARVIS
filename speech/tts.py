"""Text-to-speech for the JARVIS assistant.

Primary engine: **ElevenLabs Flash v2.5** - neural voice, ~75ms latency,
configurable speed.
Fallback: **Piper** (local) - offline-safe; used automatically if ElevenLabs
is unconfigured, fails, or the network drops.

``TTS_PROVIDER=piper`` swaps the order: a local Piper voice
(``PIPER_VOICE_MODEL``, loaded once in-process by ``speech/piper_voice.py``)
speaks first, streamed into aplay sentence by sentence, and ElevenLabs is the
fallback for any line Piper can't say.

Try a voice by ear: ``python -m speech.tts --say "Good evening, sir."``
(``--provider piper --model voices/en_GB-alan-medium.onnx`` to pick one,
``--wav out.wav`` to write a file instead of playing).

Playback is **interruptible**: ``speak(text, interrupt_event=...)`` plays
asynchronously and watches the supplied threading.Event. If something else
sets the event mid-playback, the audio is purged and the function returns.

Without an interrupt event, ``speak()`` blocks until playback finishes
(or until an estimated duration elapses, with a small safety margin).
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import requests
from dotenv import load_dotenv

from jarvis_logger import get_logger
from speech import cloud_http, piper_voice

load_dotenv()

log = get_logger(__name__)

# --- Paths ------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VOICE_DIR = _PROJECT_ROOT / "voices"
_OUTPUT_FILE = _PROJECT_ROOT / "jarvis_output.wav"

# --- Engine config ----------------------------------------------------------

# The old fallback's stock voice (voices/en_US-ryan-high.onnx); used when
# PIPER_VOICE_MODEL is empty. With it set, the fallback uses that model too.
PIPER_VOICE_MODEL = piper_voice.FALLBACK_VOICE

# Milliseconds from the start of the last line to its first sound (CLI, logs).
last_first_audio_ms: float | None = None

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
ELEVENLABS_SPEED = float(os.getenv("ELEVENLABS_SPEED", "1.05"))  # 0.7 to 1.2

_ELEVENLABS_TIMEOUT_SECONDS = 15
_ELEVENLABS_SAMPLE_RATE = 22050
_ELEVENLABS_OUTPUT_FORMAT = f"pcm_{_ELEVENLABS_SAMPLE_RATE}"


# --- ElevenLabs (primary) ---------------------------------------------------

def _elevenlabs_configured() -> bool:
    return bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID)


def _wrap_pcm_as_wav(pcm_bytes: bytes, path: Path, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)


def _elevenlabs_request(text: str) -> tuple[str, dict]:
    """URL and ``requests`` keyword arguments for one line of speech."""
    # Re-read speed from env on every call so update_setting changes take
    # effect immediately without a restart.
    current_speed = float(os.getenv("ELEVENLABS_SPEED", str(ELEVENLABS_SPEED)))
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    return url, {
        "headers": {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        },
        "params": {"output_format": _ELEVENLABS_OUTPUT_FORMAT},
        "json": {
            "text": text,
            "model_id": ELEVENLABS_MODEL,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": max(0.7, min(1.2, current_speed)),
            },
        },
        "timeout": _ELEVENLABS_TIMEOUT_SECONDS,
    }


def _synthesize_elevenlabs(text: str) -> bool:
    if not _elevenlabs_configured():
        return False

    url, request = _elevenlabs_request(text)
    try:
        response = cloud_http.post(url, **request)
    except requests.RequestException as exc:
        log.warning("ElevenLabs request failed: %s", exc)
        return False

    if response.status_code != 200:
        log.warning("ElevenLabs returned %s: %s", response.status_code, response.text[:300])
        return False

    try:
        _wrap_pcm_as_wav(response.content, _OUTPUT_FILE, _ELEVENLABS_SAMPLE_RATE)
    except Exception:  # noqa: BLE001
        log.exception("Failed to write ElevenLabs PCM to WAV.")
        return False

    return True


def warm_cloud() -> None:
    """Open the ElevenLabs connection ahead of the reply (the wake word just
    fired). No-op without ElevenLabs."""
    if _elevenlabs_configured():
        cloud_http.warm("https://api.elevenlabs.io/")


# --- ElevenLabs, played while it downloads -----------------------------------
# Waiting for the whole clip before starting aplay cost a median 0.58 s and
# over a second for long lines (Optiplex log, 2026-11-09). On Linux the audio
# goes straight into aplay as it arrives, so the first word plays as soon as
# ElevenLabs sends it. ``TTS_STREAM_PLAYBACK=0`` goes back to whole clips.

def _stream_playback_enabled() -> bool:
    if os.getenv("TTS_STREAM_PLAYBACK", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return sys.platform.startswith("linux") and _elevenlabs_configured() and shutil.which("aplay") is not None


def _stream_elevenlabs(text: str, interrupt_event: threading.Event | None) -> bytes | None:
    """Speak ``text`` through aplay while ElevenLabs is still sending it.

    Returns the audio that was played: all of it normally, a part when
    something stopped it (``interrupt_event``, ``stop_playback``, a dropped
    download). None means nothing was heard and the caller should use the
    whole-clip path, with its Piper fallback, instead."""
    url, request = _elevenlabs_request(text)
    started = time.perf_counter()
    try:
        response = cloud_http.post(f"{url}/stream", stream=True, **request)
    except requests.RequestException as exc:
        log.warning("ElevenLabs stream request failed: %s", exc)
        return None
    if response.status_code != 200:
        log.warning("ElevenLabs stream returned %s: %s", response.status_code, response.text[:300])
        response.close()
        return None

    try:
        proc = subprocess.Popen(
            ["aplay", "-q", *alsa_device_args(), "-t", "raw", "-f", "S16_LE",
             "-r", str(_ELEVENLABS_SAMPLE_RATE), "-c", "1", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except Exception:  # noqa: BLE001 - no aplay, bad device: the whole-clip path reports it
        log.debug("Could not start aplay for streamed speech", exc_info=True)
        response.close()
        return None

    global _current_player
    with _player_lock:
        _current_player = proc
    pcm = bytearray()
    complete = False
    try:
        try:
            for chunk in response.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                if not pcm:
                    _note_first_audio("ElevenLabs stream", started, level=10)
                if (interrupt_event is not None and interrupt_event.is_set()) or proc.poll() is not None:
                    break
                proc.stdin.write(chunk)  # type: ignore[union-attr]
                pcm += chunk
            else:
                complete = True
        except requests.RequestException as exc:  # (an OSError too, so it's caught first)
            log.warning("ElevenLabs stream dropped after %d bytes: %s", len(pcm), exc)
        except OSError:
            pass  # aplay went away mid-write: stop_playback, or it failed (checked below)
        finally:
            response.close()
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except OSError:
                pass
        # The download is done; aplay still has the tail of the sentence to play.
        while proc.poll() is None:
            if interrupt_event is not None and interrupt_event.is_set():
                proc.terminate()
                proc.wait(timeout=2)
                log.info("TTS interrupted by event.")
                break
            time.sleep(0.04)
    finally:
        with _player_lock:
            if _current_player is proc:
                _current_player = None
    stopped = (proc.returncode or 0) < 0 or (interrupt_event is not None and interrupt_event.is_set())
    if stopped:  # a hush or barge-in, even before the first sound: never say it again
        return b""
    if proc.returncode:  # aplay itself failed
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        log.warning("Streamed playback failed (exit %s): %s", proc.returncode, stderr.strip())
        return None
    if not pcm:
        return None
    log.debug("Spoke via ElevenLabs stream (%s).", "complete" if complete else "cut short")
    return bytes(pcm) if complete else b""


def _note_first_audio(engine: str, started: float, level: int = 20) -> None:
    """Record (and log) how long this line took to make its first sound."""
    global last_first_audio_ms
    last_first_audio_ms = (time.perf_counter() - started) * 1000
    log.log(level, "4. TTS first audio (%s): %.0f ms", engine, last_first_audio_ms)


# --- Piper as the main voice (TTS_PROVIDER=piper) ----------------------------

def _piper_stream_enabled() -> bool:
    if os.getenv("TTS_STREAM_PLAYBACK", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return sys.platform.startswith("linux") and shutil.which("aplay") is not None


def _stream_piper(text: str, interrupt_event: threading.Event | None,
                  cache_to: Path | None = None) -> bytes | None:
    """Speak ``text`` with the local Piper voice, piping each sentence into
    aplay as soon as it is synthesized.

    Same contract as ``_stream_elevenlabs``: the audio that was played (b""
    when it was stopped or cut short), or None when nothing was heard and the
    caller should try another way."""
    started = time.perf_counter()
    try:
        rate, chunks = piper_voice.open_stream(text)
        first = next(chunks, b"")
    except piper_voice.PiperUnavailable as exc:
        piper_voice.warn_once(str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 - onnxruntime errors vary
        piper_voice.warn_once(f"synthesis failed: {exc}")
        return None
    if not first:
        piper_voice.warn_once("synthesis produced no audio")
        return None
    if interrupt_event is not None and interrupt_event.is_set():
        return b""

    try:
        proc = subprocess.Popen(
            ["aplay", "-q", *alsa_device_args(), "-t", "raw", "-f", "S16_LE",
             "-r", str(rate), "-c", "1", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except Exception:  # noqa: BLE001 - no aplay, bad device: the whole-clip path reports it
        log.debug("Could not start aplay for Piper speech", exc_info=True)
        return None
    _note_first_audio("Piper stream", started)

    global _current_player
    with _player_lock:
        _current_player = proc
    pcm = bytearray()
    complete = False
    try:
        try:
            chunk: bytes | None = first
            while chunk is not None:
                if (interrupt_event is not None and interrupt_event.is_set()) or proc.poll() is not None:
                    break
                proc.stdin.write(chunk)  # type: ignore[union-attr]
                pcm += chunk
                chunk = next(chunks, None)
            else:
                complete = True
        except OSError:
            pass  # aplay went away mid-write: stop_playback, or it failed (checked below)
        except Exception as exc:  # noqa: BLE001 - synthesis died mid-line; never repeat it
            log.warning("Piper synthesis failed after %d bytes: %s", len(pcm), exc)
        finally:
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except OSError:
                pass
        while proc.poll() is None:
            if interrupt_event is not None and interrupt_event.is_set():
                proc.terminate()
                proc.wait(timeout=2)
                log.info("TTS interrupted by event.")
                break
            time.sleep(0.04)
    finally:
        with _player_lock:
            if _current_player is proc:
                _current_player = None
    stopped = (proc.returncode or 0) < 0 or (interrupt_event is not None and interrupt_event.is_set())
    if stopped:
        return b""
    if proc.returncode:
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        log.warning("Streamed Piper playback failed (exit %s): %s", proc.returncode, stderr.strip())
        return None
    if complete and cache_to is not None:
        _store_pcm_in_cache(bytes(pcm), cache_to, rate)
    return bytes(pcm) if complete else b""


def _speak_piper(
    text: str,
    interrupt_event: threading.Event | None,
    on_envelope: Callable[[list[float], float], None] | None,
    cached: Path | None,
) -> bool:
    """Say ``text`` with the Piper voice. False means it couldn't (the caller
    falls back to ElevenLabs); the reason is logged once."""
    if on_envelope is None and _piper_stream_enabled():
        if _stream_piper(text, interrupt_event, cached) is not None:
            return True
    started = time.perf_counter()
    try:
        piper_voice.write_wav(text, _OUTPUT_FILE)
    except piper_voice.PiperUnavailable as exc:
        piper_voice.warn_once(str(exc))
        return False
    except Exception as exc:  # noqa: BLE001
        piper_voice.warn_once(f"synthesis failed: {exc}")
        return False
    if cached is not None:
        _store_in_cache(cached)
    _emit_envelope(on_envelope)
    _note_first_audio("Piper", started)
    _play_async_and_wait(_OUTPUT_FILE, interrupt_event)
    return True


# --- Piper (fallback) -------------------------------------------------------

def _synthesize_piper(text: str) -> bool:
    """Whole-clip Piper into the output WAV, when ElevenLabs couldn't speak.
    Uses the in-process voice (PIPER_VOICE_MODEL, else the stock Ryan voice)
    and falls back to the old ``python -m piper`` run, which can download
    the stock voice if it's missing."""
    try:
        piper_voice.write_wav(text, _OUTPUT_FILE, default_model=PIPER_VOICE_MODEL)
        return True
    except piper_voice.PiperUnavailable as exc:
        log.debug("In-process Piper unavailable (%s); trying the piper CLI.", exc)
    except Exception:  # noqa: BLE001
        log.debug("In-process Piper failed; trying the piper CLI.", exc_info=True)
    command = [
        sys.executable, "-m", "piper",
        "--data-dir", str(_VOICE_DIR),
        "-m", PIPER_VOICE_MODEL,
        "-f", str(_OUTPUT_FILE),
        "--", text,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
        return True
    except FileNotFoundError:
        log.error("Piper is not installed or not on PATH.")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        log.error("Piper synthesis failed (exit %s): %s", exc.returncode, stderr.strip())
    except Exception:  # noqa: BLE001
        log.exception("Unexpected error during Piper synthesis.")
    return False


# --- Playback (interruptible) -----------------------------------------------

def _wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            if rate <= 0:
                return 0.0
            return frames / float(rate)
    except Exception:  # noqa: BLE001
        return 0.0


def _play_async_and_wait(
    wav_path: Path,
    interrupt_event: threading.Event | None,
) -> bool:
    """Start async playback, wait for completion or interrupt. Returns success."""
    if not wav_path.exists():
        log.error("TTS output file is missing: %s", wav_path)
        return False

    try:
        import winsound
    except ImportError:
        # Linux / macOS: aplay / afplay, stoppable (interrupt_event, stop_playback).
        return _play_posix_blocking(wav_path, interrupt_event)

    t0 = time.perf_counter()
    try:
        winsound.PlaySound(
            str(wav_path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception:  # noqa: BLE001
        log.exception("winsound async start failed")
        return False
    log.debug("5. winsound playback start: %.0f ms", (time.perf_counter() - t0) * 1000)

    duration = _wav_duration_seconds(wav_path)
    deadline = time.time() + duration + 0.4
    poll_s = 0.04

    while time.time() < deadline:
        if interrupt_event is not None and interrupt_event.is_set():
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:  # noqa: BLE001
                log.exception("winsound purge failed")
            log.info("TTS interrupted by event.")
            return True
        time.sleep(poll_s)

    return True


def _play_powershell_blocking(wav_path: Path) -> bool:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"(New-Object Media.SoundPlayer '{wav_path}').PlaySync();",
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
        return True
    except FileNotFoundError:
        log.error("PowerShell not found; cannot play audio.")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        log.error("Audio playback failed (exit %s): %s", exc.returncode, stderr.strip())
    except Exception:  # noqa: BLE001
        log.exception("Unexpected error during audio playback.")
    return False


def compute_rms_envelope(wav_path: Path, fps: int = 30) -> list[float]:
    """Read a WAV file and return a normalized RMS envelope at ``fps`` frames/sec.

    Values are in [0.0, 1.0] relative to the loudest frame in the file.
    Returns an empty list if the file can't be read.
    """
    try:
        import numpy as np

        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate()
            sampwidth = wf.getsampwidth()
            frame_size = max(1, rate // fps)
            dtype = np.int16 if sampwidth == 2 else np.int8
            rms_values: list[float] = []
            while True:
                raw = wf.readframes(frame_size)
                if not raw:
                    break
                arr = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                if arr.size > 0:
                    rms_values.append(float(np.sqrt(np.mean(arr ** 2))))

        if not rms_values:
            return []
        peak = max(rms_values) or 1.0
        return [v / peak for v in rms_values]
    except Exception:  # noqa: BLE001
        log.debug("compute_rms_envelope failed; audio reactivity unavailable.")
        return []


def stop_playback() -> None:
    """Force-stop any in-progress TTS playback. Safe to call from any thread."""
    with _player_lock:
        player = _current_player
    if player is not None and player.poll() is None:
        player.terminate()
    try:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("stop_playback failed")


# --- Cache for lines said often ---------------------------------------------
# "Sleep well, sir." and friends: synthesized once per voice/model/speed and
# replayed, so routine lines start instantly. Only ElevenLabs audio is kept.

_CACHE_DIR = _PROJECT_ROOT / "memory" / "tts_cache"
_CACHE_MAX_FILES = 200


def _cache_path(text: str) -> Path:
    import hashlib
    if piper_voice.selected():  # its own entries: a new model or knob re-synthesizes
        knobs = piper_voice.settings()
        key = "|".join(("piper", str(piper_voice.model_path()),
                        *(str(knobs[name]) for name in sorted(knobs)), text.strip()))
        return _CACHE_DIR / f"{hashlib.sha1(key.encode()).hexdigest()[:20]}.wav"
    speed = os.getenv("ELEVENLABS_SPEED", str(ELEVENLABS_SPEED))
    key = "|".join((ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL, speed, text.strip()))
    return _CACHE_DIR / f"{hashlib.sha1(key.encode()).hexdigest()[:20]}.wav"


_COUNTS_MAX = 500
_counts_lock = threading.Lock()


def _said_before(text: str) -> bool:
    """Count this line; True from its second time (memory/tts_cache/said.json)."""
    import json
    path = _CACHE_DIR / "said.json"
    key = text.strip().lower()
    if len(key) > 160:  # long answers rarely repeat word for word
        return False
    with _counts_lock:
        try:
            counts = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            counts = {}
        counts[key] = counts.pop(key, 0) + 1  # re-insert: most recent last
        while len(counts) > _COUNTS_MAX:
            counts.pop(next(iter(counts)))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(counts))
        except OSError:
            pass
        return counts[key] >= 2


def _store_pcm_in_cache(pcm: bytes, path: Path, sample_rate: int = _ELEVENLABS_SAMPLE_RATE) -> None:
    """Keep a line that was streamed (there is no output file to copy)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _wrap_pcm_as_wav(pcm, path, sample_rate)
        _trim_cache(path.parent)
    except OSError:
        log.debug("TTS cache write failed", exc_info=True)


def _trim_cache(folder: Path) -> None:
    files = sorted(folder.glob("*.wav"), key=lambda f: f.stat().st_mtime)  # said.json isn't a wav
    for old in files[:-_CACHE_MAX_FILES]:  # oldest go first
        old.unlink(missing_ok=True)


def _store_in_cache(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_OUTPUT_FILE, path)
        _trim_cache(path.parent)
    except OSError:
        log.debug("TTS cache write failed", exc_info=True)


# --- Public API -------------------------------------------------------------

def speak(
    text: str,
    interrupt_event: threading.Event | None = None,
    on_envelope: Callable[[list[float], float], None] | None = None,
    cache: bool | str = False,
) -> None:
    """Speak ``text`` aloud.

    Args:
        text: The text to synthesize and play.
        interrupt_event: If set mid-playback, audio is stopped immediately.
        on_envelope: Optional callback fired after synthesis but *before*
            playback starts.  Receives ``(envelope, frame_duration_seconds)``
            where ``envelope`` is a list of normalized [0, 1] RMS values at
            ~30 fps.  Use this to drive audio-reactive UI elements in sync
            with playback.
        cache: True keeps (and reuses) this line's audio, for fixed lines
            like the bedtime routine's; "auto" keeps it once the same line
            has been said before (JARVIS learning his usual answers).
    """
    if not text or not text.strip():
        return

    log.debug("speak(): %s", text)

    if cache == "auto":
        cache = _said_before(text)
    cached = _cache_path(text) if cache else None
    if cached is not None and cached.exists():
        _play_async_and_wait(cached, interrupt_event)  # no synthesis: starts at once
        return

    if piper_voice.selected():
        if _speak_piper(text, interrupt_event, on_envelope, cached):
            return
        cached = None  # the fallback voice must not fill Piper's cache entries

    if on_envelope is None and _stream_playback_enabled():
        played = _stream_elevenlabs(text, interrupt_event)
        if played is not None:
            if cached is not None and played:  # only a whole line is worth keeping
                _store_pcm_in_cache(played, cached)
            return

    t0 = time.perf_counter()
    if _synthesize_elevenlabs(text):
        if cached is not None:
            _store_in_cache(cached)
        log.debug("Synthesized via ElevenLabs.")
        log.debug("4. TTS synthesis (ElevenLabs): %.0f ms", (time.perf_counter() - t0) * 1000)
    elif _synthesize_piper(text):
        log.debug("Synthesized via Piper fallback.")
        log.debug("4. TTS synthesis (Piper): %.0f ms", (time.perf_counter() - t0) * 1000)
    else:
        log.error("All TTS engines failed; staying silent.")
        return

    _emit_envelope(on_envelope)
    _play_async_and_wait(_OUTPUT_FILE, interrupt_event)


def _emit_envelope(on_envelope: Callable[[list[float], float], None] | None) -> None:
    if on_envelope is None:
        return
    try:
        envelope = compute_rms_envelope(_OUTPUT_FILE, fps=30)
        if envelope:
            on_envelope(envelope, 1.0 / 30)
    except Exception:  # noqa: BLE001
        log.debug("on_envelope callback raised; skipping.")



# --- Streaming playback (opt-in, lower perceived latency) --------------------

# A sentence ends on .!? (plus optional closing quotes/brackets) then whitespace.
_SENTENCE_BOUNDARY = re.compile(r'[.!?]+["\')\]]*\s+')


def _split_ready_sentences(buffer: str) -> tuple[list[str], str]:
    """Split ``buffer`` into complete sentences and a trailing remainder.

    Only boundaries followed by whitespace count, so decimals like "3.14"
    and abbreviations mid-token aren't split prematurely.
    """
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(buffer):
        sentence = buffer[start:match.end()].strip()
        if sentence:
            sentences.append(sentence)
        start = match.end()
    return sentences, buffer[start:]


def speak_stream(
    chunks,
    interrupt_event: threading.Event | None = None,
    on_envelope: Callable[[list[float], float], None] | None = None,
) -> bool:
    """Speak an iterable of text fragments, synthesizing per sentence.

    As soon as a full sentence has arrived it is synthesized and played while
    later fragments are still being produced upstream (e.g. by a streaming
    LLM response). This pipelines generation and speech for much lower
    perceived latency than waiting for the whole reply. Each sentence is
    spoken via :func:`speak`, so the ElevenLabs/Piper fallback and the
    interrupt behavior are inherited unchanged.

    Returns True if anything was spoken.
    """
    buffer = ""
    spoke_any = False
    for chunk in chunks:
        if interrupt_event is not None and interrupt_event.is_set():
            return spoke_any
        if not chunk:
            continue
        buffer += chunk
        ready, buffer = _split_ready_sentences(buffer)
        for sentence in ready:
            if interrupt_event is not None and interrupt_event.is_set():
                return spoke_any
            speak(sentence, interrupt_event=interrupt_event, on_envelope=on_envelope)
            spoke_any = True

    tail = buffer.strip()
    if tail and not (interrupt_event is not None and interrupt_event.is_set()):
        speak(tail, interrupt_event=interrupt_event, on_envelope=on_envelope)
        spoke_any = True
    return spoke_any


def alsa_device_args() -> list[str]:
    """``-D <device>`` for aplay when ``AUDIO_OUTPUT_DEVICE`` is set (the
    Optiplex's USB mic is ALSA card 0, so the default device can't play)."""
    device = os.getenv("AUDIO_OUTPUT_DEVICE", "").strip()
    return ["-D", device] if device else []


# The aplay/afplay process playing right now, so stop_playback can end it.
_current_player: subprocess.Popen | None = None
_player_lock = threading.Lock()


def _play_posix_blocking(wav_path: Path, interrupt_event: threading.Event | None = None) -> bool:
    """Linux/macOS playback via aplay (ALSA) / afplay. Waits for the end, but
    ``interrupt_event`` or ``stop_playback()`` stops it early."""
    player = "aplay" if sys.platform.startswith("linux") else "afplay"
    t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(
            ([player, "-q", *alsa_device_args(), str(wav_path)] if player == "aplay"
             else [player, str(wav_path)]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        log.error("%s not found; cannot play audio.", player)
        return False
    except Exception:  # noqa: BLE001
        log.exception("Unexpected error starting audio playback.")
        return False
    log.debug("5. %s playback start (process spawn): %.0f ms", player, (time.perf_counter() - t0) * 1000)

    global _current_player
    with _player_lock:
        _current_player = proc
    try:
        while proc.poll() is None:
            if interrupt_event is not None and interrupt_event.is_set():
                proc.terminate()
                proc.wait(timeout=2)
                log.info("TTS interrupted by event.")
                return True
            time.sleep(0.04)
    finally:
        with _player_lock:
            if _current_player is proc:
                _current_player = None
    if proc.returncode < 0:  # stopped by stop_playback (a signal), not a failure
        return True
    if proc.returncode != 0:
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        log.error("Audio playback failed (exit %s): %s", proc.returncode, stderr.strip())
        return False
    return True


# --- Try a voice by ear -------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m speech.tts",
        description="Speak one line through JARVIS's voice (room speaker) and report the time to first audio.")
    parser.add_argument("--say", required=True, help="the line to speak")
    parser.add_argument("--provider", choices=("piper", "elevenlabs"), help="overrides TTS_PROVIDER for this run")
    parser.add_argument("--model", help="overrides PIPER_VOICE_MODEL for this run (.onnx path or voice name)")
    parser.add_argument("--wav", help="write Piper's audio to this WAV instead of playing it")
    args = parser.parse_args(argv)
    if args.provider:
        os.environ["TTS_PROVIDER"] = args.provider
    if args.model:
        os.environ["PIPER_VOICE_MODEL"] = args.model

    if args.wav:
        path = piper_voice.model_path(PIPER_VOICE_MODEL)
        try:
            if path is not None:
                piper_voice.load(path)  # load time is reported separately from synthesis
            started = time.perf_counter()
            rate = piper_voice.write_wav(args.say, Path(args.wav), default_model=PIPER_VOICE_MODEL)
        except piper_voice.PiperUnavailable as exc:
            print(f"Piper unavailable: {exc}")
            return 1
        print(f"Wrote {args.wav} ({rate} Hz) in {(time.perf_counter() - started) * 1000:.0f} ms")
        return 0

    if piper_voice.selected():
        path = piper_voice.model_path()
        if path is not None:
            try:
                piper_voice.load(path)  # warm, so the number below is per-line latency
            except piper_voice.PiperUnavailable as exc:
                print(f"Piper unavailable ({exc}); the fallback voice will speak.")
    speak(args.say)
    if last_first_audio_ms is None:
        print("No first-audio time recorded (a cached line, or ElevenLabs' whole-clip path).")
    else:
        print(f"First audio after {last_first_audio_ms:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
