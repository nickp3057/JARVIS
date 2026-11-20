"""Light tracks for light sync: one "main colour" per 0.1 s of a film.

Ported from the light-sync prototype's ``analyze.py`` (2026-11-09). ffmpeg
decodes the film at 10 fps, scaled to 64x27, and each frame becomes one
colour: the biggest vivid hue region (not the frame average), or a warm
white when nothing on screen is vivid. Brightness follows each pixel's
brightest channel, not luma: luma counts pure blue as ~7% bright, so blue
scenes came out black.

Tracks are built on request ("make a light sync for Hunger Games", or the
iPad's Make a light sync button), never for the whole library and never
on their own (a temp-4K copy used to start a quiet two-hour build just by
playing). The decode and colour maths run in a separate worker process
(``brain/light_track_worker.py``) so a build can't starve the room voice. Asking for several films queues them (FIFO, one
ffmpeg at a time); the queue lives in ``memory/light_tracks/queue.json``
so a Core restart resumes it, and the build that was interrupted starts
again from scratch. The Surface (Core's host) has
no NAS mount and no system ffmpeg, so the file is read over HTTP from Plex
(the media Part's URL with the token, never logged) and ffmpeg comes from
the ``imageio-ffmpeg`` package (``shutil.which("ffmpeg")`` as a fallback).

Storage: ``memory/light_tracks/<ratingKey>.npy`` and Part-specific
``<ratingKey>--<part hash>.npy`` (uint8 Nx3), plus an atomic index recording
Media/Part ids, Part key, container start_time_s, fps, title and build time.
New identified Parts decode at 30fps; legacy tracks remain 10fps. A playing
Part that differs from the built Part is decoded separately, never reused
with an assumed alignment between editions. Per-film offset_ms remains an
explicit override and survives a rebuild.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, IO

import numpy as np

from brain import light_track_worker
from jarvis_logger import get_logger

log = get_logger(__name__)

FPS, W, H = 10, 64, 27
_FRAME_BYTES = W * H * 3

_ROOT = Path(__file__).resolve().parents[1]
_DIR = _ROOT / "memory" / "light_tracks"
_lock = threading.RLock()

# The one build that may run: {"rating_key", "title", "year", "percent", "started_at", "proc", "film"}.
_build: dict[str, Any] | None = None
# Films waiting their turn, oldest first: {"film": plex.movie record, "rebuild", "quiet"}.
_queue: list[dict[str, Any]] = []
_stopping = False  # Core is shutting down: keep the interrupted build in the queue file
_interrupts: dict[str, int] = {}  # ratingKey -> times ffmpeg was killed from outside
_MAX_INTERRUPTS = 2
_RETRY_AFTER_S = 30.0
OFFSET_LIMIT_MS = 2000
_cancel = threading.Event()
_notifier: Callable[[str, str, str], None] | None = None
_listeners: list[Callable[[], None]] = []


# --- The colour of one frame (analyze.py, faithfully) ------------------------
# The maths lives in brain/light_track_worker.py, the process that runs it
# during a build; it's the same function here for tests and one-off use.

frame_colour = light_track_worker.frame_colour


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
            return str(meta.get("base_rating_key") or key)
    return None


def save(rating_key: str, track: np.ndarray, title: str, year: Any = None, *, source: dict[str, Any] | None = None) -> None:
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
        old = data.get(rating_key) if isinstance(data.get(rating_key), dict) else {}
        data[rating_key] = {"title": title, "year": year, "frames": int(len(track)), "built_at": time.time(),
                            "offset_ms": clamp_offset(old.get("offset_ms", 0))}
        if source:
            data[rating_key].update({k: source.get(k) for k in ("media_id", "part_id", "part_key", "start_time_s", "base_rating_key", "fps")})
        _write_index(data)
    _changed()


def _write_index(data: dict[str, Any]) -> None:
    tmp_index = _DIR / "index.json.tmp"
    tmp_index.write_text(json.dumps(data, indent=2))
    tmp_index.replace(_index_file())


def clamp_offset(value: Any) -> int:
    """A timing offset in ms, whole, within +/-``OFFSET_LIMIT_MS`` (junk is 0)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number != number or number in (float("inf"), float("-inf")):
        return 0
    return int(round(max(-OFFSET_LIMIT_MS, min(OFFSET_LIMIT_MS, number))))


def film_offset(rating_key: str | None) -> int:
    """This film's own light-sync timing nudge in ms (0 when none or no track)."""
    if not rating_key:
        return 0
    return clamp_offset((get(str(rating_key)) or {}).get("offset_ms", 0))


def set_film_offset(rating_key: str, offset_ms: Any) -> int | None:
    """Store a film's timing nudge (clamped); None when the film has no track."""
    rating_key = str(rating_key)
    value = clamp_offset(offset_ms)
    with _lock:
        try:
            data = json.loads(_index_file().read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get(rating_key), dict):
            return None
        data[rating_key]["offset_ms"] = value
        _write_index(data)
    _changed()
    return value


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


_VAAPI_DEVICE = "/dev/dri/renderD128"
_VAAPI_DRIVERS = ("/usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so", "/usr/lib/x86_64-linux-gnu/dri/i965_drv_video.so")
# HDR to SDR, done on the 64x28 frame (nearly free): without it a 4K HDR
# copy's colours came out washed out (saturation 0.35 vs 0.47 for the 1080p
# SDR copy of the same minute; 0.43 with this, measured on the Surface
# 2026-11-19). zscale needs even sizes, hence 28 then 27.
_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=gbrp")


def build_hwaccel() -> str | None:
    """The hardware decoder for builds, or None for software.
    ``LIGHT_SYNC_HWACCEL``: unset/``auto`` uses VAAPI when the Intel GPU is
    usable (render node readable and a VA driver installed: on the Surface,
    ``intel-media-va-driver`` and the ``render`` group), ``off`` forces
    software, anything else (``vaapi``, ``videotoolbox``) is passed to ffmpeg.
    Never ffmpeg's own ``auto``: imageio-ffmpeg's build tries CUDA first and
    aborts on the Surface (no NVIDIA driver)."""
    choice = os.getenv("LIGHT_SYNC_HWACCEL", "").strip().lower()
    if choice in ("off", "none", "software", "0", "false"):
        return None
    if choice and choice != "auto":
        return choice
    if (os.name == "posix" and os.access(_VAAPI_DEVICE, os.R_OK | os.W_OK)
            and any(Path(p).exists() for p in _VAAPI_DRIVERS)):
        return "vaapi"
    return None


def build_threads() -> str:
    """ffmpeg decode threads: ``LIGHT_SYNC_BUILD_THREADS``, else every core
    (up to 8). The build runs at nice 10, so the voice and API still come
    first; 8 threads decode 4K HEVC ~27% faster than 4 on the Surface."""
    value = os.getenv("LIGHT_SYNC_BUILD_THREADS", "").strip()
    return value or str(min(8, os.cpu_count() or 4))


def ffmpeg_command(exe: str, source: str, fps: int = FPS, *, hdr: bool = False,
                   hwaccel: str | None = None) -> list[str]:
    """Decode at the requested fps to 64x27 rgb24 on stdout. HTTP reconnects ride out a
    Wi-Fi blip over a two-hour read; audio, subtitles and data are skipped.
    ``hdr`` tonemaps PQ/HLG to SDR; ``hwaccel`` decodes on the GPU (frames
    come back to system memory, so the filters are the same)."""
    command = [exe, "-v", "error", "-nostdin"]
    if hwaccel == "vaapi":
        command += ["-hwaccel", "vaapi", "-hwaccel_device", _VAAPI_DEVICE]
    elif hwaccel:
        command += ["-hwaccel", hwaccel]
    command += ["-threads", build_threads()]
    if source.startswith("http"):
        command += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
    vf = (f"fps={fps},scale={W}:{H + H % 2}:flags=area,{_TONEMAP},scale={W}:{H}:flags=area" if hdr
          else f"fps={fps},scale={W}:{H}:flags=area")
    return command + ["-i", source, "-an", "-sn", "-dn", "-vf", vf,
                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]


def is_hdr(probe_text: str) -> bool:
    """ffmpeg's ``-i`` description of a file says PQ (HDR10, Dolby Vision's
    base layer) or HLG on the video stream."""
    return any(t in probe_text for t in ("smpte2084", "arib-std-b67"))


def building() -> dict[str, Any] | None:
    """The build in progress: {rating_key, title, year, percent}, else None."""
    with _lock:
        if _build is None:
            return None
        return {k: _build[k] for k in ("rating_key", "title", "year", "percent")}


def queue() -> list[dict[str, Any]]:
    """The films waiting their turn, next first: [{rating_key, title, year}]."""
    with _lock:
        return [{"rating_key": str(e["film"].get("rating_key")), "title": str(e["film"].get("title") or ""),
                 "year": e["film"].get("year")} for e in _queue]


def pending(rating_key: str | None) -> bool:
    """Building or queued."""
    key = str(rating_key or "")
    with _lock:
        return bool(key) and ((_build is not None and _build["rating_key"] == key)
                              or any(str(e["film"].get("rating_key")) == key for e in _queue))


_QUEUE_FIELDS = ("rating_key", "title", "year", "duration_ms", "part_key", "part_id", "media_id", "start_time_s", "base_rating_key", "fps")


def _queue_file() -> Path:
    return _DIR / "queue.json"


def _persist() -> None:
    """The queue on disk (the build in progress first); call with ``_lock`` held."""
    entries = []
    if _build is not None and _build.get("entry"):
        entries.append(_build["entry"])
    entries += _queue
    rows = [{"film": {k: e["film"].get(k) for k in _QUEUE_FIELDS}, "rebuild": bool(e.get("rebuild")),
             "quiet": bool(e.get("quiet"))} for e in entries]
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        tmp = _DIR / "queue.json.tmp"
        tmp.write_text(json.dumps(rows, indent=2))
        tmp.replace(_queue_file())
    except OSError as exc:
        log.error("Could not save the light track queue: %s", exc)


_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")


def _place(position: int) -> str:
    """1 -> "first in line"; 12 -> "number 12 in line"."""
    return f"{_ORDINALS[position - 1]} in line" if 1 <= position <= len(_ORDINALS) else f"number {position} in line"


def _position(rating_key: str) -> int:
    """1-based place in line counting the build in progress; 0 if absent. ``_lock`` held."""
    ahead = 1 if _build is not None else 0
    for i, entry in enumerate(_queue):
        if str(entry["film"].get("rating_key")) == rating_key:
            return ahead + i + 1
    return 0


def cancel_build() -> bool:
    """Stop the build in progress (the next one in the queue starts)."""
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


def cancel(rating_key: str | None = None, *, everything: bool = False) -> list[str]:
    """Cancel one film (queued or building) or everything; the titles cancelled."""
    key = str(rating_key or "")
    titles: list[str] = []
    with _lock:
        keep = []
        for entry in _queue:
            if everything or str(entry["film"].get("rating_key")) == key:
                titles.append(str(entry["film"].get("title") or "that film"))
            else:
                keep.append(entry)
        _queue[:] = keep
        current = _build is not None and (everything or _build["rating_key"] == key)
        if current:
            titles.insert(0, str(_build["title"]))
        _persist()
    if current:
        cancel_build()
    if titles:
        _changed()
    return titles


def start_build(film: dict[str, Any], *, rebuild: bool = False, front: bool = False,
                quiet: bool = False) -> tuple[bool, str]:
    """Build ``film``'s track in the background (a ``plex.movie`` record), or
    queue it behind the one building. ``front`` jumps the queue (the
    calibration clip); ``quiet`` skips the "ready" push (failures still
    alert). False with the reason when it isn't added (no file, already has
    a track, already queued)."""
    key, title = str(film.get("rating_key") or ""), str(film.get("title") or "that film")
    if not key or not film.get("part_key"):
        return False, f"Plex has no file for {title}, sir."
    if has_track(key) and not rebuild:
        return False, f"{title} already has a light sync, sir. Say rebuild if you want it made again."
    if not ffmpeg_exe():
        return False, ("I can't build light syncs here, sir: there's no ffmpeg. "
                       "Installing imageio-ffmpeg in Core's venv fixes it.")
    entry = {"film": dict(film), "rebuild": bool(rebuild), "quiet": bool(quiet)}
    with _lock:
        if _build is not None and _build["rating_key"] == key:
            return False, f"I'm already building {title} ({_build['percent']}%), sir."
        place = _position(key)
        if place:
            if not front or place <= 2:
                return False, f"{title} is already in the queue, sir: {_place(place)}."
            _queue[:] = [e for e in _queue if str(e["film"].get("rating_key")) != key]
        if front:
            _queue.insert(0, entry)
        else:
            _queue.append(entry)
        place = _position(key)
        _persist()
    started = _pump()
    _changed()
    if started == key:
        return True, f"Building the light sync for {title}, sir. I'll let you know when it's ready."
    return True, f"Queued {title}, sir: {_place(place)}."


def resume() -> int:
    """Core started: pick the queue back up from ``queue.json`` (the build
    that was interrupted starts again from scratch). The number queued."""
    try:
        rows = json.loads(_queue_file().read_text())
    except (OSError, ValueError):
        return 0
    if not isinstance(rows, list):
        return 0
    added = 0
    with _lock:
        known = {str(e["film"].get("rating_key")) for e in _queue}
        if _build is not None:
            known.add(_build["rating_key"])
        for row in rows:
            film = row.get("film") if isinstance(row, dict) else None
            if not isinstance(film, dict) or not film.get("rating_key") or not film.get("part_key"):
                continue
            key = str(film["rating_key"])
            if key in known or (has_track(key) and not row.get("rebuild")):
                continue
            if row.get("quiet") and film.get("base_rating_key"):
                # A copy's build light sync queued on its own before 2026-11-19
                # (nobody asked; Part builds from the iPad aren't quiet): drop it.
                log.info("Light track for %s (another copy) dropped: builds only start on request",
                         film.get("title") or key)
                continue
            known.add(key)
            _queue.append({"film": film, "rebuild": bool(row.get("rebuild")), "quiet": bool(row.get("quiet"))})
            added += 1
        _persist()
    if added:
        log.info("Light track queue resumed: %d film(s)", added)
        _pump()
        _changed()
    return added


def _pump() -> str | None:
    """Start the next queued build if none is running; its ratingKey, else None."""
    global _build
    if _stopping:
        return None
    exe = ffmpeg_exe()
    if not exe:
        return None
    with _lock:
        while _build is None and _queue:
            entry = _queue.pop(0)
            film = entry["film"]
            key = str(film.get("rating_key"))
            if has_track(key) and not entry.get("rebuild"):
                continue  # built meanwhile
            _cancel.clear()
            _build = {"rating_key": key, "title": str(film.get("title") or "that film"), "year": film.get("year"),
                      "percent": 0, "started_at": time.time(), "proc": None, "entry": entry}
            _persist()
            threading.Thread(target=_run_build, args=(film, exe, bool(entry.get("quiet"))), daemon=True,
                             name="light-track-build").start()
            return key
        _persist()
    return None


def shutdown() -> None:
    """Core is stopping: end ffmpeg but keep the build in the queue file, so
    the next start builds it again."""
    global _stopping
    _stopping = True
    with _lock:
        proc = _build.get("proc") if _build is not None else None
        if _build is not None:
            _cancel.set()
    if proc is not None:
        try:
            proc.terminate()
        except OSError:
            pass


def _run_build(film: dict[str, Any], exe: str, quiet: bool = False) -> None:
    global _build
    from integrations import plex
    key, title = str(film["rating_key"]), str(film.get("title") or "that film")
    film["fps"] = 30 if film.get("part_id") else FPS
    expected = max(1, int(film.get("duration_ms") or 0) * film["fps"] // 1000)
    url = plex.part_url(str(film["part_key"]))
    _, token = plex._config()  # noqa: SLF001 - only to scrub it from ffmpeg's error text
    started = time.monotonic()
    stderr_tail: list[bytes] = []
    requeue = False
    hdr = False
    try:
        if film.get("part_id"):
            try:
                probe = subprocess.run([exe, "-hide_banner", "-i", url], capture_output=True, timeout=30)
            except subprocess.TimeoutExpired:
                # TimeoutExpired includes argv (and the Plex token) in its text.
                raise RuntimeError("Plex media probe timed out") from None
            import re
            described = probe.stderr.decode("utf-8", "replace")
            start = re.search(r"start: (-?[0-9.]+)", described)
            film["start_time_s"] = float(start.group(1)) if start else None
            hdr = is_hdr(described)

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

        def decode(accel: str | None) -> tuple[int | None, np.ndarray]:
            # The decode and the colour maths run in their own process
            # (brain/light_track_worker.py): in a thread here they held Core's
            # interpreter lock for the whole film and the room voice fell behind.
            stderr_tail.clear()
            proc = subprocess.Popen(worker_command(ffmpeg_command(exe, url, film["fps"], hdr=hdr, hwaccel=accel)),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                    cwd=str(_ROOT), preexec_fn=_nice if os.name == "posix" else None)
            with _lock:
                if _build is not None:
                    _build["proc"] = proc

            def drain() -> None:  # ffmpeg's errors, bounded, so a full pipe never stalls it
                for line in iter(proc.stderr.readline, b""):
                    stderr_tail.append(line)
                    del stderr_tail[:-20]

            threading.Thread(target=drain, daemon=True, name="light-track-ffmpeg-err").start()
            frames = read_track(proc.stdout, progress, _cancel)
            if _cancel.is_set():
                proc.terminate()
            return proc.wait(timeout=30), frames

        accel = build_hwaccel()
        code, track = decode(accel)
        if accel and code != 0 and not len(track) and not _cancel.is_set() \
                and not killed_from_outside(code, b"".join(stderr_tail).decode("utf-8", "replace")):
            log.warning("Light track for %s: %s decode failed (exit %s); building in software instead",
                        title, accel, code)
            accel = None
            code, track = decode(None)
        if _cancel.is_set():
            log.info("Light track for %s cancelled", title)
            return
        why = b"".join(stderr_tail).decode("utf-8", "replace").strip()
        if killed_from_outside(code, why) and _interrupts.get(key, 0) < _MAX_INTERRUPTS:
            # systemd stops ffmpeg with Core on a restart (exit 255, no error
            # text), often before Core's own shutdown hook runs: not the film's
            # fault. Keep it at the front of the queue, quietly.
            _interrupts[key] = _interrupts.get(key, 0) + 1
            log.info("Light track for %s interrupted (ffmpeg exit %s at %d of ~%d frames); kept in the queue",
                     title, code, len(track), expected)
            requeue = True
            return
        if code != 0 or len(track) < max(10, expected // 2):
            if token:
                why = why.replace(token, "***")
            log.warning("Light track for %s failed: ffmpeg exit %s, %d of ~%d frames. %s",
                        title, code, len(track), expected, why[-500:])
            _notify("Light sync failed", f"I couldn't build the light sync for {title}, sir. "
                    f"The film stopped reading after {len(track) // film['fps'] // 60} minutes. Try again later.",
                    "warning")
            return
        save(key, track, title, film.get("year"), source=film)
        minutes = (time.monotonic() - started) / 60
        log.info("Light track for %s: %d frames in %.1f min (%s decode%s)", title, len(track), minutes,
                 accel or "software", ", HDR tonemapped" if hdr else "")
        if not quiet:
            _notify("Light sync ready", f"The light sync for {title} is ready, sir. "
                    "It'll run on its own when you play it in movie mode.", "info")
    except Exception as exc:  # noqa: BLE001 - a build must never take Core down
        log.exception("Light track for %s crashed", title)
        _notify("Light sync failed", f"I couldn't build the light sync for {title}, sir ({type(exc).__name__}).",
                "warning")
    finally:
        with _lock:
            entry = (_build or {}).get("entry") or {"film": film, "rebuild": False, "quiet": quiet}
            _build = None
            if requeue:
                _queue.insert(0, entry)
            if not _stopping:
                _persist()  # at shutdown the file keeps it, so the next start builds it again
        if requeue:
            # Later, not now: if Core is going down, it's gone by then and the
            # queue file brings the film back on the next start.
            retry = threading.Timer(_RETRY_AFTER_S, _pump)
            retry.daemon = True
            retry.start()
        elif not _stopping:
            _cancel.clear()
            _pump()
        _changed()


def killed_from_outside(code: int | None, stderr_text: str) -> bool:
    """ffmpeg ended by a signal rather than a read or decode error: a
    negative return code, or 255 with nothing on stderr (how it exits on
    SIGTERM/SIGINT at ``-v error``)."""
    return code is not None and (code < 0 or (code == 255 and not stderr_text.strip()))


def worker_command(ffmpeg: list[str]) -> list[str]:
    """Run ``ffmpeg`` under the build worker, with Core's own Python."""
    return [sys.executable, "-m", "brain.light_track_worker", *ffmpeg]


def read_track(stream: IO[bytes], on_frame: Callable[[int], None] | None = None,
               cancel: threading.Event | None = None) -> np.ndarray:
    """The worker's output (3 bytes per frame) -> uint8 N x 3 track.
    Reads in big chunks: Core only counts frames, the worker does the work."""
    data = bytearray()
    reported = 0
    while not (cancel and cancel.is_set()):
        chunk = stream.read(3 * 300)
        if not chunk:
            break
        data += chunk
        frames = len(data) // 3
        if on_frame:
            for mark in range(reported + 100 - reported % 100, frames + 1, 100):
                on_frame(mark)
        reported = frames
    usable = len(data) - len(data) % 3
    return np.frombuffer(bytes(data[:usable]), np.uint8).reshape(-1, 3).copy()


def _nice() -> None:  # in the build worker (and its ffmpeg): Core's voice and API come first
    try:
        os.nice(10)
    except OSError:
        pass
