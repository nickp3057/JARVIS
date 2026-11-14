"""Light tracks for light sync: one "main colour" per 0.1 s of a film.

Ported from the light-sync prototype's ``analyze.py`` (2026-11-09). ffmpeg
decodes the film at 10 fps, scaled to 64x27, and each frame becomes one
colour: the biggest vivid hue region (not the frame average), or a warm
white when nothing on screen is vivid. Brightness follows each pixel's
brightest channel, not luma: luma counts pure blue as ~7% bright, so blue
scenes came out black.

Tracks are built on request, one film at a time ("make a light sync for
Hunger Games"), never for the whole library. The Surface (Core's host) has
no NAS mount and no system ffmpeg, so the file is read over HTTP from Plex
(the media Part's URL with the token, never logged) and ffmpeg comes from
the ``imageio-ffmpeg`` package (``shutil.which("ffmpeg")`` as a fallback).

Storage: ``memory/light_tracks/<plex ratingKey>.npy`` (uint8, N x 3) and
``memory/light_tracks/index.json`` ({ratingKey: title, year, frames,
built_at}). The .npy is written atomically.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, IO

import numpy as np

from jarvis_logger import get_logger

log = get_logger(__name__)

FPS, W, H = 10, 64, 27
_FRAME_BYTES = W * H * 3
_BINS = 18
_WARM_WHITE = np.array([1.0, 0.85, 0.7], np.float32)
_LUMA = np.array([.2126, .7152, .0722], np.float32)

_DIR = Path(__file__).resolve().parents[1] / "memory" / "light_tracks"
_lock = threading.RLock()

# The one build that may run: {"rating_key", "title", "year", "percent", "started_at", "proc"}.
_build: dict[str, Any] | None = None
_cancel = threading.Event()
_notifier: Callable[[str, str, str], None] | None = None
_listeners: list[Callable[[], None]] = []


# --- The colour of one frame (analyze.py, faithfully) ------------------------

def frame_colour(frame: bytes | np.ndarray) -> np.ndarray:
    """One 64x27 rgb24 frame -> its main colour, 0-1 floats (already scaled by brightness)."""
    px = np.frombuffer(bytes(frame), np.uint8) if not isinstance(frame, np.ndarray) else frame
    px = px.reshape(-1, 3).astype(np.float32) / 255
    mx, mn = px.max(1), px.min(1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    vivid = (sat > 0.3) & (mx > 0.15)
    if vivid.sum() >= px.shape[0] * 0.03:
        r, g, bl = px[:, 0], px[:, 1], px[:, 2]
        d = np.maximum(mx - mn, 1e-6)
        hue = np.where(mx == r, ((g - bl) / d) % 6, np.where(mx == g, (bl - r) / d + 2, (r - g) / d + 4)) / 6
        hb = (hue * _BINS).astype(int) % _BINS
        w = sat * mx * vivid
        hist = np.bincount(hb, weights=w, minlength=_BINS)
        hist = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))  # neighbouring hues count too
        best = int(hist.argmax())
        sel = vivid & ((hb == best) | (hb == (best + 1) % _BINS) | (hb == (best - 1) % _BINS))
        col = (px[sel] * w[sel, None]).sum(0) / max(w[sel].sum(), 1e-6)
        col = col / max(col.max(), 1e-6)  # full-brightness hue
    else:
        col = _WARM_WHITE  # no vivid colour: warm white
    # Brightness follows the scene, from each pixel's brightest channel (not luma).
    level = min(1.0, 0.08 + float(mx.mean()) * 1.4)
    return np.asarray(col * level, np.float32)


def analyze_stream(stream: IO[bytes], on_frame: Callable[[int], None] | None = None,
                   cancel: threading.Event | None = None) -> np.ndarray:
    """Read raw 64x27 rgb24 frames until the stream ends -> uint8 N x 3 track."""
    rows: list[np.ndarray] = []
    while not (cancel and cancel.is_set()):
        chunk = _read_exact(stream, _FRAME_BYTES)
        if len(chunk) < _FRAME_BYTES:
            break
        rows.append(frame_colour(chunk))
        if on_frame:
            on_frame(len(rows))
    if not rows:
        return np.zeros((0, 3), np.uint8)
    return (np.array(rows) * 255).clip(0, 255).astype(np.uint8)


def _read_exact(stream: IO[bytes], size: int) -> bytes:
    data = b""
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            break
        data += part
    return data


# --- The store ---------------------------------------------------------------

def _index_file() -> Path:
    return _DIR / "index.json"


def index() -> dict[str, dict[str, Any]]:
    """{ratingKey: {title, year, frames, built_at}} for every track on disk."""
    try:
        data = json.loads(_index_file().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and (_DIR / f"{k}.npy").exists()}


def listing() -> list[dict[str, Any]]:
    """Every track, newest first, for the apps and the tool."""
    rows = [{"rating_key": key, **meta} for key, meta in index().items()]
    return sorted(rows, key=lambda r: r.get("built_at") or 0, reverse=True)


def get(rating_key: str) -> dict[str, Any] | None:
    return index().get(str(rating_key))


def has_track(rating_key: str | None) -> bool:
    return bool(rating_key) and (_DIR / f"{rating_key}.npy").exists()


def load(rating_key: str) -> np.ndarray | None:
    try:
        track = np.load(_DIR / f"{rating_key}.npy")
    except (OSError, ValueError):
        return None
    return track if track.ndim == 2 and track.shape[1] == 3 and len(track) > 1 else None


def find_by_title(title: str) -> str | None:
    """The ratingKey of a stored track whose title matches (TV reports without a key)."""
    from integrations.plex import _title_key
    wanted = _title_key(title)
    for key, meta in index().items():
        if wanted and _title_key(meta.get("title")) == wanted:
            return key
    return None


def save(rating_key: str, track: np.ndarray, title: str, year: Any = None) -> None:
    """Write the track atomically and record it in the index."""
    rating_key = str(rating_key)
    with _lock:
        _DIR.mkdir(parents=True, exist_ok=True)
        tmp = _DIR / f".{rating_key}.npy.tmp"
        with tmp.open("wb") as handle:
            np.save(handle, np.asarray(track, np.uint8))
        tmp.replace(_DIR / f"{rating_key}.npy")
        data = {}
        try:
            data = json.loads(_index_file().read_text())
        except (OSError, ValueError):
            pass
        if not isinstance(data, dict):
            data = {}
        data[rating_key] = {"title": title, "year": year, "frames": int(len(track)), "built_at": time.time()}
        tmp_index = _DIR / "index.json.tmp"
        tmp_index.write_text(json.dumps(data, indent=2))
        tmp_index.replace(_index_file())
    _changed()


# --- Building one ------------------------------------------------------------

def set_notifier(callback: Callable[[str, str, str], None] | None) -> None:
    """``callback(title, detail, severity)`` when a build finishes or fails (the runtime's raise_alert)."""
    global _notifier
    _notifier = callback


def add_listener(callback: Callable[[], None]) -> None:
    """Called on any change the apps should see (progress, a new track)."""
    if callback not in _listeners:
        _listeners.append(callback)


def _changed() -> None:
    for callback in list(_listeners):
        try:
            callback()
        except Exception:  # noqa: BLE001 - a listener never breaks a build
            log.debug("light track listener failed", exc_info=True)


def _notify(title: str, detail: str, severity: str) -> None:
    if _notifier is None:
        log.info("Light track: %s - %s", title, detail)
        return
    try:
        _notifier(title, detail, severity)
    except Exception:  # noqa: BLE001
        log.exception("Light track notifier failed")


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:  # noqa: BLE001 - not installed, or no binary for this platform
        pass
    return shutil.which("ffmpeg")


def ffmpeg_command(exe: str, source: str) -> list[str]:
    """Decode at 10 fps to 64x27 rgb24 on stdout. HTTP reconnects ride out a
    Wi-Fi blip over a two-hour read; audio, subtitles and data are skipped."""
    threads = os.getenv("LIGHT_SYNC_BUILD_THREADS", "4").strip() or "4"
    command = [exe, "-v", "error", "-nostdin", "-hwaccel", "auto", "-threads", threads]
    if source.startswith("http"):
        command += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
    return command + ["-i", source, "-an", "-sn", "-dn",
                      "-vf", f"fps={FPS},scale={W}:{H}:flags=area",
                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]


def building() -> dict[str, Any] | None:
    """The build in progress: {rating_key, title, year, percent}, else None."""
    with _lock:
        if _build is None:
            return None
        return {k: _build[k] for k in ("rating_key", "title", "year", "percent")}


def cancel_build() -> bool:
    with _lock:
        if _build is None:
            return False
        _cancel.set()
        proc = _build.get("proc")
    if proc is not None:
        try:
            proc.terminate()
        except OSError:
            pass
    return True


def start_build(film: dict[str, Any], *, rebuild: bool = False) -> tuple[bool, str]:
    """Build ``film``'s track in the background (a ``plex.movie`` record).
    False with the reason when it can't start (one is building, it exists)."""
    global _build
    key, title = str(film.get("rating_key") or ""), str(film.get("title") or "that film")
    if not key or not film.get("part_key"):
        return False, f"Plex has no file for {title}, sir."
    if has_track(key) and not rebuild:
        return False, f"{title} already has a light sync, sir. Say rebuild if you want it made again."
    exe = ffmpeg_exe()
    if not exe:
        return False, ("I can't build light syncs here, sir: there's no ffmpeg. "
                       "Installing imageio-ffmpeg in Core's venv fixes it.")
    with _lock:
        if _build is not None:
            return False, f"I'm still building {_build['title']} ({_build['percent']}%), sir. One at a time."
        _cancel.clear()
        _build = {"rating_key": key, "title": title, "year": film.get("year"), "percent": 0,
                  "started_at": time.time(), "proc": None}
    threading.Thread(target=_run_build, args=(film, exe), daemon=True, name="light-track-build").start()
    _changed()
    return True, f"Building the light sync for {title}, sir. I'll let you know when it's ready."


def _run_build(film: dict[str, Any], exe: str) -> None:
    global _build
    from integrations import plex
    key, title = str(film["rating_key"]), str(film.get("title") or "that film")
    expected = max(1, int(film.get("duration_ms") or 0) // 100)  # frames at 10 fps
    url = plex.part_url(str(film["part_key"]))
    _, token = plex._config()  # noqa: SLF001 - only to scrub it from ffmpeg's error text
    started = time.monotonic()
    stderr_tail: list[bytes] = []
    try:
        proc = subprocess.Popen(ffmpeg_command(exe, url), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, bufsize=_FRAME_BYTES * 64,
                                preexec_fn=_nice if os.name == "posix" else None)
        with _lock:
            if _build is not None:
                _build["proc"] = proc

        def drain() -> None:  # ffmpeg's errors, bounded, so a full pipe never stalls it
            for line in iter(proc.stderr.readline, b""):
                stderr_tail.append(line)
                del stderr_tail[:-20]

        threading.Thread(target=drain, daemon=True, name="light-track-ffmpeg-err").start()

        def progress(frames: int) -> None:
            if frames % 100:
                return
            percent = min(99, frames * 100 // expected)
            with _lock:
                if _build is not None and percent != _build["percent"]:
                    _build["percent"] = percent
                    moved = True
                else:
                    moved = False
            if moved:
                _changed()

        track = analyze_stream(proc.stdout, progress, _cancel)
        if _cancel.is_set():
            proc.terminate()
        code = proc.wait(timeout=30)
        if _cancel.is_set():
            log.info("Light track for %s cancelled", title)
            return
        if code != 0 or len(track) < max(10, expected // 2):
            why = b"".join(stderr_tail).decode("utf-8", "replace").strip()
            if token:
                why = why.replace(token, "***")
            log.warning("Light track for %s failed: ffmpeg exit %s, %d of ~%d frames. %s",
                        title, code, len(track), expected, why[-500:])
            _notify("Light sync failed", f"I couldn't build the light sync for {title}, sir. "
                    f"The film stopped reading after {len(track) // FPS // 60} minutes. Try again later.",
                    "warning")
            return
        save(key, track, title, film.get("year"))
        minutes = (time.monotonic() - started) / 60
        log.info("Light track for %s: %d frames in %.1f min", title, len(track), minutes)
        _notify("Light sync ready", f"The light sync for {title} is ready, sir. "
                "It'll run on its own when you play it in movie mode.", "info")
    except Exception as exc:  # noqa: BLE001 - a build must never take Core down
        log.exception("Light track for %s crashed", title)
        _notify("Light sync failed", f"I couldn't build the light sync for {title}, sir ({type(exc).__name__}).",
                "warning")
    finally:
        with _lock:
            _build = None
        _cancel.clear()
        _changed()


def _nice() -> None:  # in the ffmpeg child: Core's voice and API come first
    try:
        os.nice(10)
    except OSError:
        pass
