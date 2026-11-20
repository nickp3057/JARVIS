"""The light-track build's worker process: decode and colour a film outside Core.

A build reads every frame of a film (283,000 for a 2.5 h film at 30 fps) and
works out each one's main colour with numpy. Done in a thread inside Core,
that Python work held the interpreter lock for an hour at a time, and the
room voice (one Python loop listening to the desk mic and every iPad) fell
30 s behind: "Hey JARVIS" was heard but never answered in time (2026-11-19).
So the build runs here, in its own process at nice 10, and Core only reads
the result.

    python -m brain.light_track_worker <ffmpeg argv...>

Runs the ffmpeg command (raw 64x27 rgb24 frames on its stdout), and writes
one uint8 RGB colour (3 bytes) per frame to our stdout as it goes. ffmpeg's
stderr passes straight through. Exit code: ffmpeg's; 255 when ffmpeg was
killed by a signal (what ``light_tracks.killed_from_outside`` expects). A
SIGTERM stops ffmpeg first, then ends this process by the same signal.

Only numpy here: no Core imports, no logging to Core's log file.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import IO

import numpy as np

W, H = 64, 27
FRAME_BYTES = W * H * 3
_BINS = 18
_WARM_WHITE = np.array([1.0, 0.85, 0.7], np.float32)
_FLUSH_EVERY = 30  # frames: Core's progress moves about once a second of film


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


def colour_bytes(frame: bytes) -> bytes:
    """A frame's colour as the track stores it: 3 uint8s."""
    return (frame_colour(frame) * 255).clip(0, 255).astype(np.uint8).tobytes()


def read_exact(stream: IO[bytes], size: int) -> bytes:
    data = b""
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            break
        data += part
    return data


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m brain.light_track_worker <ffmpeg argv...>", file=sys.stderr)
        return 2
    ffmpeg = subprocess.Popen(argv, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, bufsize=FRAME_BYTES * 64)

    def stop(signum, _frame):  # noqa: ANN001
        try:
            ffmpeg.terminate()
        except OSError:
            pass
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    out = sys.stdout.buffer
    pending: list[bytes] = []
    try:
        while True:
            frame = read_exact(ffmpeg.stdout, FRAME_BYTES)
            if len(frame) < FRAME_BYTES:
                break
            pending.append(colour_bytes(frame))
            if len(pending) >= _FLUSH_EVERY:
                out.write(b"".join(pending))
                out.flush()
                pending.clear()
        if pending:
            out.write(b"".join(pending))
            out.flush()
    except BrokenPipeError:  # Core went away: stop decoding
        ffmpeg.terminate()
    code = ffmpeg.wait()
    return 255 if code < 0 else code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
