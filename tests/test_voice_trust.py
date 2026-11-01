"""Voice ID: guests keep lights and scenes, never memory, settings or Barn Door."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from brain import speaker_id, tools, voice_trust

GUEST = voice_trust.guest("test")


class LockedToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        fake = {name: (lambda args, n=name: self.calls.append(n) or "ok")
                for name in ("barn_door_protocol", "remember_fact", "update_setting", "activate_home_action")}
        patcher = patch.dict(tools._DISPATCH, fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_guest_is_refused_locked_tools_but_runs_home_actions(self) -> None:
        with voice_trust.speaking(GUEST):
            for name in ("barn_door_protocol", "remember_fact", "update_setting"):
                self.assertTrue(tools.execute_tool(name, {}).startswith("Refused"))
            self.assertEqual(tools.execute_tool("activate_home_action", {"kind": "scene", "name": "peace"}), "ok")
        self.assertEqual(self.calls, ["activate_home_action"])

    def test_nick_and_unwrapped_calls_run_everything(self) -> None:
        self.assertEqual(tools.execute_tool("barn_door_protocol", {}), "ok")
        with voice_trust.speaking(voice_trust.Speaker("nick", True)):
            self.assertEqual(tools.execute_tool("remember_fact", {}), "ok")
        self.assertEqual(self.calls, ["barn_door_protocol", "remember_fact"])

    def test_extra_locked_tools_from_env(self) -> None:
        with patch.dict(os.environ, {"VOICE_LOCKED_TOOLS": "activate_home_action"}), voice_trust.speaking(GUEST):
            self.assertTrue(tools.execute_tool("activate_home_action", {}).startswith("Refused"))

    def test_every_locked_tool_exists(self) -> None:
        self.assertEqual(voice_trust.LOCKED_TOOLS - set(tools._DISPATCH), set())


class AgentTests(unittest.TestCase):
    def agent(self):  # noqa: ANN201
        from brain.agent import JarvisAgent
        agent = object.__new__(JarvisAgent)
        agent._lock = threading.RLock()
        agent._client = Mock()
        agent._session_id = "s"
        agent._last_turn_tools = []
        return agent

    def test_parallel_tool_workers_keep_the_speaker(self) -> None:
        seen: list[str] = []
        uses = [SimpleNamespace(id=str(i), name="t", input={}) for i in range(3)]
        with patch("brain.agent.execute_tool", side_effect=lambda *a, **k: seen.append(voice_trust.current().name) or "x"):
            with voice_trust.speaking(GUEST):
                self.agent()._run_tools(uses, [])
        self.assertEqual(seen, ["guest"] * 3)

    def test_guest_turns_skip_memory_learning(self) -> None:
        with patch("brain.agent.memory_extractor.observe_turn") as observe:
            with voice_trust.speaking(GUEST):
                self.agent()._after_turn("call him ma'am from now on", "Noted.")
            observe.assert_not_called()
            self.agent()._after_turn("hello", "Hello, sir.")
            observe.assert_called_once()

    def test_guest_cannot_approve_a_staged_fix(self) -> None:
        with patch("brain.confirm_gate.conversation_action") as gate, \
                patch("brain.arr_stack.conversation_action") as arr, voice_trust.speaking(GUEST):
            self.assertIsNone(self.agent()._arr_stack_action("yes"))
        arr.assert_not_called()
        gate.assert_called_once_with("yes", "s", handled=True)  # disarmed, not approved

    def test_guest_turn_context_says_so(self) -> None:
        self.assertEqual(voice_trust.context_note(), "")
        with voice_trust.speaking(GUEST):
            self.assertIn("NOT recognised as Nick", voice_trust.context_note())


class FakeExtractor:
    """Vectors from the clip's first sample, so tests choose who is talking."""
    def create_stream(self):  # noqa: ANN201
        return SimpleNamespace(accept_waveform=self._take, input_finished=lambda: None)

    def _take(self, rate: int, wave: np.ndarray) -> None:
        self.last = wave

    def compute(self, stream) -> list[float]:  # noqa: ANN001
        return [1.0, 0.0] if self.last[0] > 0 else [0.0, 1.0]


def clip(nick: bool, seconds: float = 2.0) -> np.ndarray:
    audio = np.full(int(16000 * seconds), 1000 if nick else -1000, dtype=np.int16)
    return audio


class SpeakerIdTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (patch.object(speaker_id, "_PRINT", Path(tmp.name) / "voiceprint.json"),
                  patch.object(speaker_id, "_load_extractor", return_value=FakeExtractor()),
                  patch.dict(os.environ, {"VOICE_ID": "1", "VOICE_ID_SAMPLES": "3"})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(speaker_id.cancel_enrollment)

    def test_everyone_is_nick_until_enrolled_or_when_off(self) -> None:
        self.assertTrue(speaker_id.identify(clip(False)).verified)
        self.enroll()
        with patch.dict(os.environ, {"VOICE_ID": "0"}):
            self.assertTrue(speaker_id.identify(clip(False)).verified)

    def enroll(self) -> None:
        self.assertEqual(speaker_id.start_enrollment(), 3)
        self.assertEqual(speaker_id.take_sample(clip(True, 0.3)), "That one was too short, sir. A full sentence, please.")
        self.assertEqual(speaker_id.take_sample(clip(True)), "Got it. 2 more.")
        speaker_id.take_sample(clip(True))
        self.assertIn("Voiceprint saved", speaker_id.take_sample(clip(True)))
        self.assertFalse(speaker_id.enrolling())

    def test_enrolled_voice_matches_and_others_are_guests(self) -> None:
        self.enroll()
        nick, other = speaker_id.identify(clip(True)), speaker_id.identify(clip(False))
        self.assertEqual((nick.name, nick.verified), ("nick", True))
        self.assertEqual((other.name, other.verified), ("guest", False))
        self.assertLess(other.score, 0.55)

    def test_fails_closed_once_enrolled(self) -> None:
        self.enroll()
        self.assertFalse(speaker_id.identify(clip(True, 0.3)).verified)  # too short to judge
        with patch.object(speaker_id, "_load_extractor", return_value=None):
            self.assertFalse(speaker_id.identify(clip(True)).verified)  # model gone

    def test_print_from_another_model_is_ignored(self) -> None:
        self.enroll()
        with patch.dict(os.environ, {"VOICE_ID_MODEL": "/models/other.onnx"}):
            self.assertFalse(speaker_id.enrolled())

    def test_tool_status_enroll_reset(self) -> None:
        self.assertIn("No voiceprint", tools.execute_tool("voice_id", {"action": "status"}))
        self.assertIn("Enrolment armed", tools.execute_tool("voice_id", {"action": "enroll"}))
        self.assertTrue(speaker_id.enrolling())
        self.assertIn("cancelled", tools.execute_tool("voice_id", {"action": "cancel"}))
        self.enroll()
        self.assertIn("deleted", tools.execute_tool("voice_id", {"action": "reset"}))
        self.assertFalse(speaker_id.enrolled())


class RoomVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        from speech import quick_answers, quick_commands, room_sounds, room_voice
        self.room_voice = room_voice
        for p in (patch.object(room_sounds, "play"), patch.object(quick_commands, "run", return_value=None),
                  patch.object(quick_answers, "answer", return_value=None), patch("speech.tts.speak")):
            p.start()
            self.addCleanup(p.stop)

    def voice(self, runtime, said: str):  # noqa: ANN001, ANN201
        final = Mock()
        final.transcribe.return_value = said
        return self.room_voice.RoomVoice(runtime, detector=Mock(), final=final, live=Mock())

    def test_turn_runs_as_the_identified_speaker(self) -> None:
        seen = []
        runtime = Mock(last_used_tools=False)
        runtime.respond.side_effect = lambda *a, **k: seen.append(voice_trust.current().name) or "Hello."
        with patch.object(speaker_id, "identify", return_value=GUEST):
            self.voice(runtime, "Engage barn door protocol.")._handle(clip(False))
        self.assertEqual(seen, ["guest"])
        self.assertEqual(voice_trust.current().name, "nick")  # restored after the turn

    def test_enrolment_takes_samples_instead_of_commands(self) -> None:
        runtime = Mock(last_used_tools=False)
        with patch.object(speaker_id, "enrolling", side_effect=[True, True]), \
                patch.object(speaker_id, "take_sample", return_value="Got it. 2 more.") as take:
            keep_listening = self.voice(runtime, "Turn off the lights.")._handle(clip(True))
        take.assert_called_once()
        runtime.respond.assert_not_called()
        self.assertTrue(keep_listening)


class ServerChatTests(unittest.TestCase):
    def test_client_token_chats_are_guests_unless_trusted(self) -> None:
        from fastapi.testclient import TestClient
        import app.server as server
        seen = []
        core = Mock()
        core.start_session.return_value = "s"
        core.respond_in_session.side_effect = lambda *a, **k: seen.append(voice_trust.current().name) or "ok"
        with patch.object(server, "_runtime", core), patch.dict(os.environ, {"JARVIS_API_TOKEN": "admin"}), \
                patch("app.client_tokens.scopes_for", return_value=("tv", frozenset({"chat"}))), \
                TestClient(server.app) as client:
            for token in ("tv-token", "admin"):
                self.assertEqual(client.post("/chat", json={"message": "hi"},
                                             headers={"Authorization": f"Bearer {token}"}).status_code, 200)
            with patch.dict(os.environ, {"VOICE_TRUSTED_CLIENTS": "TV"}):
                client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer tv-token"})
        self.assertEqual(seen, ["guest", "nick", "nick"])


@unittest.skipUnless(os.getenv("VOICE_ID_TEST_MODEL"), "set VOICE_ID_TEST_MODEL to a TitaNet .onnx to run")
class RealModelTests(unittest.TestCase):
    def test_model_loads_and_embeds(self) -> None:
        with patch.dict(os.environ, {"VOICE_ID_MODEL": os.environ["VOICE_ID_TEST_MODEL"]}):
            speaker_id._extractor = None
            vector = speaker_id.embed((np.random.default_rng(0).standard_normal(32000) * 3000).astype(np.int16))
        self.assertIsNotNone(vector)
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
