"""Voice satellites: an iPad lends the room voice its mic and speaker."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from fastapi.testclient import TestClient

from app import server
from brain import voice_presence
from speech import room_sounds, room_voice, room_volume, satellites


WAKE = 7  # a frame filled with this "says" Hey JARVIS to FakeDetector


class FakeDetector:
    """Scores 0.9 on a WAKE frame, 0 otherwise."""

    def __init__(self) -> None:
        self._model = Mock()
        self._model.predict.side_effect = lambda f: {"hey_jarvis": 0.9 if f[0] == WAKE else 0.0}
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1


class QuietStream:
    def read(self, timeout=None):  # noqa: ANN001
        time.sleep(0.005)
        return np.zeros(1280, dtype=np.int16)

    def drain(self) -> None:
        pass

    def get_recent_audio(self, seconds):  # noqa: ANN001
        return np.zeros(0, dtype=np.int16)


def frame_bytes(value: int, frames: int = 1) -> bytes:
    return np.full(1280 * frames, value, dtype="<i2").tobytes()


class SatelliteStreamTests(unittest.TestCase):
    def test_audio_is_cut_into_80ms_frames(self) -> None:
        sat = satellites.Satellite("bed", Mock())
        data = frame_bytes(3, 2) + frame_bytes(3)[:1000]  # 2 whole frames and a bit
        sat.feed(data[:3000])
        sat.feed(data[3000:])
        first, second = sat.read(0), sat.read(0)
        self.assertEqual((first.size, second.size), (1280, 1280))
        self.assertIsNone(sat.read(0))
        self.assertEqual(sat.get_recent_audio(1.0).size, 2560)
        sat.feed(frame_bytes(3))  # the leftover half frame completes on the next feed
        self.assertIsNotNone(sat.read(0))

    def test_unread_audio_is_bounded(self) -> None:
        sat = satellites.Satellite("bed", Mock())
        sat.feed(frame_bytes(1, satellites._QUEUE_FRAMES + 10))
        self.assertEqual(sat._frames.qsize(), satellites._QUEUE_FRAMES)
        sat.drain()
        self.assertIsNone(sat.read(0))

    def test_names(self) -> None:
        self.assertEqual(satellites.clean_name(" Bed "), "bed")
        self.assertIsNone(satellites.clean_name(""))
        self.assertIsNone(satellites.clean_name("../x"))


class SatelliteSpeakerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list = []
        self.sat = satellites.Satellite("bed", self.sent.append)

    def messages(self) -> list:
        return [json.loads(m)["type"] if isinstance(m, str) else "pcm" for m in self.sent]

    def test_a_clip_is_play_pcm_end(self) -> None:
        with patch.object(room_volume, "level", return_value=None):
            self.assertTrue(self.sat.play_pcm([b"\x01\x00" * 100, b"\x01\x00" * 100], 22050))
        self.assertEqual(self.messages(), ["play", "pcm", "pcm", "end"])
        self.assertEqual(json.loads(self.sent[0]), {"type": "play", "rate": 22050, "gain": 1.0})

    def test_ipad_loudness_matches_the_desk(self) -> None:
        # ALSA's mapped scale is 60 dB wide: level 8 is about -6 dB, amplitude 0.512.
        self.assertEqual(satellites.output_gain(10), 1.0)
        self.assertEqual(satellites.output_gain(8), 0.512)
        self.assertEqual(satellites.output_gain(3), 0.027)
        with patch.dict("os.environ", {"ROOM_VOLUME_MAPPED": "0"}):
            self.assertEqual(satellites.output_gain(8), 0.8)
        with patch.object(room_volume, "level", return_value=4):  # the night level, say
            self.sat.play_pcm([b"\x00\x00"], 22050)
        self.assertEqual(json.loads(self.sent[0])["gain"], 0.064)

    def test_an_interrupt_stops_the_ipad(self) -> None:
        stop = __import__("threading").Event()
        stop.set()
        self.assertFalse(self.sat.play_pcm([b"\x00\x00" * 50], 22050, stop))
        self.assertEqual(self.messages(), ["play", "end", "stop"][:2])  # nothing sent, nothing to stop
        self.sent.clear()
        long_clip = b"\x00\x00" * 22050  # one second
        later = __import__("threading").Timer(0.1, self.sat.stop)
        later.start()
        started = time.monotonic()
        self.sat.play_pcm([long_clip], 22050)
        self.assertLess(time.monotonic() - started, 0.8)  # cut off, not played out
        self.assertIn("stop", self.messages())


    def test_sounds_go_to_the_ipad(self) -> None:
        satellites._sound_cache.clear()
        self.addCleanup(satellites._sound_cache.clear)
        with tempfile.TemporaryDirectory() as tmp, patch.object(room_sounds, "_DIR", Path(tmp)):
            self.sat.play_sound("listen", wait=True)
        self.assertEqual(self.messages(), ["play", "pcm", "end"])


class FakeDesk:
    """aplay's stdin, recorded."""

    def __init__(self) -> None:
        self.stdin = __import__("io").BytesIO()
        self.stdin.close = lambda: None  # keep what was written readable


class EverywhereTests(unittest.TestCase):
    """The desk and every iPad play the same audio at once, at matched loudness."""

    def setUp(self) -> None:
        satellites.reset_for_tests()
        self.addCleanup(satellites.reset_for_tests)
        self.sent: list = []
        self.sat = satellites.register("bed", self.sent.append)
        self.desk = FakeDesk()
        patch.object(satellites, "_open_desk", return_value=self.desk).start()
        self.closed = patch.object(satellites, "_close_desk").start()
        patch.object(room_volume, "level", return_value=8).start()
        patch.dict("os.environ", {"SATELLITE_SYNC_MS": "100"}).start()
        self.addCleanup(patch.stopall)

    def kinds(self) -> list:
        return [json.loads(m)["type"] if isinstance(m, str) else "pcm" for m in self.sent]

    def test_one_clip_on_both_speakers(self) -> None:
        clip = b"\x01\x00" * 220
        self.assertTrue(satellites.play_everywhere([clip], 22050))
        self.assertEqual(self.kinds(), ["play", "pcm", "end"])
        self.assertEqual(json.loads(self.sent[0])["gain"], 0.512)  # the desk's level 8
        written = self.desk.stdin.getvalue()
        lead = b"\0\0" * int(22050 * 0.1)  # the desk waits for the iPad's delay
        self.assertEqual(written, lead + clip)
        self.closed.assert_called_once_with(self.desk, False)

    def test_spoken_once_for_the_whole_room(self) -> None:
        with patch("speech.remote_voice.pcm_stream", return_value=iter([b"\x00\x00" * 10])) as voice:
            self.assertTrue(satellites.say_everywhere("Good evening, sir."))
        voice.assert_called_once()  # one synthesis, not one per speaker
        self.assertEqual(self.kinds(), ["play", "pcm", "end"])

    def test_a_stop_cuts_every_speaker(self) -> None:
        def chunks():  # noqa: ANN202
            yield b"\x00\x00" * 10
            satellites.stop_all()
            yield b"\x00\x00" * 10
        satellites.play_everywhere(chunks(), 22050)
        self.assertEqual(self.kinds(), ["play", "pcm", "stop", "end", "stop"])
        self.closed.assert_called_once_with(self.desk, True)

    def test_streamed_reply_sentence_by_sentence(self) -> None:
        with patch.object(satellites, "say_everywhere", return_value=True) as say:
            satellites.say_stream_everywhere(iter(["Good evening, sir. ", "The lights ", "are off."]))
        self.assertEqual([c.args[0] for c in say.call_args_list], ["Good evening, sir.", "The lights are off."])


class ArbitrationTests(unittest.TestCase):
    """Two mics hear "Hey JARVIS": the clearer one answers, on its own speaker."""

    def setUp(self) -> None:
        satellites.reset_for_tests()
        self.addCleanup(satellites.reset_for_tests)
        voice_presence.reset_for_tests()
        patch.dict("os.environ", {"SATELLITE_ARBITRATION_MS": "60", "WAKE_CONSECUTIVE": "2"}).start()
        patch.object(room_sounds, "play").start()
        patch.object(room_volume, "prepare").start()
        self.addCleanup(patch.stopall)
        self.runtime = Mock(last_used_tools=False)
        self.runtime.pop_due_reminders.return_value = []
        self.voice = room_voice.RoomVoice(self.runtime, detector=Mock(), final=Mock(), live=Mock(),
                                          satellite_detector=FakeDetector)
        self.voice._wake_ringing = lambda: False
        self.sent: list = []
        self.sat = satellites.register("bed", self.sent.append)

    def desk_hears(self, score: float):  # noqa: ANN201
        calls = {"n": 0}

        def heard(frame):  # noqa: ANN001, ANN202
            calls["n"] += 1
            if calls["n"] == 1:
                self.voice._last_wake_score = score
                return True
            return False
        return patch.object(self.voice, "_heard_wake", side_effect=heard)

    def test_the_bedside_ipad_answers_when_it_heard_best(self) -> None:
        self.sat.feed(frame_bytes(WAKE, 2))
        with self.desk_hears(0.6):
            self.assertTrue(self.voice._wait_for_wake(QuietStream()))
        self.assertIs(self.voice._source, self.sat)
        self.assertIs(self.voice._in(), self.sat)

    def test_the_desk_answers_when_it_heard_best(self) -> None:
        self.sat.feed(frame_bytes(WAKE, 2))
        with self.desk_hears(0.97):
            self.assertTrue(self.voice._wait_for_wake(QuietStream()))
        self.assertIsNone(self.voice._source)

    def test_the_satellite_alone(self) -> None:
        self.sat.feed(frame_bytes(WAKE, 2))
        with patch.object(self.voice, "_heard_wake", return_value=False):
            self.assertTrue(self.voice._wait_for_wake(QuietStream()))
        self.assertIs(self.voice._source, self.sat)

    def test_with_a_satellite_everything_plays_everywhere(self) -> None:
        self.voice._source = self.sat
        self.voice.stream = QuietStream()
        with patch.object(satellites, "say_everywhere", return_value=True) as say, \
                patch.object(satellites, "sound_everywhere") as sound, patch("speech.tts.speak") as desk_only:
            self.voice._speak("It's 9 o'clock, sir.")
            self.voice._sound("listen")
            self.voice._say("Sleep well, sir.")
        self.assertEqual([c.args[0] for c in say.call_args_list], ["It's 9 o'clock, sir.", "Sleep well, sir."])
        sound.assert_called_once_with("listen", wait=False)
        desk_only.assert_not_called()  # the desk plays through play_everywhere instead

    def test_a_dropped_satellite_falls_back_to_the_desk(self) -> None:
        self.voice._source = self.sat
        self.voice.stream = QuietStream()
        satellites.unregister(self.sat)
        self.assertIs(self.voice._in(), self.voice.stream)
        with patch("speech.tts.speak") as desk:
            self.voice._say("Still here, sir.")
        desk.assert_called_once()

    def test_hush_stops_the_satellite(self) -> None:
        voice_presence.update("speaking", reply="x")
        with patch("speech.tts.stop_playback"):
            self.assertTrue(self.voice.hush())
        self.assertIn({"type": "stop"}, [json.loads(m) for m in self.sent if isinstance(m, str)])

    def test_the_wake_up_backup_ring_rings_the_satellite_too(self) -> None:
        self.voice._wake_ringing = lambda: True
        with patch.object(room_volume, "ring_alarm", return_value=8), patch.object(satellites, "ring_all") as ring:
            self.voice._duties(QuietStream())
        ring.assert_called_once_with("alarm", level=8)


class SatelliteSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        satellites.reset_for_tests()
        self.addCleanup(satellites.reset_for_tests)
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer t"}

    def wait_for(self, check) -> bool:  # noqa: ANN001
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if check():
                return True
            time.sleep(0.02)
        return False

    def test_mic_in_and_registry(self) -> None:
        with patch.dict("os.environ", {"JARVIS_API_TOKEN": "t", "ROOM_VOICE": "1"}):
            with self.client.websocket_connect("/api/voice/satellite?name=bed", headers=self.headers) as ws:
                self.assertEqual(ws.receive_json(), {"type": "hello", "rate": 16000})
                ws.send_bytes(frame_bytes(5))
                self.assertTrue(self.wait_for(lambda: satellites.get("bed") is not None))
                sat = satellites.get("bed")
                self.assertTrue(self.wait_for(lambda: sat._frames.qsize() == 1))
                listed = self.client.get("/api/voice/satellites", headers=self.headers).json()
                self.assertEqual([s["name"] for s in listed["satellites"]], ["bed"])
                sat.send_json({"type": "stop"})  # Core -> iPad from another thread
                self.assertEqual(ws.receive_json(), {"type": "stop"})
            self.assertTrue(self.wait_for(lambda: satellites.get("bed") is None))

    def test_room_voice_off(self) -> None:
        with patch.dict("os.environ", {"JARVIS_API_TOKEN": "t", "ROOM_VOICE": "0"}):
            with self.client.websocket_connect("/api/voice/satellite?name=bed", headers=self.headers) as ws:
                self.assertEqual(ws.receive_json(), {"type": "off"})

    def test_needs_the_token(self) -> None:
        with patch.dict("os.environ", {"JARVIS_API_TOKEN": "t", "ROOM_VOICE": "1"}):
            with self.assertRaises(Exception):
                with self.client.websocket_connect("/api/voice/satellite?name=bed",
                                                   headers={"Authorization": "Bearer nope"}) as ws:
                    ws.receive_json()


if __name__ == "__main__":
    unittest.main()
