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
from brain import light_sync, light_tracks, tools
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

    def test_ffmpeg_command_reads_http_decodes_at_10fps_without_audio(self) -> None:
        command = light_tracks.ffmpeg_command("ffmpeg", "http://plex/library/parts/1/file.mkv?X-Plex-Token=t")
        joined = " ".join(command)
        self.assertIn("-hwaccel auto", joined)
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


class _FakeProc:
    def __init__(self, data: bytes, code: int = 0, err: bytes = b"") -> None:
        self.stdout = io.BytesIO(data)
        self.stderr = io.BytesIO(err)
        self.code = code
        self.terminated = False

    def wait(self, timeout=None):  # noqa: ANN001
        return self.code

    def terminate(self) -> None:
        self.terminated = True


class BuildTests(TrackStoreDir):
    FILM = {"rating_key": "7", "title": "Cars 2", "year": 2011, "duration_ms": 2000, "part_key": "/library/parts/9/f.mkv"}

    def setUp(self) -> None:
        super().setUp()
        self.notes = []
        light_tracks.set_notifier(lambda *a: self.notes.append(a))
        self.addCleanup(light_tracks.set_notifier, None)
        for p in (patch.object(light_tracks, "ffmpeg_exe", return_value="/bin/ffmpeg"),
                  patch.dict(os.environ, {"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "sekrit"})):
            p.start()
            self.addCleanup(p.stop)

    def _wait(self) -> None:
        for _ in range(200):
            if light_tracks.building() is None:
                return
            time.sleep(0.01)
        self.fail("build never finished")

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

    def test_existing_track_and_one_at_a_time(self) -> None:
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
            self.assertFalse(ok)
            self.assertIn("One at a time", message)
            self.assertEqual(light_tracks.building()["title"], "Cars 2")
            self.assertTrue(light_tracks.cancel_build())
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
        self.t += 2.0  # TV stale: Plex takes over (it was polled 3.5 s ago)
        self.assertEqual(self.clock.current().source, "plex")
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

    def __init__(self, track, rating_key, clock, tune, sender, now=None, on_exit=None):  # noqa: ANN001
        self.rating_key, self.started, self.stopped = rating_key, False, False
        self._stop = threading.Event()
        FakePlayer.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self, wait=2.0) -> None:  # noqa: ANN001
        self.stopped = True
        self._stop.set()


class ControllerTests(TrackStoreDir):
    def setUp(self) -> None:
        super().setUp()
        FakePlayer.instances = []
        self.t = 1000.0
        self.sync = light_sync.LightSync(sender_factory=FakeSender, now=lambda: self.t)
        light_tracks.save("1", np.zeros((100, 3), np.uint8), "Up")
        self.restored = []
        self.follow = []
        self.barn = False
        for p in (patch.object(light_sync, "Player", FakePlayer),
                  patch.object(light_sync, "_restore_movie_scene", side_effect=lambda: self.restored.append(1)),
                  patch.object(light_sync, "_pause_follow", side_effect=lambda: self.follow.append("off") or True),
                  patch.object(light_sync, "_resume_follow", side_effect=lambda: self.follow.append("on")),
                  patch.object(light_sync, "_barn_door_engaged", side_effect=lambda: self.barn),
                  patch.object(light_sync, "_movie_mode", return_value=False),
                  patch.dict(os.environ, {"LIGHT_SYNC": "1"})):
            p.start()
            self.addCleanup(p.stop)

    def play(self, key="1", title="Up", state="playing") -> None:
        self.sync.clock.tv_report(state=state, position_ms=10_000, title=title, rating_key=key)

    def tick(self, movie=True, barn=False) -> None:
        self.barn = barn
        self.sync.tick(movie_mode=movie, barn_door=barn)

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
        self.assertEqual(light_sync.load_state(), {"auto": True, "intensity": "medium"})
        before = self.sync.revision()
        self.sync.set_intensity("Subtle")
        self.sync.set_auto(False)
        self.assertEqual(light_sync.load_state(), {"auto": False, "intensity": "subtle"})
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


class ApiTests(TrackStoreDir):
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
        self.assertEqual(self.client.post("/api/tv/playback", headers=without, json=report).json(),
                         {"ok": True, "used": True})
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
