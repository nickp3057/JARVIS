"""Voice-path speed (2026-11-09): kept-alive connections warmed by the wake
word, voice ID worked out while the transcript is fetched, and the lens
state pushed on the home stream instead of polled."""
import asyncio
import json
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import requests

from app import server
from brain import agent as agent_mod
from brain import camera, voice_presence, voice_trust
from brain.runtime import JarvisRuntime
from speech import cloud_http, quick_commands, room_sounds, room_voice, stt, tts


class CloudHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock()
        patch.object(cloud_http, "_session", self.session).start()
        self.addCleanup(patch.stopall)

    def test_one_session_is_shared(self) -> None:
        self.assertIs(cloud_http.session(), self.session)
        self.session.post.return_value = "ok"
        self.assertEqual(cloud_http.post("https://api.elevenlabs.io/v1/x", timeout=3), "ok")
        self.session.post.assert_called_once_with("https://api.elevenlabs.io/v1/x", timeout=3)

    def test_a_stale_kept_alive_connection_is_retried_once(self) -> None:
        self.session.post.side_effect = [requests.ConnectionError("closed"), "ok"]
        self.assertEqual(cloud_http.post("https://api.elevenlabs.io/v1/x"), "ok")
        self.session.post.side_effect = requests.ConnectionError("down")
        with self.assertRaises(requests.ConnectionError):
            cloud_http.post("https://api.elevenlabs.io/v1/x")
        self.assertEqual(self.session.post.call_count, 4)  # two tries each, never more
        self.session.post.side_effect = requests.Timeout("slow")  # a slow reply isn't sent twice
        with self.assertRaises(requests.Timeout):
            cloud_http.post("https://api.elevenlabs.io/v1/x")
        self.assertEqual(self.session.post.call_count, 5)

    def test_warm_runs_in_the_background_and_never_raises(self) -> None:
        reached = threading.Event()
        self.session.head.side_effect = lambda *a, **k: (reached.set(), (_ for _ in ()).throw(requests.Timeout()))[1]
        cloud_http.warm("https://api.elevenlabs.io/")
        self.assertTrue(reached.wait(2))
        self.session.head.assert_called_once_with("https://api.elevenlabs.io/", timeout=3)

    def test_speech_clouds_use_it(self) -> None:
        self.session.post.return_value = Mock(json=Mock(return_value={"text": " lights on "}))
        with patch.dict("os.environ", {"STT_PROVIDER": "elevenlabs", "ELEVENLABS_API_KEY": "k"}):
            self.assertEqual(stt.cloud_transcribe(np.zeros(1600, dtype=np.float32), "elevenlabs"), "lights on")
            with patch.object(cloud_http, "warm") as warm:
                stt.warm_cloud()
            warm.assert_called_once_with("https://api.elevenlabs.io/")
        with patch.dict("os.environ", {"STT_PROVIDER": "local"}), patch.object(cloud_http, "warm") as warm:
            stt.warm_cloud()  # local Whisper: nothing to connect to
            with patch.object(tts, "ELEVENLABS_API_KEY", ""):
                tts.warm_cloud()
        warm.assert_not_called()


class BrainConnectionTests(unittest.TestCase):
    def test_the_api_connection_outlives_the_gap_between_turns(self) -> None:
        with patch("anthropic.DefaultHttpxClient") as http_client:
            kept = agent_mod._kept_alive()
        self.assertIs(kept["http_client"], http_client.return_value)
        self.assertEqual(http_client.call_args.kwargs["limits"].keepalive_expiry, agent_mod._KEEPALIVE_S)
        self.assertGreaterEqual(agent_mod._KEEPALIVE_S, 60)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertIsNotNone(agent_mod._get_client())  # the real SDK accepts it
        with patch("anthropic.DefaultHttpxClient", side_effect=TypeError("older SDK")):
            self.assertEqual(agent_mod._kept_alive(), {})  # falls back to the SDK's own client

    def test_warming_lists_one_model_off_the_turn_thread(self) -> None:
        agent = agent_mod.JarvisAgent.__new__(agent_mod.JarvisAgent)
        done = threading.Event()
        quick = Mock()
        quick.models.list.side_effect = lambda **kwargs: done.set()
        agent._client = Mock(with_options=Mock(return_value=quick))
        agent.warm_connection()
        self.assertTrue(done.wait(2))
        agent._client.with_options.assert_called_once_with(timeout=4.0, max_retries=0)
        quick.models.list.side_effect = RuntimeError("offline")  # never reaches the caller
        agent.warm_connection()
        agent._client = None
        agent.warm_connection()  # brain offline: nothing to do

    def test_runtime_warm_reaches_the_agent(self) -> None:
        runtime = JarvisRuntime.__new__(JarvisRuntime)
        runtime.agent = Mock()
        runtime.warm()
        runtime.agent.warm_connection.assert_called_once_with()
        runtime.agent = object()  # a test double without it
        runtime.warm()


class RoomVoiceSpeedTests(unittest.TestCase):
    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        patch.object(room_sounds, "play").start()
        patch("speech.tts.speak").start()
        self.addCleanup(patch.stopall)
        self.audio = np.full(8000, 4000, dtype=np.int16)

    def voice(self, final, runtime=None) -> room_voice.RoomVoice:  # noqa: ANN001
        return room_voice.RoomVoice(runtime or Mock(last_used_tools=False), detector=Mock(), final=final,
                                    live=Mock())

    def test_wake_word_warms_every_connection_the_exchange_needs(self) -> None:
        runtime = Mock()
        with patch.object(stt, "warm_cloud") as hear, patch.object(tts, "warm_cloud") as say:
            self.voice(Mock(), runtime)._warm_connections()
            hear.assert_called_once_with()
            say.assert_called_once_with()
            runtime.warm.assert_called_once_with()
            hear.side_effect = RuntimeError("no network")
            self.voice(Mock(), runtime)._warm_connections()  # a head start only: never raises

    def test_voice_id_runs_while_the_transcript_is_fetched(self) -> None:
        order: list[str] = []
        identified = threading.Event()

        class SlowCloud:
            def transcribe(self, audio):  # noqa: ANN001, ANN201
                # The transcript only comes back once voice ID has already finished.
                order.append("id done first" if identified.wait(2) else "transcript waited for nothing")
                return "lamp on"

        def identify(audio):  # noqa: ANN001, ANN202
            identified.set()
            return voice_trust.guest("voiceprint mismatch", score=0.2)

        seen = []
        with patch("brain.speaker_id.identify", side_effect=identify), \
                patch("brain.speaker_id.enrolling", return_value=False), \
                patch.object(quick_commands, "run", return_value=None), \
                patch("brain.voice_shortcuts.lookup", return_value=None):
            runtime = Mock(last_used_tools=False)
            runtime.respond.side_effect = lambda text, spoken, room: seen.append(voice_trust.current().name) or "Done, sir."
            self.voice(SlowCloud(), runtime)._handle(self.audio)
        self.assertEqual(order, ["id done first"])
        self.assertEqual(seen, ["guest"])  # the verdict still gates the turn

    def test_a_voice_id_crash_is_a_guest_not_nick(self) -> None:
        seen = []
        with patch("brain.speaker_id.identify", side_effect=RuntimeError("model gone")), \
                patch("brain.speaker_id.enrolling", return_value=False), \
                patch.object(quick_commands, "run", return_value=None), \
                patch("brain.voice_shortcuts.lookup", return_value=None), \
                self.assertLogs("jarvis.speech.room_voice", level="ERROR"):
            runtime = Mock(last_used_tools=False)
            runtime.respond.side_effect = lambda text, spoken, room: seen.append(voice_trust.current().verified) or "Hello."
            final = Mock(transcribe=Mock(return_value="what's the plan"))
            self.voice(final, runtime)._handle(self.audio)
        self.assertEqual(seen, [False])


class HomeStreamCameraTests(unittest.TestCase):
    def setUp(self) -> None:
        camera.reset_for_tests()
        self.addCleanup(camera.reset_for_tests)

    def test_the_lens_state_is_pushed_when_it_changes(self) -> None:
        async def scenario() -> list[bool]:
            request = Mock(is_disconnected=AsyncMock(return_value=False), headers={})
            with patch.object(server, "runtime"):
                response = await server.home_stream(request, _=None)
            frames = response.body_iterator
            try:
                first = await asyncio.wait_for(anext(frames), 5)
                camera.view_heartbeat()  # Nick's phone opens the live view
                second = await asyncio.wait_for(anext(frames), 5)
                camera.kill()
                third = await asyncio.wait_for(anext(frames), 5)
            finally:
                await frames.aclose()
            return [json.loads(f.split("data: ", 1)[1])["camera"] for f in (first, second, third)]

        started = time.monotonic()
        self.assertEqual(asyncio.run(scenario()), [False, True, False])
        self.assertLess(time.monotonic() - started, 3)  # a beat, not the old 1.5 s poll each way


if __name__ == "__main__":
    unittest.main()
