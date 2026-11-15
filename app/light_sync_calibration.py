"""Build a deterministic three-minute, 30fps Plex calibration film.

Run on the Mac: .venv/bin/python -m app.light_sync_calibration /tmp/Light-Sync-Test.mp4
The adjacent JSON records exact cut frames; intervals are irregular, with
at most two brightness changes in any second, including a loop boundary.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import subprocess

from brain.light_tracks import ffmpeg_exe

FPS = 30
COLOURS = ((0, 0, 0), (255, 255, 255), (0, 0, 0), (255, 0, 0),
           (0, 0, 0), (0, 255, 0), (0, 0, 0), (0, 0, 255))


def timeline(seconds: int = 180) -> list[dict]:
    rng = random.Random(15112026)
    result, frame, i = [], 0, 0
    while frame < seconds * FPS:
        # Every colour is held >=1.3s; the final black tail makes looping unambiguous.
        length = min(rng.choice((43, 59, 71, 89, 107, 131)), seconds * FPS - frame)
        rgb = COLOURS[i % len(COLOURS)] if frame + length < seconds * FPS else COLOURS[0]
        result.append({"frame": frame, "frames": length, "rgb": list(rgb)})
        frame, i = frame + length, i + 1
    return result


def build(path: Path, seconds: int = 180) -> None:
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg is required")
    cuts = timeline(seconds)
    command = [exe, "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", "320x180", "-r", str(FPS), "-i", "pipe:0", "-f", "lavfi", "-i",
               "anullsrc=r=48000:cl=stereo", "-t", str(seconds), "-vf", "scale=1920:1080:flags=neighbor",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-movflags", "+faststart", str(path)]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as proc:
        try:
            for cut in cuts:
                frame = bytes(cut["rgb"]) * (320 * 180)
                for _ in range(cut["frames"]):
                    proc.stdin.write(frame)
        finally:
            proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("Calibration encode failed")
    path.with_suffix(".json").write_text(json.dumps({"fps": FPS, "seconds": seconds, "cuts": cuts}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.output)
