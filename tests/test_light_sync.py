"""Light sync (brain/light_tracks.py, brain/light_sync.py): no real lights,
Plex, Home Assistant or films. A tiny generated clip goes through ffmpeg only
when ffmpeg exists on this machine."""
import io
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from fastapi.testclient import TestClient

from app import client_tokens
import app.server as server
from brain import light_sync, light_track_worker, light_tracks, tools
from speech import quick_commands


def _reference_colour(b: bytes) -> np.ndarray:
    """The prototype's analyze.py per-frame maths, verbatim."""
    BINS = 18
    px = np.frombuffer(b, np.uint8).reshape(-1, 3).astype(np.float32) / 255
    mx, mn = px.max(1), px.min(1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    vivid = (sat > 0.3) & (mx > 0.15)
    if vivid.sum() >= px.shape[0] * 0.03:
        r, g, bl = px[:, 0], px[:, 1], px[:, 2]
        d = np.maximum(mx - mn, 1e-6)
        hue = np.where(mx == r, ((g - bl) / d) % 6, np.where(mx == g, (bl - r) / d + 2, (r - g) / d + 4)) / 6
        hb = (hue * BINS).astype(int) % BINS
        w = sat * mx * vivid
        hist = np.bincount(hb, weights=w, minlength=BINS)
        hist = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))
        best = int(hist.argmax())
        sel = vivid & ((hb == best) | (hb == (best + 1) % BINS) | (hb == (best - 1) % BINS))
        col = (px[sel] * w[sel, None]).sum(0) / max(w[sel].sum(), 1e-6)
        col = col / max(col.max(), 1e-6)
    else:
        col = np.array([1.0, 0.85, 0.7])
    level = min(1.0, 0.08 + float(mx.mean()) * 1.4)
    return np.append(col * level, 0)[:3]


def _frame(rgb, size=light_tracks.W * light_tracks.H) -> bytes:
    return bytes(rgb) * size


class TrackStoreDir(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "light_tracks"
        p = patch.object(light_tracks, "_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        s = patch.object(light_sync, "_STATE_FILE", Path(tmp.name) / "light_sync.json")
        s.start()
        self.addCleanup(s.stop)
        light_tracks._queue.clear()
        light_tracks._stopping = False
        self.addCleanup(light_tracks._queue.clear)


class FrameColourTests(unittest.TestCase):
    def test_matches_the_prototype_on_random_and_plain_frames(self) -> None:
        rng = np.random.default_rng(7)
        frames = [rng.integers(0, 256, light_tracks._FRAME_BYTES, dtype=np.uint8).tobytes() for _ in range(20)]
        frames += [_frame((255, 0, 0)), _frame((0, 0, 0)), _frame((0, 0, 255)), _frame((128, 128, 128))]
        for b in frames:
            np.testing.assert_allclose(light_tracks.frame_colour(b), _reference_colour(b), atol=1e-5)

    def test_blue_is_not_black_and_grey_is_warm_white(self) -> None:
        blue = light_tracks.frame_colour(_frame((0, 0, 255)))
        self.assertAlmostEqual(float(blue[2]), 1.0, places=4)
        grey = light_tracks.frame_colour(_frame((100, 100, 100)))
        self.assertGreater(grey[0], grey[1])
        self.assertGreater(grey[1], grey[2])

    def test_biggest_vivid_region_wins_not_the_average(self) -> None:
        n = light_tracks.W * light_tracks.H
        frame = bytes((255, 0, 0)) * int(n * 0.6) + bytes((0, 255, 0)) * (n - int(n * 0.6))
        colour = light_tracks.frame_colour(frame)
        self.assertGreater(colour[0], 0.9)
        self.assertLess(colour[1], 0.05)

    def test_analyze_stream_drops_a_partial_frame_and_honours_cancel(self) -> None:
        data = _frame((255, 0, 0)) * 3 + b"\x00" * 10
        track = light_tracks.analyze_stream(io.BytesIO(data))
        self.assertEqual(track.shape, (3, 3))
        self.assertEqual(track.dtype, np.uint8)
        self.assertEqual(list(track[0]), [255, 0, 0])
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(len(light_tracks.analyze_stream(io.BytesIO(data), cancel=cancel)), 0)

    def test_ffmpeg_hwaccel_only_when_asked(self) -> None:
        command = light_tracks.ffmpeg_command("ffmpeg", "/tmp/film.mkv", hwaccel="vaapi")
        self.assertEqual(command[command.index("-hwaccel") + 1], "vaapi")
        self.assertEqual(command[command.index("-hwaccel_device") + 1], "/dev/dri/renderD128")
        self.assertLess(command.index("-hwaccel"), command.index("-i"))

    def test_build_hwaccel_picks_vaapi_only_when_the_gpu_is_usable(self) -> None:
        with patch.dict(os.environ, {"LIGHT_SYNC_HWACCEL": ""}), \
                patch.object(light_tracks.os, "access", return_value=True), \
                patch.object(light_tracks.Path, "exists", return_value=True):
            self.assertEqual(light_tracks.build_hwaccel(), "vaapi")
        with patch.dict(os.environ, {"LIGHT_SYNC_HWACCEL": "auto"}), \
                patch.object(light_tracks.os, "access", return_value=False):
            self.assertIsNone(light_tracks.build_hwaccel())  # no render group: software
        with patch.dict(os.environ, {"LIGHT_SYNC_HWACCEL": "off"}), \
                patch.object(light_tracks.os, "access", return_value=True):
            self.assertIsNone(light_tracks.build_hwaccel())
        with patch.dict(os.environ, {"LIGHT_SYNC_HWACCEL": "videotoolbox"}):
            self.assertEqual(light_tracks.build_hwaccel(), "videotoolbox")

    def test_hdr_is_tonemapped_on_the_small_frame(self) -> None:
        self.assertTrue(light_tracks.is_hdr("Video: hevc (Main 10), yuv420p10le(tv, bt2020nc/bt2020/smpte2084)"))
        self.assertFalse(light_tracks.is_hdr("Video: h264 (High), yuv420p(tv, bt709)"))
        vf = light_tracks.ffmpeg_command("ffmpeg", "/tmp/f.mkv", 30, hdr=True)
        vf = vf[vf.index("-vf") + 1]
        self.assertTrue(vf.startswith("fps=30,scale=64:28:flags=area,zscale=t=linear"))
        self.assertTrue(vf.endswith("format=gbrp,scale=64:27:flags=area"))

    def test_build_threads_default_to_the_cores(self) -> None:
        with patch.dict(os.environ, {"LIGHT_SYNC_BUILD_THREADS": ""}), \
                patch.object(light_tracks.os, "cpu_count", return_value=8):
            self.assertEqual(light_tracks.build_threads(), "8")
        with patch.dict(os.environ, {"LIGHT_SYNC_BUILD_THREADS": "3"}):
            self.assertEqual(light_tracks.build_threads(), "3")

    def test_ffmpeg_command_reads_http_decodes_at_10fps_without_audio(self) -> None:
        command = light_tracks.ffmpeg_command("ffmpeg", "http://plex/library/parts/1/file.mkv?X-Plex-Token=t")
        joined = " ".join(command)
        self.assertNotIn("-hwaccel", command)  # CUDA probe aborts ffmpeg on the Surface
        self.assertIn("fps=10,scale=64:27:flags=area", joined)
        self.assertIn("-reconnect 1", joined)
        for flag in ("-an", "-sn", "-pix_fmt"):
            self.assertIn(flag, command)
        self.assertNotIn("videotoolbox", joined)

    @unittest.skipUnless(light_tracks.ffmpeg_exe(), "no ffmpeg here")
    def test_a_tiny_generated_clip_through_real_ffmpeg(self) -> None:
        exe = light_tracks.ffmpeg_exe()
        with tempfile.TemporaryDirectory() as tmp:
            clip = os.path.join(tmp, "red.mp4")
            subprocess.run([exe, "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=128x54:d=1:r=24",
                            "-pix_fmt", "yuv420p", clip], check=True, timeout=60)
            proc = subprocess.run(light_tracks.ffmpeg_command(exe, clip), capture_output=True, timeout=60)
        track = light_tracks.analyze_stream(io.BytesIO(proc.stdout))
        self.assertIn(len(track), range(9, 12))
        self.assertGreater(int(track[5][0]), 200)
        self.assertLess(int(track[5][1]), 40)


class TrackStoreTests(TrackStoreDir):
    def test_save_load_index_and_title_lookup(self) -> None:
        track = np.array([[1, 2, 3], [4, 5, 6]], np.uint8)
        light_tracks.save("42", track, "The Hunger Games", 2012)
        self.assertTrue(light_tracks.has_track("42"))
        np.testing.assert_array_equal(light_tracks.load("42"), track)
        self.assertEqual(light_tracks.get("42")["frames"], 2)
        self.assertEqual(light_tracks.listing()[0]["rating_key"], "42")
        self.assertEqual(light_tracks.find_by_title("hunger games"), "42")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])
        self.assertFalse(light_tracks.has_track(None))


def _worker_output(frames: bytes) -> bytes:
    """What brain/light_track_worker.py writes for these raw frames: 3 bytes each."""
    size = light_track_worker.FRAME_BYTES
    return b"".join(light_track_worker.colour_bytes(frames[i:i + size]) for i in range(0, len(frames), size))


class _FakeProc:
    """The build worker: ``data`` is the raw frames ffmpeg would decode."""

    def __init__(self, data: bytes, code: int = 0, err: bytes = b"") -> None:
        self.stdout = io.BytesIO(_worker_output(data))
        self.stderr = io.BytesIO(err)
        self.code = code
        self.terminated = False

    def wait(self, timeout=None):  # noqa: ANN001
        return self.code

    def terminate(self) -> None:
        self.terminated = True


class BuildBase(TrackStoreDir):
    FILM = {"rating_key": "7", "title": "Cars 2", "year": 2011, "duration_ms": 2000, "part_key": "/library/parts/9/f.mkv"}

    def setUp(self) -> None:
        super().setUp()
        self.notes = []
        light_tracks.set_notifier(lambda *a: self.notes.append(a))
        self.addCleanup(light_tracks.set_notifier, None)
        for p in (patch.object(light_tracks, "ffmpeg_exe", return_value="/bin/ffmpeg"),
                  patch.object(light_tracks, "build_hwaccel", return_value=None),
                  patch.dict(os.environ, {"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "sekrit"})):
            p.start()
            self.addCleanup(p.stop)

    def _wait(self) -> None:
        for _ in range(200):
            if light_tracks.building() is None:
                return
            time.sleep(0.01)
        self.fail("build never finished")


class BuildTests(BuildBase):
    def test_builds_saves_and_alerts_ready(self) -> None:
        proc = _FakeProc(_frame((0, 0, 255)) * 20)
        with patch.object(light_tracks.subprocess, "Popen", return_value=proc) as popen:
            ok, message = light_tracks.start_build(self.FILM)
            self._wait()
        self.assertTrue(ok)
        self.assertIn("Cars 2", message)
        self.assertIn("http://plex:32400/library/parts/9/f.mkv?X-Plex-Token=sekrit", popen.call_args.args[0])
        self.assertEqual(len(light_tracks.load("7")), 20)
        self.assertEqual(self.notes[-1][0], "Light sync ready")
        self.assertEqual(self.notes[-1][2], "info")

    def test_a_failed_gpu_decode_falls_back_to_software(self) -> None:
        procs = [_FakeProc(b"", code=1, err=b"Failed to initialise VAAPI connection\n"),
                 _FakeProc(_frame((0, 0, 255)) * 20)]
        with patch.object(light_tracks, "build_hwaccel", return_value="vaapi"), \
                patch.object(light_tracks.subprocess, "Popen", side_effect=procs) as popen, \
                self.assertLogs("jarvis.brain.light_tracks", level="INFO") as logs:
            light_tracks.start_build(self.FILM)
            self._wait()
        self.assertIn("-hwaccel", popen.call_args_list[0].args[0])
        self.assertNotIn("-hwaccel", popen.call_args_list[1].args[0])
        self.assertEqual(len(light_tracks.load("7")), 20)
        self.assertIn("software decode", " ".join(logs.output))

    def test_failure_alerts_in_persona_and_never_leaks_the_token(self) -> None:
        proc = _FakeProc(_frame((0, 0, 255)) * 2, code=1, err=b"http://plex?X-Plex-Token=sekrit: 404\n")
        with patch.object(light_tracks.subprocess, "Popen", return_value=proc), \
                self.assertLogs("jarvis.brain.light_tracks", level="WARNING") as logs:
            light_tracks.start_build(self.FILM)
            self._wait()
        self.assertFalse(light_tracks.has_track("7"))
        self.assertEqual(self.notes[-1][0], "Light sync failed")
        self.assertIn("sir", self.notes[-1][1])
        self.assertNotIn("sekrit", " ".join(logs.output))

    def test_a_restart_killing_ffmpeg_keeps_the_film_queued_quietly(self) -> None:
        light_tracks._interrupts.clear()
        self.addCleanup(light_tracks._interrupts.clear)
        proc = _FakeProc(_frame((0, 0, 255)) * 5, code=255, err=b"")  # SIGTERM from systemd
        with patch.object(light_tracks.subprocess, "Popen", return_value=proc), \
                patch.object(light_tracks, "_RETRY_AFTER_S", 60.0):
            light_tracks.start_build(self.FILM)
            self._wait()
        self.assertEqual(self.notes, [])  # no "failed" push
        self.assertEqual([e["film"]["rating_key"] for e in light_tracks._queue], ["7"])
        light_tracks._queue.clear()

    def test_a_film_that_keeps_getting_killed_fails_in_the_end(self) -> None:
        light_tracks._interrupts.clear()
        self.addCleanup(light_tracks._interrupts.clear)
        light_tracks._interrupts["7"] = light_tracks._MAX_INTERRUPTS
        proc = _FakeProc(_frame((0, 0, 255)) * 5, code=255, err=b"")
        with patch.object(light_tracks.subprocess, "Popen", return_value=proc), \
                self.assertLogs("jarvis.brain.light_tracks", level="WARNING"):
            light_tracks.start_build(self.FILM)
            self._wait()
        self.assertEqual(self.notes[-1][0], "Light sync failed")

    def test_killed_from_outside(self) -> None:
        self.assertTrue(light_tracks.killed_from_outside(255, ""))
        self.assertTrue(light_tracks.killed_from_outside(-15, "anything"))
        self.assertFalse(light_tracks.killed_from_outside(255, "http error 404"))
        self.assertFalse(light_tracks.killed_from_outside(1, ""))

    def test_existing_track_and_a_second_film_queues(self) -> None:
        light_tracks.save("7", np.zeros((5, 3), np.uint8), "Cars 2")
        ok, message = light_tracks.start_build(self.FILM)
        self.assertFalse(ok)
        self.assertIn("already has a light sync", message)
        gate = threading.Event()

        class Slow(_FakeProc):
            def __init__(self) -> None:
                super().__init__(b"")
                self.stdout = Mock()
                self.stdout.read = lambda n: (gate.wait(2), b"")[1]

        with patch.object(light_tracks.subprocess, "Popen", return_value=Slow()):
            self.assertTrue(light_tracks.start_build(self.FILM, rebuild=True)[0])
            ok, message = light_tracks.start_build({**self.FILM, "rating_key": "8", "title": "Up"})
            self.assertTrue(ok)
            self.assertEqual(message, "Queued Up, sir: second in line.")
            self.assertEqual(light_tracks.building()["title"], "Cars 2")
            self.assertEqual(light_tracks.cancel(everything=True), ["Cars 2", "Up"])
            gate.set()
            self._wait()
        self.assertEqual(self.notes, [])  # a cancel says nothing

    def test_no_ffmpeg_says_how_to_fix_it(self) -> None:
        with patch.object(light_tracks, "ffmpeg_exe", return_value=None):
            ok, message = light_tracks.start_build(self.FILM)
        self.assertFalse(ok)
        self.assertIn("imageio-ffmpeg", message)


class FlashLimiterTests(unittest.TestCase):
    @staticmethod
    def _big_jumps(outs, times, jump=64.0):
        """Runs (one direction) that move more than ``jump``, by start time."""
        starts, anchor, direction, counted = [], outs[0], 0, False
        for prev, cur, t in zip(outs, outs[1:], times[1:]):
            d = cur - prev
            if abs(d) > 0.5 and (1 if d > 0 else -1) != direction:
                anchor, direction, counted = prev, (1 if d > 0 else -1), False
            if abs(cur - anchor) > jump and not counted:
                starts.append(t)
                counted = True
        return starts

    def test_a_strobe_gets_at_most_three_big_jumps_a_second(self) -> None:
        limiter = light_sync.FlashLimiter(3)
        outs, times = [], []
        for k in range(300):  # 10 s of a 15 Hz black/white strobe at 30 fps
            v = 255.0 if (k // 2) % 2 == 0 else 0.0
            t = k / 30
            outs.append(float(limiter.apply(np.array([v, v, v]), t).max()))
            times.append(t)
        starts = self._big_jumps(outs, times)
        self.assertGreater(len(starts), 5)
        for i, t in enumerate(starts):
            self.assertLessEqual(sum(1 for s in starts[i:] if s - t < 1.0), 3)

    def test_slow_fades_and_hue_changes_pass_untouched(self) -> None:
        limiter = light_sync.FlashLimiter(3)
        for k in range(60):
            v = k * 4.0
            np.testing.assert_allclose(limiter.apply(np.array([v, 0, 0]), k / 30), [v, 0, 0])
        limiter = light_sync.FlashLimiter(3)
        for k in range(60):
            colour = np.array([200.0, 0, 0]) if k % 2 else np.array([0, 0, 200.0])
            np.testing.assert_allclose(limiter.apply(colour, k / 30), colour)

    def test_held_jump_keeps_its_hue(self) -> None:
        limiter = light_sync.FlashLimiter(per_second=0)
        limiter.apply(np.array([0.0, 0, 0]), 0)
        out = limiter.apply(np.array([0.0, 255, 0]), 0.03)
        np.testing.assert_allclose(out, [0, 64, 0])


class ClockTests(TrackStoreDir):
    def setUp(self) -> None:
        super().setUp()
        self.t = 100.0
        self.clock = light_sync.PlaybackClock(now=lambda: self.t)

    def test_tv_report_interpolates_and_wins_while_fresh(self) -> None:
        self.clock.plex_report({"rating_key": "1", "title": "Up", "state": "playing", "view_offset_ms": 50_000})
        self.clock.tv_report(state="playing", position_ms=60_000, title="Up", rating_key="1")
        self.t += 1.5
        pb = self.clock.current()
        self.assertEqual(pb.source, "tv")
        self.assertAlmostEqual(pb.position(self.t), 61.5)
        self.t += 2.0  # TV stale: never fall back to the coarse Plex clock
        self.assertIsNone(self.clock.current())
        self.t += 20  # nothing fresh at all
        self.assertIsNone(self.clock.current())

    def test_paused_holds_and_stays_fresh_longer(self) -> None:
        self.clock.tv_report(state="paused", position_ms=30_000, title="Up", rating_key="1")
        self.t += 10
        pb = self.clock.current()
        self.assertFalse(pb.moving)
        self.assertEqual(pb.position(self.t), 30.0)

    def test_stopped_tv_means_nothing_playing(self) -> None:
        self.clock.plex_report({"rating_key": "1", "title": "Up", "state": "playing", "view_offset_ms": 1})
        self.clock.tv_report(state="stopped", position_ms=0, title="Up")
        self.assertIsNone(self.clock.current())

    def test_coarse_plex_offsets_only_reanchor_when_they_disagree(self) -> None:
        session = {"rating_key": "1", "title": "Up", "state": "playing", "view_offset_ms": 10_000}
        self.clock.plex_report(session)
        self.t += 5
        self.clock.plex_report({**session, "view_offset_ms": 14_000})  # stale by 1 s: keep interpolating
        self.assertAlmostEqual(self.clock.current().position(self.t), 15.0)
        self.clock.plex_report({**session, "view_offset_ms": 60_000})  # a seek
        self.assertAlmostEqual(self.clock.current().position(self.t), 60.0)

    def test_tv_without_a_key_takes_plex_or_the_track_index(self) -> None:
        self.clock.plex_report({"rating_key": "5", "title": "The Hunger Games", "state": "playing",
                                "view_offset_ms": 0})
        self.clock.tv_report(state="playing", position_ms=1000, title="Hunger Games")
        self.assertEqual(self.clock.current().rating_key, "5")
        light_tracks.save("9", np.zeros((3, 3), np.uint8), "Cars 2")
        self.clock.plex_report(None)
        self.clock.tv_report(state="playing", position_ms=1000, title="Cars 2")
        self.assertEqual(self.clock.current().rating_key, "9")

    def test_pick_session(self) -> None:
        tv = {"type": "movie", "rating_key": "1", "player": {"title": "Nick's Room TV", "product": "Plex for Android (TV)",
                                                              "machine_id": "abc"}}
        other = {"type": "movie", "rating_key": "2", "player": {"title": "iPad", "product": "Plex", "machine_id": "x"}}
        song = {"type": "track", "rating_key": "3", "player": {}}
        self.assertIs(light_sync.pick_session([tv, song]), tv)
        self.assertIsNone(light_sync.pick_session([tv, other]))
        self.assertIs(light_sync.pick_session([other, tv], "abc"), tv)
        self.assertIs(light_sync.pick_session([other, tv], "room tv"), tv)
        self.assertIsNone(light_sync.pick_session([other], "abc"))


class FakeSender:
    def __init__(self) -> None:
        self.log = []

    def wake(self, ips):  # noqa: ANN001
        self.log.append(("wake", tuple(ips)))

    def enable(self, ips):  # noqa: ANN001
        self.log.append(("enable", tuple(ips)))

    def disable(self, ips):  # noqa: ANN001
        self.log.append(("disable", tuple(ips)))

    def strip(self, ip, rgb, segments):  # noqa: ANN001
        self.log.append(("strip", ip, tuple(rgb)))

    def lamp(self, ip, rgb):  # noqa: ANN001
        self.log.append(("lamp", ip, tuple(rgb)))


def _tune(**over):
    base = dict(strips=("s1", "s2"), lamp="l1", segments=15, lead_ms=0, blend_ms=80, lamp_lead_ms=150,
                strip_level=0.35, lamp_level=0.6, jumps_per_s=3)
    base.update(over)
    return light_sync.Tuning(**base)


class GoveeTests(unittest.TestCase):
    def test_packets(self) -> None:
        self.assertEqual(light_sync.razer_packet(light_sync.DISABLE), "uwABsQAL")
        self.assertEqual(light_sync.razer_packet(light_sync.ENABLE), "uwABsQEK")
        sock = Mock()
        lan = light_sync.GoveeLan(sock)
        lan.disable(("10.9.9.9",))
        self.assertEqual(sock.sendto.call_count, 3)
        self.assertEqual(sock.sendto.call_args.args[1], ("10.9.9.9", 4003))
        sock.sendto.side_effect = OSError("down")
        lan.lamp("10.9.9.8", [1, 2, 3])  # never raises
        self.assertEqual(lan.errors["10.9.9.8"], 1)


class PlayerTests(unittest.TestCase):
    def _run(self, clock, *, tune=None, seconds=0.25):
        sender = FakeSender()
        track = np.tile(np.array([[200, 0, 0], [0, 0, 200]], np.uint8), (300, 1))
        exited = []
        player = light_sync.Player(track, "1", clock, tune or _tune, sender, on_exit=exited.append)
        player.start()
        time.sleep(seconds)
        player.stop()
        return sender, player, exited

    def test_live_strip_change_remains_limited_and_releases_every_target(self):
        calls = 0
        def tune():
            nonlocal calls
            calls += 1
            return _tune(strips=("old",) if calls == 1 else ("new",), lamp="")
        sender, player, _ = self._run(lambda: (1.0, 1.0), tune=tune, seconds=1.15)
        self.assertIn(("disable", ("old", "new")), sender.log)
        self.assertTrue(any(row[0] == "strip" and row[1] == "new" for row in sender.log))

    def test_streams_then_disables_on_stop(self) -> None:
        sender, _, exited = self._run(lambda: (1.0, 1.0))
        kinds = [e[0] for e in sender.log]
        self.assertEqual(kinds[:2], ["wake", "enable"])
        self.assertIn("strip", kinds)
        self.assertEqual(kinds[-1], "disable")
        self.assertEqual(kinds.count("disable"), 1)  # once (GoveeLan.disable sends it 3x)
        self.assertEqual(len(exited), 1)
        self.assertTrue(all(v <= int(255 * 0.35) for e in sender.log if e[0] == "strip" for v in e[2]))

    def test_a_crash_still_disables(self) -> None:
        calls = {"n": 0}

        def tune():
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("bad env")
            return _tune()

        def clock():
            raise RuntimeError("boom")

        with self.assertLogs("jarvis.brain.light_sync", level="ERROR"):
            sender, player, exited = self._run(clock, tune=tune, seconds=0.15)
        self.assertEqual([e[0] for e in sender.log][-1], "disable")
        self.assertEqual(exited, [player])

    def test_unknown_position_holds_and_resume_retakes(self) -> None:
        state = {"r": None}
        sender = FakeSender()
        track = np.full((600, 3), 100, np.uint8)
        player = light_sync.Player(track, "1", lambda: state["r"], _tune, sender)
        player.start()
        time.sleep(0.15)
        self.assertNotIn("strip", [e[0] for e in sender.log])
        state["r"] = (5.0, 0.0)  # paused
        time.sleep(0.15)
        state["r"] = (5.0, 1.0)  # resumed
        time.sleep(0.15)
        player.stop()
        self.assertEqual([e[0] for e in sender.log].count("enable"), 2)


class FakePlayer:
    instances = []

    def __init__(self, track, rating_key, clock, tune, sender, now=None, on_exit=None, lead_ms=None):  # noqa: ANN001
        self.rating_key, self.started, self.stopped = rating_key, False, False
        self.lead_ms = lead_ms
        self._stop = threading.Event()
        FakePlayer.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self, wait=2.0) -> None:  # noqa: ANN001
        self.stopped = True
        self._stop.set()


class ControllerBase(TrackStoreDir):
    def setUp(self) -> None:
        super().setUp()
        FakePlayer.instances = []
        self.t = 1000.0
        self.sync = light_sync.LightSync(sender_factory=FakeSender, now=lambda: self.t)
        light_tracks.save("1", np.zeros((100, 3), np.uint8), "Up")
        self.restored = []
        self.follow = []
        self.fan = []
        self.fan_was_on = True
        self.barn = False
        for p in (patch.object(light_sync, "Player", FakePlayer),
                  patch.object(light_sync, "_restore_movie_scene", side_effect=lambda: self.restored.append(1)),
                  patch.object(light_sync, "_pause_follow", side_effect=lambda: self.follow.append("off") or True),
                  patch.object(light_sync, "_resume_follow", side_effect=lambda: self.follow.append("on")),
                  patch.object(light_sync, "_fan_off", side_effect=lambda: self.fan.append("off") or self.fan_was_on),
                  patch.object(light_sync, "_fan_on", side_effect=lambda: self.fan.append("on")),
                  patch.object(light_sync, "_barn_door_engaged", side_effect=lambda: self.barn),
                  patch.object(light_sync, "_movie_mode", return_value=False),
                  patch.dict(os.environ, {"LIGHT_SYNC": "1", "LIGHT_SYNC_LEAD_MS": "", "LIGHT_SYNC_TEST_TITLE": ""})):
            p.start()
            self.addCleanup(p.stop)

    def play(self, key="1", title="Up", state="playing") -> None:
        self.sync.clock.tv_report(state=state, position_ms=10_000, title=title, rating_key=key)

    def tick(self, movie=True, barn=False) -> None:
        self.barn = barn
        self.sync.tick(movie_mode=movie, barn_door=barn)


class ControllerTests(ControllerBase):
    def test_movie_mode_with_a_track_starts_and_pause_holds(self) -> None:
        self.play()
        self.tick()
        self.assertTrue(self.sync.running)
        self.assertEqual(self.follow, ["off"])
        self.play(state="paused")
        self.tick()
        self.assertTrue(self.sync.running)
        self.assertTrue(self.sync.status()["paused"])

    def test_no_track_or_no_movie_mode_does_nothing(self) -> None:
        self.play(key="2", title="Cars")
        self.tick()
        self.assertFalse(self.sync.running)
        self.assertIn("no light sync", self.sync.status()["reason"])
        self.play()
        self.tick(movie=False)
        self.assertFalse(self.sync.running)
        self.assertEqual(FakePlayer.instances, [])

    def test_stopping_playback_releases_and_puts_movie_back(self) -> None:
        self.play()
        self.tick()
        self.play(state="stopped")
        self.tick()
        self.assertFalse(self.sync.running)
        self.assertTrue(FakePlayer.instances[0].stopped)
        self.assertEqual(self.restored, [1])
        self.assertEqual(self.follow, ["off", "on"])

    def test_a_film_without_a_track_releases_and_restores(self) -> None:
        self.play()
        self.tick()
        self.play(key="2", title="Cars")
        self.tick()
        self.assertFalse(self.sync.running)
        self.assertEqual(self.restored, [1])

    def test_movie_mode_ending_releases_without_touching_the_room(self) -> None:
        self.play()
        self.tick()
        self.tick(movie=False)
        self.assertFalse(self.sync.running)
        self.assertEqual(self.restored, [])

    def test_barn_door_wins_and_sync_comes_back_after(self) -> None:
        self.play()
        self.tick()
        self.barn = True
        self.sync.release_for_barn_door()
        self.assertFalse(self.sync.running)
        self.assertTrue(FakePlayer.instances[0].stopped)
        self.assertEqual(self.restored, [])
        self.assertEqual(self.follow, ["off"])  # Barn Door's stand-down turns it back on
        self.tick(barn=True)
        self.assertFalse(self.sync.running)
        self.tick(barn=False)
        self.assertTrue(self.sync.running)
        self.assertEqual(len(FakePlayer.instances), 2)

    def test_auto_off_and_manual_start_outside_movie_mode(self) -> None:
        self.sync.set_auto(False)
        self.play()
        self.tick()
        self.assertFalse(self.sync.running)
        ok, message = self.sync.start()
        self.assertTrue(ok, message)
        self.assertIn("Up", message)
        self.assertTrue(self.sync.running)
        self.play(state="stopped")  # the manual start ends with the film
        self.tick(movie=False)
        self.assertFalse(self.sync.running)
        self.assertIsNone(self.sync.manual_key)

    def test_fan_ring_goes_dark_and_the_movie_scene_brings_it_back(self) -> None:
        self.play()
        self.tick()
        self.assertEqual(self.fan, ["off"])
        self.play(state="stopped")
        self.tick()
        self.assertEqual(self.fan, ["off"])  # the Movie scene sets the ring itself
        self.assertEqual(self.restored, [1])

    def test_fan_ring_comes_back_after_a_sync_outside_movie_mode(self) -> None:
        self.play()
        self.sync.start()
        self.assertEqual(self.fan, ["off"])
        self.sync.stop()
        self.assertEqual(self.fan, ["off", "on"])

    def test_fan_ring_that_was_off_stays_off(self) -> None:
        self.fan_was_on = False
        self.play()
        self.sync.start()
        self.sync.stop()
        self.assertEqual(self.fan, ["off"])

    def test_fan_ring_left_to_barn_door(self) -> None:
        self.play()
        self.tick()
        self.barn = True
        self.sync.release_for_barn_door()
        self.assertEqual(self.fan, ["off"])

    def test_manual_start_refusals(self) -> None:
        with patch.object(self.sync, "poll_plex"):
            self.assertIn("Nothing's playing", self.sync.start()[1])
            self.play(key="2", title="Cars")
            self.assertIn("no light sync for Cars", self.sync.start()[1])
            self.play()
            self.barn = True
            self.assertIn("Barn Door", self.sync.start()[1])

    def test_manual_stop_holds_for_this_film_only(self) -> None:
        self.play()
        self.tick()
        ok, message = self.sync.stop()
        self.assertTrue(ok)
        self.assertIn("Movie scene", message)
        self.tick()
        self.assertFalse(self.sync.running)
        self.play(key="3", title="Brave")
        light_tracks.save("3", np.zeros((10, 3), np.uint8), "Brave")
        self.tick()
        self.assertTrue(self.sync.running)

    def test_master_switch_off_stops(self) -> None:
        self.play()
        self.tick()
        with patch.dict(os.environ, {"LIGHT_SYNC": "0"}):
            self.tick()
        self.assertFalse(self.sync.running)

    def test_shutdown_releases(self) -> None:
        self.play()
        self.tick()
        self.sync.shutdown()
        self.assertTrue(FakePlayer.instances[0].stopped)
        self.assertEqual(self.follow, ["off", "on"])
        self.assertFalse(self.sync.running)

    def test_state_file_round_trip_and_revision(self) -> None:
        self.assertEqual(light_sync.load_state(), {"auto": True, "intensity": "medium", "offset_ms": 0, "lamp_lead_ms": 150, "format_offsets": {}, "light_offsets": {}})
        before = self.sync.revision()
        self.sync.set_intensity("Subtle")
        self.sync.set_auto(False)
        self.assertEqual(light_sync.load_state(), {"auto": False, "intensity": "subtle", "offset_ms": 0, "lamp_lead_ms": 150, "format_offsets": {}, "light_offsets": {}})
        self.assertGreater(self.sync.revision(), before)
        with self.assertRaises(ValueError):
            self.sync.set_intensity("blinding")
        light_sync._STATE_FILE.write_text("{bad")
        self.assertEqual(light_sync.load_state()["intensity"], "medium")

    def test_intensity_presets(self) -> None:
        with patch.dict(os.environ, {"LIGHT_SYNC_BLEND_MS": "", "LIGHT_SYNC_STRIP_LEVEL": "", "LIGHT_SYNC_LAMP_LEVEL": ""}):
            medium = light_sync.tuning("medium")
            self.assertEqual((medium.strip_level, medium.lamp_level, medium.blend_ms), (0.35, 0.6, 80))
            subtle, intense = light_sync.tuning("subtle"), light_sync.tuning("intense")
        self.assertLess(subtle.strip_level, medium.strip_level)
        self.assertGreater(subtle.blend_ms, medium.blend_ms)
        self.assertGreater(intense.strip_level, medium.strip_level)
        self.assertLessEqual(intense.jumps_per_s, 3)
        self.assertEqual(medium.strips, ("10.0.0.125", "10.0.0.221"))


class MonitorPassTests(unittest.TestCase):
    def test_polls_plex_only_when_armed(self) -> None:
        sync = Mock()
        sync.clock.tv_fresh.return_value = False
        sync.armed.return_value = False
        with patch.object(light_sync, "controller", sync), patch.object(light_sync, "_movie_mode", return_value=False), \
                patch.object(light_sync, "_barn_door_engaged", return_value=False), \
                patch.dict(os.environ, {"LIGHT_SYNC": "1"}):
            self.assertEqual(light_sync.monitor_pass(), 5.0)
            sync.poll_plex.assert_not_called()
            sync.armed.return_value = True
            self.assertEqual(light_sync.monitor_pass(), 1.0)
            sync.poll_plex.assert_called_once()
        sync.tick.assert_called_with(movie_mode=False, barn_door=False)


class ToolAndVoiceTests(TrackStoreDir):
    def test_create_light_sync_finds_the_film_and_starts_once(self) -> None:
        film = {"rating_key": "4", "title": "The Hunger Games", "year": 2012, "duration_ms": 1, "part_key": "/p"}
        with patch("integrations.plex.is_configured", return_value=True), \
                patch("integrations.plex.find_movie", return_value=(True, film)) as find, \
                patch.object(light_tracks, "start_build", return_value=(True, "Building the light sync")) as build:
            reply = tools.execute_tool("create_light_sync", {"title": "Hunger Games"})
        find.assert_called_once_with("Hunger Games")
        build.assert_called_once_with(film, rebuild=False)
        self.assertIn("Building", reply)
        light_tracks.save("4", np.zeros((3, 3), np.uint8), "The Hunger Games")
        with patch("integrations.plex.is_configured", return_value=True), \
                patch("integrations.plex.find_movie", return_value=(True, film)), \
                patch.object(light_tracks, "ffmpeg_exe", return_value="/bin/ffmpeg"):
            self.assertIn("already has a light sync", tools.execute_tool("create_light_sync", {"title": "Hunger Games"}))

    def test_light_sync_tool_actions(self) -> None:
        sync = Mock()
        sync.start.return_value = (True, "Light sync on for Up, sir.")
        sync.stop.return_value = (True, "Light sync off for Up, sir.")
        sync.status.return_value = {"running": False, "running_title": None, "paused": False, "intensity": "medium",
                                    "auto": True, "reason": "Not in movie mode.", "building": None}
        with patch.object(light_sync, "controller", sync):
            self.assertIn("on for Up", tools.execute_tool("light_sync", {"action": "start"}))
            self.assertIn("off for Up", tools.execute_tool("light_sync", {"action": "stop", "intensity": "intense"}))
            sync.set_intensity.assert_called_with("intense")
            self.assertIn("Not in movie mode", tools.execute_tool("light_sync", {"action": "status"}))
        self.assertIn("no light syncs", tools.execute_tool("light_sync", {"action": "list"}))

    def test_quick_command(self) -> None:
        sync = Mock()
        sync.start.return_value = (True, "")
        with patch.object(light_sync, "controller", sync):
            self.assertEqual(quick_commands.run("Hey Jarvis, start light sync").action, "light sync on")
            self.assertEqual(quick_commands.run("stop the light sync").action, "light sync off")
            sync.start.return_value = (False, "no track")
            self.assertIsNone(quick_commands.run("light sync on"))  # the conversation explains why


class ApiBase(TrackStoreDir):
    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (patch.object(client_tokens, "_FILE", Path(tmp.name) / "client_tokens.json"),
                  patch.dict(os.environ, {"JARVIS_API_TOKEN": "admin", "PLEX_URL": "", "PLEX_TOKEN": ""})):
            p.start()
            self.addCleanup(p.stop)
        server._runtime = Mock()
        self.addCleanup(setattr, server, "_runtime", None)
        self.sync = light_sync.LightSync(sender_factory=FakeSender)
        p = patch.object(light_sync, "controller", self.sync)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.admin = {"Authorization": "Bearer admin"}


class ApiTests(ApiBase):
    def test_get_and_set(self) -> None:
        self.assertEqual(self.client.get("/api/lightsync").status_code, 401)
        state = self.client.get("/api/lightsync", headers=self.admin).json()
        self.assertEqual((state["auto"], state["intensity"], state["running"]), (True, "medium", False))
        state = self.client.post("/api/lightsync", headers=self.admin, json={"intensity": "intense", "auto": False}).json()
        self.assertEqual((state["auto"], state["intensity"]), (False, "intense"))
        state = self.client.post("/api/lightsync", headers=self.admin, json={"action": "start"}).json()
        self.assertIn("Nothing's playing", state["message"])
        self.assertEqual(self.client.post("/api/lightsync", headers=self.admin, json={"intensity": "max"}).status_code, 422)
        self.assertEqual(self.client.post("/api/lightsync", headers=self.admin, json={}).status_code, 422)

    def test_build(self) -> None:
        film = {"rating_key": "4", "title": "Up", "duration_ms": 1, "part_key": "/p"}
        with patch("integrations.plex.movie", return_value=(True, film)), \
                patch.object(light_tracks, "start_build", return_value=(True, "Building")) as build:
            body = self.client.post("/api/lightsync/build", headers=self.admin, json={"rating_key": "4"}).json()
        self.assertTrue(body["started"])
        build.assert_called_once_with(film, rebuild=False)
        self.assertEqual(self.client.post("/api/lightsync/build", headers=self.admin, json={}).status_code, 422)

    def test_tv_playback_needs_its_scope(self) -> None:
        report = {"package": "com.plexapp.android", "state": "playing", "position_ms": 5000, "speed": 1.0,
                  "title": "Up", "rating_key": "1"}
        without = {"Authorization": "Bearer " + client_tokens.create("nicks-room-tv", ["status:read"])}
        self.assertEqual(self.client.post("/api/tv/playback", headers=without, json=report).status_code, 403)
        client_tokens.grant("nicks-room-tv", ["playback:write"])
        self.assertTrue(self.client.post("/api/tv/playback", headers=without, json=report).json()["used"])
        pb = self.sync.clock.current()
        self.assertEqual((pb.rating_key, pb.source), ("1", "tv"))
        other = {**report, "package": "com.spotify.tv.android"}
        self.assertFalse(self.client.post("/api/tv/playback", headers=without, json=other).json()["used"])
        self.assertEqual(self.client.post("/api/tv/playback", headers=without,
                                          json={**report, "state": "dancing"}).status_code, 422)


class BarnDoorHookTests(unittest.TestCase):
    def test_engage_releases_light_sync_first(self) -> None:
        from brain import protocols
        order = []
        with patch.object(light_sync, "release_for_barn_door", side_effect=lambda: order.append("release")), \
                patch.object(protocols.home_assistant, "call_service",
                             side_effect=lambda *a, **k: order.append(a[:2]) or (True, "")), \
                patch.object(protocols.home_assistant, "_request", return_value=(False, None)), \
                patch.object(protocols.theme, "current", return_value={"theme": "barnDoor"}), \
                patch.object(protocols.theme, "choose"), \
                patch.dict(os.environ, {"BARN_DOOR_TV": "0"}):
            protocols.engage()
        self.assertEqual(order[0], "release")


if __name__ == "__main__":
    unittest.main()


class PlexMovieRecordTests(unittest.TestCase):
    def test_smallest_version_is_read_for_the_track(self) -> None:
        from integrations import plex
        item = {"ratingKey": 7, "title": "Michael", "year": 2026, "duration": 1000, "Media": [
            {"width": 3840, "height": 2160, "Part": [{"key": "/library/parts/4k/file.mkv"}]},
            {"width": 1920, "height": 1080, "Part": [{"key": "/library/parts/hd/file.mkv"}]},
        ]}
        self.assertEqual(plex._movie_record(item)["part_key"], "/library/parts/hd/file.mkv")

    def test_versions_without_sizes_still_give_a_part(self) -> None:
        from integrations import plex
        item = {"ratingKey": 8, "title": "Cars 2", "Media": [{"Part": [{"key": "/library/parts/1/file.mp4"}]}]}
        self.assertEqual(plex._movie_record(item)["part_key"], "/library/parts/1/file.mp4")


# --- Build queue, timing nudges, calibration (2026-11-14) -------------------

class _GatedProc(_FakeProc):
    """ffmpeg that hands over its frames only once ``gate`` is set."""

    def __init__(self, gate: threading.Event, frames: int = 20) -> None:
        super().__init__(_frame((0, 0, 255)) * frames)
        data = self.stdout

        class Out:
            def read(self, n):  # noqa: ANN001
                gate.wait(5)
                return data.read(n)

        self.stdout = Out()


class WorkerTests(unittest.TestCase):
    """brain/light_track_worker.py for real, with a Python stand-in for ffmpeg."""

    def run_worker(self, script: str) -> subprocess.CompletedProcess:
        command = light_tracks.worker_command([__import__("sys").executable, "-c", script])
        root = Path(light_tracks.__file__).resolve().parents[1]
        return subprocess.run(command, capture_output=True, timeout=60, cwd=root)

    def test_colours_every_frame_like_core_did(self) -> None:
        frames = _frame((0, 0, 255)) * 3 + _frame((100, 100, 100)) * 2
        script = f"import sys; sys.stdout.buffer.write(bytes.fromhex('{frames.hex()}'))"
        done = self.run_worker(script)
        self.assertEqual(done.returncode, 0, done.stderr)
        track = light_tracks.read_track(io.BytesIO(done.stdout))
        expected = (np.array([light_tracks.frame_colour(_frame((0, 0, 255)))] * 3
                             + [light_tracks.frame_colour(_frame((100, 100, 100)))] * 2) * 255).clip(0, 255).astype(np.uint8)
        np.testing.assert_array_equal(track, expected)

    def test_passes_ffmpegs_exit_code_and_errors_through(self) -> None:
        done = self.run_worker("import sys; sys.stderr.write('404 Not Found'); sys.exit(1)")
        self.assertEqual(done.returncode, 1)
        self.assertIn(b"404 Not Found", done.stderr)

    def test_read_track_reports_progress_every_hundred_frames(self) -> None:
        marks = []
        track = light_tracks.read_track(io.BytesIO(bytes(3 * 250)), marks.append)
        self.assertEqual(len(track), 250)
        self.assertEqual(marks, [100, 200])


class QueueTests(BuildBase):
    def film(self, key: str, title: str) -> dict:
        return {**self.FILM, "rating_key": key, "title": title, "part_key": f"/library/parts/{key}/f.mkv"}

    def _wait_all(self) -> None:
        for _ in range(500):
            if light_tracks.building() is None and not light_tracks.queue():
                return
            time.sleep(0.01)
        self.fail("queue never drained")

    def _popen(self, gate: threading.Event, order: list):
        def make(command, **_):  # noqa: ANN001
            url = next(c for c in command if c.startswith("http"))
            order.append(url.split("/library/parts/")[1].split("/")[0])
            return _GatedProc(gate) if len(order) == 1 else _FakeProc(_frame((0, 0, 255)) * 20)
        return make

    def _saved_queue(self) -> list:
        import json
        return [row["film"]["rating_key"] for row in json.loads((self.dir / "queue.json").read_text())]

    def test_fifo_order_dedupe_and_one_push_per_film(self) -> None:
        gate, order = threading.Event(), []
        with patch.object(light_tracks.subprocess, "Popen", side_effect=self._popen(gate, order)):
            ok, said = light_tracks.start_build(self.film("1", "The Hunger Games"))
            self.assertTrue(ok)
            self.assertIn("Building the light sync for The Hunger Games", said)
            self.assertEqual(light_tracks.start_build(self.film("2", "Catching Fire"))[1],
                             "Queued Catching Fire, sir: second in line.")
            self.assertEqual(light_tracks.start_build(self.film("3", "Mockingjay"))[1],
                             "Queued Mockingjay, sir: third in line.")
            self.assertEqual([q["title"] for q in light_tracks.queue()], ["Catching Fire", "Mockingjay"])
            self.assertEqual(self._saved_queue(), ["1", "2", "3"])
            ok, said = light_tracks.start_build(self.film("1", "The Hunger Games"))
            self.assertFalse(ok)
            self.assertIn("already building", said)
            ok, said = light_tracks.start_build(self.film("3", "Mockingjay"))
            self.assertFalse(ok)
            self.assertIn("already in the queue, sir: third in line", said)
            self.assertTrue(light_tracks.pending("3"))
            gate.set()
            self._wait_all()
        self.assertEqual(order, ["1", "2", "3"])
        self.assertTrue(all(light_tracks.has_track(k) for k in "123"))
        self.assertEqual([n[0] for n in self.notes], ["Light sync ready"] * 3)
        self.assertEqual(self._saved_queue(), [])
        self.assertIn("already has a light sync", light_tracks.start_build(self.film("2", "Catching Fire"))[1])

    def test_cancel_one_or_all_and_front_of_queue(self) -> None:
        gate, order = threading.Event(), []
        with patch.object(light_tracks.subprocess, "Popen", side_effect=self._popen(gate, order)):
            light_tracks.start_build(self.film("1", "A"))
            light_tracks.start_build(self.film("2", "B"))
            light_tracks.start_build(self.film("3", "C"))
            self.assertEqual(light_tracks.start_build(self.film("9", "Test"), front=True, quiet=True)[1],
                             "Queued Test, sir: second in line.")
            self.assertEqual([q["rating_key"] for q in light_tracks.queue()], ["9", "2", "3"])
            # Already waiting further back: moved up front.
            self.assertTrue(light_tracks.start_build(self.film("3", "C"), front=True)[0])
            self.assertEqual([q["rating_key"] for q in light_tracks.queue()], ["3", "9", "2"])
            self.assertEqual(light_tracks.cancel("2"), ["B"])
            self.assertEqual(light_tracks.cancel("nope"), [])
            self.assertEqual(self._saved_queue(), ["1", "3", "9"])
            self.assertEqual(light_tracks.cancel(everything=True), ["A", "C", "Test"])
            gate.set()
            self._wait_all()
        self.assertEqual(order, ["1"])
        self.assertEqual(self.notes, [])
        self.assertEqual(self._saved_queue(), [])

    def test_quiet_build_skips_the_ready_push(self) -> None:
        with patch.object(light_tracks.subprocess, "Popen", return_value=_FakeProc(_frame((0, 0, 255)) * 20)):
            light_tracks.start_build(self.FILM, quiet=True)
            self._wait()
        self.assertTrue(light_tracks.has_track("7"))
        self.assertEqual(self.notes, [])

    def test_restart_resumes_and_the_interrupted_build_starts_again(self) -> None:
        gate, order = threading.Event(), []
        with patch.object(light_tracks.subprocess, "Popen", side_effect=self._popen(gate, order)):
            light_tracks.start_build(self.film("1", "A"))
            light_tracks.start_build(self.film("2", "B"))
            light_tracks.shutdown()  # Core stopping mid-build
            gate.set()
            self._wait()
        self.assertFalse(light_tracks.has_track("1"))
        self.assertEqual(self._saved_queue(), ["1", "2"])
        # A fresh Core: nothing in memory, the file says what's left.
        light_tracks._queue.clear()
        light_tracks._stopping = False
        order.clear()
        with patch.object(light_tracks.subprocess, "Popen", side_effect=lambda c, **_: (
                order.append(next(x for x in c if x.startswith("http")).split("/parts/")[1][0]),
                _FakeProc(_frame((0, 0, 255)) * 20))[1]):
            self.assertEqual(light_tracks.resume(), 2)
            self._wait_all()
        self.assertEqual(order, ["1", "2"])
        self.assertTrue(light_tracks.has_track("1") and light_tracks.has_track("2"))
        self.assertEqual(light_tracks.resume(), 0)  # drained

    def test_resume_drops_a_copy_build_that_queued_itself(self) -> None:
        import json
        self.dir.mkdir(parents=True, exist_ok=True)
        auto = {**self.film("1", "Songbirds"), "rating_key": "1--4251e5d3e550c3bb", "base_rating_key": "1"}
        (self.dir / "queue.json").write_text(json.dumps([{"film": auto, "rebuild": False, "quiet": True}]))
        with self.assertLogs("jarvis.brain.light_tracks", level="INFO"):
            self.assertEqual(light_tracks.resume(), 0)
        self.assertEqual(self._saved_queue(), [])

    def test_resume_skips_junk_and_films_that_have_tracks(self) -> None:
        import json
        light_tracks.save("1", np.zeros((5, 3), np.uint8), "A")
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "queue.json").write_text(json.dumps([{"film": self.film("1", "A")}, {"film": {"title": "x"}}, 7]))
        self.assertEqual(light_tracks.resume(), 0)
        (self.dir / "queue.json").write_text("{bad")
        self.assertEqual(light_tracks.resume(), 0)


class OffsetTests(ControllerBase):
    def test_lamp_head_start_is_nudged_live_and_kept(self) -> None:
        with patch.dict(os.environ, {"LIGHT_SYNC_LAMP_LEAD_MS": "150"}):
            self.assertEqual(light_sync.load_state()["lamp_lead_ms"], 150)
            ok, said = self.sync.nudge_lamp(25)
            self.assertTrue(ok)
            self.assertIn("175 ms ahead", said)
            self.assertEqual(light_sync._live_tuning().lamp_lead_ms, 175)  # the player's next read
            self.assertEqual(self.sync.offsets()["lamp_lead_ms"], 175)
            self.sync.nudge_lamp(-5000)
            self.assertEqual(light_sync.load_state()["lamp_lead_ms"], 0)
            ok, _ = self.sync.nudge_lamp(-25)
            self.assertFalse(ok)
            self.sync.set_auto(False)  # other saves keep it
            self.assertEqual(light_sync.load_state()["lamp_lead_ms"], 0)

    def test_offset_math_default_from_env_and_clamp(self) -> None:
        with patch.dict(os.environ, {"LIGHT_SYNC_LEAD_MS": "40"}):
            self.assertEqual(light_sync.load_state()["offset_ms"], 40)
            self.assertEqual(self.sync.effective_offset("1"), 40)
            ok, said = self.sync.nudge(25, "global")
            self.assertTrue(ok)
            self.assertIn("now +65 ms", said)
        with patch.dict(os.environ, {"LIGHT_SYNC_LEAD_MS": "999"}):
            self.assertEqual(self.sync.global_offset(), 65)  # saved wins over the .env default
        self.assertEqual(light_tracks.set_film_offset("1", -15), -15)
        self.assertEqual(self.sync.effective_offset("1"), 50)
        self.assertEqual(light_tracks.set_film_offset("1", 9999), 2000)
        self.assertEqual(self.sync.effective_offset("1"), 2000)  # 65 + 2000, clamped
        self.assertIsNone(light_tracks.set_film_offset("404", 10))
        self.assertEqual(light_tracks.clamp_offset("junk"), 0)
        self.assertEqual(light_tracks.clamp_offset(float("nan")), 0)
        self.sync.nudge(5000, "global")
        self.assertEqual(self.sync.global_offset(), 2000)
        self.assertIn("as far earlier", self.sync.nudge(25, "global")[1])
        self.assertFalse(self.sync.nudge(0)[0])

    def test_nudges_reach_the_running_player_at_once(self) -> None:
        self.play()
        self.tick()
        player = FakePlayer.instances[0]
        self.assertEqual(player.lead_ms, 0)
        ok, said = self.sync.nudge(50)  # default: the film syncing
        self.assertTrue(ok)
        self.assertIn("for Up", said)
        self.assertEqual((light_tracks.film_offset("1"), self.sync.global_offset(), player.lead_ms), (50, 0, 50))
        self.sync.nudge(-25, "global")
        self.assertEqual(player.lead_ms, 25)
        self.assertEqual(self.sync.status()["offset"]["film_ms"], 50)
        self.assertEqual(self.sync.status()["offset"]["effective_ms"], 25)
        self.sync.reset_offset()
        self.assertEqual((light_tracks.film_offset("1"), player.lead_ms), (0, -25))
        # A rebuild keeps the film's offset.
        self.sync.nudge(75)
        light_tracks.save("1", np.zeros((100, 3), np.uint8), "Up")
        self.assertEqual(light_tracks.film_offset("1"), 75)

    def test_no_film_means_global_unless_film_asked_for(self) -> None:
        self.assertFalse(self.sync.nudge(25, "film")[0])
        self.assertTrue(self.sync.nudge(25)[0])
        self.assertEqual(self.sync.global_offset(), 25)


class LivePlayerOffsetTests(unittest.TestCase):
    def test_player_reads_lead_every_frame(self) -> None:
        sender = FakeSender()
        track = np.array([[200, 0, 0]] * 100 + [[0, 0, 200]] * 100, np.uint8)
        player = light_sync.Player(track, "1", lambda: (9.5, 0.0), _tune, sender, lead_ms=0)
        player.start()
        time.sleep(0.4)
        before = [e for e in sender.log if e[0] == "strip"][-1][2]
        player.lead_ms = 1000  # one second later in the film: blue
        time.sleep(0.6)
        player.stop()
        after = [e for e in sender.log if e[0] == "strip"][-1][2]
        self.assertGreater(before[0], before[2])
        self.assertGreater(after[2], after[0])


class CalibrationTests(ControllerBase):
    TEST = {"rating_key": "50", "title": "Light Sync Test", "year": 2026, "duration_ms": 180000, "part_key": "/p/50"}

    def setUp(self) -> None:
        super().setUp()
        self.play_plex = Mock(return_value=(True, "ok"))
        self.card = Mock()
        self.from_start = Mock(return_value=(True, ""))
        self.stop_tv = Mock(return_value=(True, "ok"))
        self.find = Mock(return_value=(True, dict(self.TEST)))
        for p in (patch("integrations.plex.is_configured", return_value=True),
                  patch("integrations.plex.find_movie", self.find),
                  patch("integrations.plex.server_id", return_value="srv"),
                  patch("integrations.tv_remote.play_plex", self.play_plex),
                  patch("brain.event_bus.publish_card", self.card),
                  patch("integrations.plex.play_from_start", self.from_start),
                  patch("integrations.tv_remote.stop_playback", self.stop_tv)):
            p.start()
            self.addCleanup(p.stop)

    def assert_asked_tv(self) -> None:
        self.card.assert_called_once()
        args, kwargs = self.card.call_args
        self.assertEqual(args[0], "tv_play")
        self.from_start.assert_called_with("50")  # no resuming half way through
        self.assertEqual(args[2]["rating_key"], "50")
        self.play_plex.assert_not_called()  # JARVIS TV first

    def test_ha_deep_link_only_when_the_tv_doesnt_start_the_clip(self) -> None:
        light_tracks.save("50", np.zeros((1800, 3), np.uint8), "Light Sync Test")
        self.assertTrue(self.sync.calibrate()[0])
        self.assert_asked_tv()
        self.t += light_sync.TV_FALLBACK_S - 1
        self.tick(movie=False)
        self.play_plex.assert_not_called()
        self.t += 2
        self.tick(movie=False)
        self.tick(movie=False)
        self.play_plex.assert_called_once_with("50", "srv")
        self.assertIsNotNone(self.sync.calibration)  # still waiting for the clip
        # When the TV does start it, there's no fallback at all.
        self.sync.end_calibration()
        self.play_plex.reset_mock()
        self.sync.calibrate()
        self.play(key="50", title="Light Sync Test")
        self.tick(movie=False)
        self.t += light_sync.TV_FALLBACK_S + 1
        self.tick(movie=False)
        self.play_plex.assert_not_called()

    def test_builds_the_test_track_first_then_plays_and_syncs_in_any_scene(self) -> None:
        with patch.object(light_tracks, "start_build", return_value=(True, "Building")) as build, \
                patch.object(light_tracks, "pending", return_value=True):
            ok, said = self.sync.calibrate()
            self.assertTrue(ok)
            self.assertIn("calibration track first", said)
            build.assert_called_once_with(self.TEST, front=True, quiet=True)
            self.find.assert_called_once_with("Light Sync Test")
            self.tick(movie=False)
            self.card.assert_not_called()
            self.assertEqual(self.sync.status()["calibrating"]["phase"], "building")
        light_tracks.save("50", np.zeros((1800, 3), np.uint8), "Light Sync Test")
        self.sync.set_auto(False)  # calibration is a manual start: auto doesn't matter
        self.tick(movie=False)  # Game Time, say
        self.assert_asked_tv()
        self.play(key="50", title="Light Sync Test")
        self.tick(movie=False)
        self.assertTrue(self.sync.running)
        self.assertTrue(self.sync.armed())
        # Calibration requires the TV's current format, never another player's default.
        self.assertFalse(self.sync.nudge(25)[0])
        self.sync.clock.session_info = {"rating_key": "50", "format_key": "1080/sdr/h264/directplay/aac"}
        ok, said = self.sync.nudge(25)
        self.assertIn("1080/sdr/h264/directplay/aac", said)
        self.assertEqual((self.sync.global_offset(), light_tracks.film_offset("50")), (0, 0))
        self.assertEqual(FakePlayer.instances[0].lead_ms, 25)
        self.assertEqual(self.sync.status()["offset"]["scope"], "format")
        ok, said = self.sync.end_calibration()
        self.assertTrue(ok)
        self.assertIn("+25 ms", said)
        self.stop_tv.assert_called_once()
        self.assertFalse(self.sync.running)
        self.assertTrue(FakePlayer.instances[0].stopped)
        self.assertEqual(self.restored, [])  # not in movie mode: the room isn't touched
        self.tick(movie=False)
        self.assertFalse(self.sync.running)  # the clip's last seconds don't restart it

    def test_with_a_track_it_plays_at_once_and_done_restores_movie_scene(self) -> None:
        light_tracks.save("50", np.zeros((1800, 3), np.uint8), "Light Sync Test")
        self.play()
        self.tick()  # Up is syncing in movie mode
        ok, said = self.sync.calibrate()
        self.assertTrue(ok, said)
        self.assertIn("Calibrating", said)
        self.assertTrue(FakePlayer.instances[0].stopped)  # Up let go for the clip
        self.assert_asked_tv()
        self.play(key="50", title="Light Sync Test")
        self.tick()
        self.assertEqual(self.sync.session.rating_key, "50")
        ok, _ = self.sync.stop()  # stop = Done while calibrating
        self.assertTrue(ok)
        self.assertIsNone(self.sync.calibration)
        self.assertEqual(self.restored, [1])
        self.stop_tv.assert_called_once()

    def test_clip_ending_or_never_starting_ends_calibration(self) -> None:
        light_tracks.save("50", np.zeros((1800, 3), np.uint8), "Light Sync Test")
        self.sync.calibrate()
        self.play(key="50", title="Light Sync Test")
        self.tick(movie=False)
        self.play(key="50", title="Light Sync Test", state="stopped")
        self.tick(movie=False)
        self.assertIsNone(self.sync.calibration)
        self.assertFalse(self.sync.running)
        self.sync.calibrate()
        self.t += light_sync.CALIBRATION_START_S + 1
        self.tick(movie=False)
        self.assertIsNone(self.sync.calibration)
        self.assertFalse(self.sync.end_calibration()[0])

    def test_refusals(self) -> None:
        self.barn = True
        self.assertIn("Barn Door", self.sync.calibrate()[1])
        self.barn = False
        self.find.return_value = (False, "nope")
        with patch.dict(os.environ, {"LIGHT_SYNC_TEST_TITLE": "Flashes"}):
            ok, said = self.sync.calibrate()
        self.assertFalse(ok)
        self.assertIn("Flashes", said)
        self.find.return_value = (True, dict(self.TEST))


class QueueToolAndApiTests(ApiBase):
    def test_api_nudge_reset_calibrate_and_queue(self) -> None:
        with patch.object(self.sync, "nudge", return_value=(True, "Lights 25 ms earlier")) as nudge:
            body = self.client.post("/api/lightsync", headers=self.admin, json={"nudge_ms": 25}).json()
        nudge.assert_called_once_with(25, None)
        self.assertIn("earlier", body["message"])
        self.assertEqual(body["offset"]["step_ms"], 25)
        self.assertEqual(self.client.post("/api/lightsync", headers=self.admin,
                                          json={"nudge_ms": 2500}).status_code, 422)
        with patch.object(self.sync, "reset_offset", return_value=(True, "reset")) as reset:
            self.client.post("/api/lightsync", headers=self.admin, json={"reset_offset": True, "scope": "global"})
        reset.assert_called_once_with("global")
        with patch.object(self.sync, "calibrate", return_value=(True, "Calibrating")) as cal, \
                patch.object(self.sync, "end_calibration", return_value=(True, "Done")) as done:
            self.assertEqual(self.client.post("/api/lightsync", headers=self.admin,
                                              json={"calibrate": True}).json()["message"], "Calibrating")
            self.client.post("/api/lightsync", headers=self.admin, json={"calibrate": False})
        cal.assert_called_once()
        done.assert_called_once()
        with patch.object(light_tracks, "queue", return_value=[{"rating_key": "2", "title": "B", "year": None}]), \
                patch.object(light_tracks, "building", return_value={"rating_key": "1", "title": "A", "year": None,
                                                                      "percent": 40}):
            state = self.client.get("/api/lightsync", headers=self.admin).json()
        self.assertEqual(state["queue"][0]["title"], "B")
        self.assertEqual(state["building"]["percent"], 40)
        self.assertIsNone(state["calibrating"])
        with patch.object(light_tracks, "cancel", return_value=["A", "B"]) as cancel:
            body = self.client.post("/api/lightsync/build", headers=self.admin, json={"cancel": "all"}).json()
        cancel.assert_called_once_with(everything=True)
        self.assertIn("A, B", body["message"])

    def test_create_light_sync_takes_several_titles_or_a_series(self) -> None:
        films = {t: {"rating_key": k, "title": t, "duration_ms": 1, "part_key": "/p"}
                 for k, t in (("1", "The Hunger Games"), ("2", "Catching Fire"), ("3", "Mockingjay"))}
        answers = iter([(True, "Building the light sync for The Hunger Games, sir."),
                        (True, "Queued Catching Fire, sir: second in line."),
                        (False, "Mockingjay already has a light sync, sir.")])
        with patch("integrations.plex.is_configured", return_value=True), \
                patch("integrations.plex.find_movie", side_effect=lambda t: (True, films[t])) as find, \
                patch.object(light_tracks, "start_build", side_effect=lambda f, rebuild: next(answers)) as build:
            said = tools.execute_tool("create_light_sync", {"titles": list(films)})
        self.assertEqual([c.args[0] for c in find.call_args_list], list(films))
        self.assertEqual(build.call_count, 3)
        self.assertIn("Building The Hunger Games now", said)
        self.assertIn("Catching Fire", said)
        self.assertIn("Already done: Mockingjay", said)
        with patch("integrations.plex.is_configured", return_value=True), \
                patch("integrations.plex.search_movies", return_value=(True, list(films.values()))) as search, \
                patch.object(light_tracks, "start_build", return_value=(True, "Queued x")) as build:
            said = tools.execute_tool("create_light_sync", {"series": "Hunger Games"})
        search.assert_called_once_with("Hunger Games")
        self.assertEqual(build.call_count, 3)
        self.assertIn("Queued The Hunger Games, Catching Fire, Mockingjay", said)

    def test_light_sync_tool_nudge_calibrate_done_and_cancel(self) -> None:
        sync = Mock()
        sync.calibration = {"title": "Light Sync Test"}
        sync.nudge.return_value = (True, "nudged")
        sync.calibrate.return_value = (True, "Calibrating")
        sync.end_calibration.return_value = (True, "Calibration done")
        sync.reset_offset.return_value = (True, "reset")
        with patch.object(light_sync, "controller", sync):
            tools.execute_tool("light_sync", {"action": "nudge", "direction": "later", "ms": 25})
            sync.nudge.assert_called_with(-25.0, None)
            tools.execute_tool("light_sync", {"action": "nudge", "direction": "earlier", "scope": "global"})
            sync.nudge.assert_called_with(50.0, "global")
            tools.execute_tool("light_sync", {"action": "nudge", "ms": -75})
            sync.nudge.assert_called_with(-75.0, None)
            self.assertIn("Earlier or later", tools.execute_tool("light_sync", {"action": "nudge"}))
            self.assertEqual(tools.execute_tool("light_sync", {"action": "calibrate"}), "Calibrating")
            self.assertEqual(tools.execute_tool("light_sync", {"action": "done"}), "Calibration done")
            tools.execute_tool("light_sync", {"action": "reset_timing", "scope": "film"})
            sync.reset_offset.assert_called_with("film")
        with patch.object(light_tracks, "building", return_value={"rating_key": "1", "title": "A", "percent": 10}), \
                patch.object(light_tracks, "queue", return_value=[{"rating_key": "2", "title": "Catching Fire"}]), \
                patch.object(light_tracks, "cancel", return_value=["Catching Fire"]) as cancel:
            self.assertIn("Queued next: Catching Fire", tools.execute_tool("light_sync", {"action": "list"}))
            self.assertIn("Cancelled: Catching Fire",
                          tools.execute_tool("light_sync", {"action": "cancel_build", "title": "catching fire"}))
            cancel.assert_called_with("2")
            tools.execute_tool("light_sync", {"action": "cancel_build"})
            cancel.assert_called_with(everything=True)
        state = self.sync.status()
        self.assertIn("queue", state)
        status = tools.execute_tool("light_sync", {"action": "status"})
        self.assertIn("Timing: global 0 ms", status)


class PlexSearchTests(unittest.TestCase):
    def test_search_movies_keeps_the_series_in_order(self) -> None:
        from integrations import plex
        hits = {"MediaContainer": {"Hub": [{"Metadata": [
            {"type": "movie", "ratingKey": 3, "title": "The Hunger Games: Mockingjay - Part 1", "year": 2014},
            {"type": "movie", "ratingKey": 1, "title": "The Hunger Games", "year": 2012},
            {"type": "show", "ratingKey": 8, "title": "Hunger Games Show"},
            {"type": "movie", "ratingKey": 9, "title": "Gladiator", "year": 2000},
        ]}]}}
        with patch.object(plex, "_get", return_value=(True, hits)), \
                patch.object(plex, "movie", side_effect=lambda k: (True, {"rating_key": k})):
            ok, films = plex.search_movies("Hunger Games")
        self.assertTrue(ok)
        self.assertEqual([f["rating_key"] for f in films], ["1", "3"])


class WithoutGlowTests(unittest.TestCase):
    def test_test_clip_goes_dark_between_flashes(self) -> None:
        from brain.light_sync import without_glow
        glow, red, dim_blue = [20, 17, 14], [255, 0, 0], [10, 30, 60]
        out = without_glow(np.array([glow, red, glow, dim_blue], np.uint8))
        self.assertEqual(out.tolist(), [[0, 0, 0], red, [0, 0, 0], dim_blue])


class SteadyTrackTests(unittest.TestCase):
    BLUE = [23, 41, 57]
    GREY = [57, 48, 40]  # the warm-white fallback, dim

    def test_dim_blue_grey_flicker_holds_blue(self) -> None:
        from brain.light_sync import steady_track
        frames = [self.BLUE] * 8 + [self.GREY, self.BLUE, self.BLUE, self.GREY, self.BLUE, self.GREY] + [self.BLUE] * 8
        out = steady_track(np.array(frames, np.uint8))
        for row in out:
            self.assertGreater(int(row[2]), int(row[0]))  # blue everywhere, no grey blips
        self.assertEqual(out.max(1).tolist(), np.array(frames, np.uint8).max(1).tolist())  # brightness untouched

    def test_a_real_colour_change_goes_through(self) -> None:
        from brain.light_sync import steady_track
        red = [180, 20, 20]
        frames = [self.BLUE] * 8 + [red] * 10
        out = steady_track(np.array(frames, np.uint8))
        self.assertEqual(out[-1].tolist(), red)

    def test_action_flash_still_punches_through(self) -> None:
        from brain.light_sync import steady_track
        flash = [255, 200, 120]  # an explosion: much brighter, short
        frames = [self.BLUE] * 8 + [flash, flash] + [self.BLUE] * 8
        out = steady_track(np.array(frames, np.uint8))
        self.assertEqual(out[8].tolist(), flash)


class FilteredClockTests(TrackStoreDir):
    def setUp(self):
        super().setUp()
        self.t = 100.0
        self.clock = light_sync.PlaybackClock(now=lambda: self.t)

    @staticmethod
    def exchange(sent, outbound=.002, inbound=.002):
        return {"tv_send_ms": (sent - 80) * 1000,
                "core_receive_ms": (sent + outbound) * 1000,
                "core_send_ms": (sent + outbound) * 1000,
                "tv_receive_ms": (sent - 80 + outbound + inbound) * 1000}

    def report(self, sent, delay=.01, position=None, **kwargs):
        self.t = sent + delay
        self.clock.tv_report(state="playing", rating_key="1", title="Up",
                             position_ms=(sent - 100) * 1000 if position is None else position,
                             elapsed_ms=(sent - 80) * 1000,
                             exchange=self.exchange(sent - .1), **kwargs)

    def test_five_launches_two_minutes_each_ignore_wifi_arrival_jitter(self):
        for launch in range(5):
            self.clock = light_sync.PlaybackClock(now=lambda: self.t)
            for second in range(121):
                delay = (.010, .290, .035, .160, .070)[(second + launch) % 5]
                self.report(100 + second, delay)
                error = self.clock.current().position(self.t) - (self.t - 100)
                self.assertLess(abs(error), .001)
            diag = self.clock.diagnostics()
            self.assertEqual(diag["source_switches"], 0)
            self.assertEqual(len(diag["reanchors"]), 1)
            self.assertGreater(diag["jitter_p95_ms"], 100)
            self.assertLess(abs(diag["drift_ms"]), 1)

    def test_minimum_rtt_rejects_asymmetric_queueing(self):
        self.report(100)
        self.t = 101.3
        self.clock.tv_report(state="playing", rating_key="1", title="Up", position_ms=1000,
                             elapsed_ms=21000, exchange=self.exchange(100.9, outbound=.25))
        self.assertAlmostEqual(self.clock.current().position(self.t), 1.3, places=5)
        self.assertAlmostEqual(self.clock.diagnostics()["rtt_ms"], 4, places=4)

    def test_small_media_position_noise_slews_at_most_five_ms(self):
        for second in range(10):
            self.report(100 + second)
        predicted = self.clock.current().position(110)
        self.report(110, position=10080)
        self.assertLessEqual(abs(self.clock.current().position(110) - predicted), .005001)
        self.assertEqual(len(self.clock.reanchors), 1)

    def test_seek_pause_resume_anchor_immediately(self):
        self.report(100)
        self.report(101, position=60000)
        self.assertAlmostEqual(self.clock.current().position(self.t), 60.01, places=5)
        self.assertEqual(self.clock.reanchors[-1]["reason"], "seek")
        self.t = 102.1
        self.clock.tv_report(state="paused", rating_key="1", title="Up", position_ms=61000,
                             elapsed_ms=22000, exchange=self.exchange(101.9))
        self.t += 10
        self.assertEqual(self.clock.current().position(self.t), 61)
        self.clock.tv_report(state="playing", rating_key="1", title="Up", position_ms=61000,
                             elapsed_ms=(self.t - 80) * 1000, exchange=self.exchange(self.t - .1))
        self.assertAlmostEqual(self.clock.current().position(self.t), 61, places=5)

    def test_report_gap_and_duplicate_counted_and_restart_recovers(self):
        self.report(100, stream_id="first", report_id=1)
        self.report(101, stream_id="first", report_id=3)
        self.report(101.1, stream_id="first", report_id=2)
        self.assertEqual(self.clock.dropped, 2)
        self.report(102, stream_id="second", report_id=1)
        self.assertEqual(self.clock.report_id, 1)
        self.assertAlmostEqual(self.clock.current().position(self.t), 2.01)

    def test_stale_tv_never_uses_fresh_plex(self):
        self.report(100)
        self.t = 104
        self.clock.plex_report({"rating_key": "1", "title": "Up", "state": "playing", "view_offset_ms": 90000})
        self.assertIsNone(self.clock.current())
        self.report(105)
        self.assertAlmostEqual(self.clock.current().position(self.t), 5.01)

    def test_decoder_clock_rate_is_fitted_without_repeated_reanchors(self):
        for second in range(121):
            self.report(100 + second, position=second * 980)
            error = self.clock.current().position(self.t) - (self.t - 100) * .98
            if second >= 3:
                self.assertLess(abs(error), .03)
        self.assertEqual(len(self.clock.reanchors), 1)
        self.assertAlmostEqual(self.clock.tv.speed, .98, places=5)

    def test_native_media_sample_is_fitted_once_while_heartbeats_stay_fresh(self):
        self.t = 103.01
        args = dict(state="playing", rating_key="1", title="Up", position_ms=1000,
                    sample_elapsed_ms=21000, exchange=self.exchange(102.9))
        self.clock.tv_report(**args, elapsed_ms=23000)
        self.assertAlmostEqual(self.clock.current().position(self.t), 3.01, places=5)
        for sent in range(104, 120):
            self.t = sent + .01
            self.clock.tv_report(**{**args, "exchange": self.exchange(sent - .1)}, elapsed_ms=(sent - 80) * 1000)
        self.assertEqual(len(self.clock.position_samples), 1)
        self.assertEqual(len(self.clock.errors), 1)
        self.assertTrue(self.clock.tv_fresh())
        self.assertAlmostEqual(self.clock.current().position(self.t), self.t - 100, places=5)

    def test_native_paused_sample_can_be_old_without_becoming_stale(self):
        self.t = 110.01
        self.clock.tv_report(state="paused", rating_key="1", title="Up", position_ms=1000,
                             sample_elapsed_ms=21000, elapsed_ms=30000, exchange=self.exchange(109.9))
        self.assertEqual(self.clock.current().position(self.t), 1)
        self.assertTrue(self.clock.tv_fresh())


    def test_clock_skew_does_not_become_periodic_false_seeks(self):
        tv_rate = 1.013
        for second in range(181):
            sent = 100 + second
            self.t = sent + .01
            ping = sent - .1
            def tv_stamp(core):
                return (20 + (core - 100) * tv_rate) * 1000
            exchange = {"tv_send_ms": tv_stamp(ping), "core_receive_ms": (ping+.002)*1000,
                        "core_send_ms": (ping+.002)*1000, "tv_receive_ms": tv_stamp(ping+.004)}
            self.clock.tv_report(state="playing", rating_key="1", title="Up", position_ms=second*1000,
                                 elapsed_ms=tv_stamp(sent), sample_elapsed_ms=tv_stamp(sent), exchange=exchange)
            if second >= 6:
                self.assertLess(abs(self.clock.current().position(self.t)-(self.t-100)), .03)
        self.assertEqual(len(self.clock.reanchors), 1)
        self.assertAlmostEqual(self.clock.clock_rate, 1/tv_rate, places=5)



class SetupAndPartTests(ControllerBase):
    def metadata(self, part="9", fmt="1080/sdr/h264/directplay/aac"):
        info = {"rating_key": "1", "title": "Up", "state": "playing", "view_offset_ms": 10000,
                "part_id": part, "part_key": "/library/parts/" + part + "/file.mkv", "media_id": "7",
                "format_key": fmt, "duration_ms": 10000}
        self.sync.clock.plex_report(info)
        return info

    def test_calibration_saves_format_and_nudges_running_player_instantly(self):
        source = self.metadata()
        light_tracks.save("1", np.zeros((300, 3), np.uint8), "Up", source={**source, "fps": 30})
        self.play()
        self.tick()
        player = FakePlayer.instances[-1]
        self.assertEqual(player.fps, 30)
        self.assertTrue(self.sync.nudge(5)[0])
        self.assertEqual(player.lead_ms, 5)
        self.assertEqual(light_tracks.film_offset("1"), 0)
        self.assertEqual(self.sync.global_offset(), 0)
        self.metadata(fmt="4k/dv/hevc/directplay/truehd")
        self.assertEqual(self.sync.effective_offset("1"), 0)
        self.sync.nudge(25)
        self.metadata()
        self.assertEqual(self.sync.effective_offset("1"), 5)
        self.assertEqual(light_sync.load_state()["format_offsets"][source["format_key"]], 5)
        self.sync.reset_offset()
        self.assertEqual(self.sync.effective_offset("1"), 0)

    def test_mismatched_part_never_builds_on_its_own_and_does_not_play_wrong_track(self):
        light_tracks.save("1", np.zeros((300, 3), np.uint8), "Up", source={**self.metadata(), "fps": 30})
        self.metadata(part="42")
        self.play()
        with patch.object(light_tracks, "start_build", return_value=(True, "Building")) as build:
            self.tick()
            build.assert_not_called()  # playing a temp-4K copy must not start a build
            self.assertFalse(self.sync.running)
            self.assertTrue(self.sync.status()["other_copy_has_track"])
            self.assertIn("another copy", self.sync.reason)
            self.assertEqual(self.sync.build_playing(), (True, "Building"))  # the iPad's button
        film = build.call_args.args[0]
        self.assertNotIn("quiet", build.call_args.kwargs)
        self.assertEqual((film["part_id"], film["base_rating_key"]), ("42", "1"))
        light_tracks.save(film["rating_key"], np.zeros((300, 3), np.uint8), "Up",
                          source={**film, "fps": 30, "start_time_s": 1.25})
        self.tick()
        self.assertTrue(self.sync.running)
        self.assertEqual(FakePlayer.instances[-1].track_key, film["rating_key"])
        self.assertEqual(self.sync.status()["diagnostics"]["track_part"], "42")

    def test_plex_alone_never_starts_light_sync(self):
        self.metadata()
        self.tick()
        self.assertFalse(self.sync.running)
        self.assertIn("TV clock", self.sync.reason)

    def test_buffering_does_not_count_as_playing_clock_acquisition(self):
        self.sync.clock.tv_report(state="buffering", title="Up", rating_key="1", position_ms=0,
                                  elapsed_ms=920000, sample_elapsed_ms=920000,
                                  exchange=FilteredClockTests.exchange(999.9))
        self.t = 1010
        self.sync.clock.tv_report(state="playing", title="Up", rating_key="1", position_ms=0,
                                  elapsed_ms=930000, sample_elapsed_ms=930000,
                                  exchange=FilteredClockTests.exchange(1009.9))
        self.assertEqual(self.sync.clock.acquire_until, 1013)
        self.metadata()
        self.tick()
        self.assertFalse(self.sync.running)
        self.assertIn("Acquiring", self.sync.reason)

    def test_initial_clock_acquisition_holds_lights_until_three_seconds(self):
        for i in range(4):
            self.t = 1000 + i
            self.sync.clock.tv_report(state="playing", title="Up", rating_key="1", position_ms=(10+i)*1000,
                                      elapsed_ms=(920+i)*1000, sample_elapsed_ms=(920+i)*1000,
                                      exchange=FilteredClockTests.exchange(self.t-.1))
            source=self.metadata()
            if i == 0:
                light_tracks.save("1", np.zeros((300,3),np.uint8), "Up", source={**source,"fps":30})
            self.tick()
            self.assertEqual(self.sync.running, i >= 3)

    def test_strip_latency_persists_separately(self):
        with patch.dict(os.environ, {"LIGHT_SYNC_STRIPS": "strip,desk"}):
            self.sync.nudge_light("desk", 25)
            self.assertEqual(light_sync.load_state()["light_offsets"], {"desk": 25})
            with self.assertRaises(ValueError):
                self.sync.nudge_light("somewhere-else", 25)


class CalibrationTimelineTests(unittest.TestCase):
    def test_three_minutes_irregular_cuts_and_no_flash_budget_violation(self):
        from app.light_sync_calibration import timeline
        cuts = timeline()
        self.assertEqual(sum(c["frames"] for c in cuts), 180 * 30)
        self.assertGreater(len({c["frames"] for c in cuts[:-1]}), 4)
        self.assertTrue(all(c["frames"] >= 39 for c in cuts[:-1]))
        self.assertEqual(cuts[-1]["rgb"], [0, 0, 0])
        self.assertEqual(cuts, timeline())


class TVSessionSelectionTests(unittest.TestCase):
    def test_tv_title_selects_metadata_when_another_movie_is_playing_on_mac(self):
        mac = {"type": "movie", "rating_key": "9347", "title": "The Lego Ninjago Movie",
               "player": {"product": "Plex for Mac"}}
        tv = {"type": "movie", "rating_key": "9234", "title": "Light Sync Test",
              "player": {"product": "Plex for Android (TV)"}}
        report = light_sync.Playback("", "Light Sync Test", "playing", 0, 1, 100, "tv")
        self.assertIs(light_sync.pick_session([mac, tv], tv=report), tv)
        self.assertIsNone(light_sync.pick_session([mac], tv=report))
        self.assertIsNone(light_sync.pick_session([mac, tv], player="some other room", tv=report))

    def test_same_film_on_two_players_uses_android_tv_metadata(self):
        tv = {"type": "movie", "rating_key": "1", "title": "Up",
              "player": {"product": "Plex for Android (TV)"}}
        mac = {**tv, "player": {"product": "Plex for Mac"}}
        report = light_sync.Playback("1", "Up", "playing", 0, 1, 100, "tv")
        self.assertIs(light_sync.pick_session([mac, tv], tv=report), tv)
        self.assertIsNone(light_sync.pick_session([mac, mac], tv=report))
