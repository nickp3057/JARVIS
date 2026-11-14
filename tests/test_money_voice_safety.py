"""Money and voice-gate safety (2026-11-14 review): phone voice turns stay in
their session, Schwab timeouts are never "refused", cancel reaches the gate,
money by room voice needs a voiceprint match, the cash check (tests/test_trading.py) only
counts what is really there, a hub hiccup doesn't re-alert, enrolment can't deadlock, and
a guest can't move Nick's alarm.

Everything is mocked: no Schwab, no Home Assistant, no microphone, no model.
"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from brain import confirm_gate, speaker_id, voice_presence, voice_shortcuts, voice_trust
from integrations import schwab
from speech import goodnight, quick_commands, room_sounds, room_voice
from tests.test_room_voice_loop import LOUD, make_voice

ROOM_UNMEASURED = voice_trust.Speaker("nick", True, reason="voice ID off", room=True)
ROOM_MATCHED = voice_trust.Speaker("nick", True, score=0.8, reason="voiceprint match", room=True)
GUEST = voice_trust.guest("voiceprint mismatch", score=0.1)


class PhoneVoiceSessionTests(unittest.TestCase):
    """Finding 1: the phone's hold-to-talk stays in the phone's session."""

    def test_spoken_turn_in_session_stays_there(self) -> None:
        from brain import agent, event_bus, history, memory_extractor
        from brain.runtime import JarvisRuntime
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(history, "_DB_PATH", Path(tmp.name) / "history.db"):
            with patch.object(JarvisRuntime, "start_monitors"), patch.object(agent, "_get_client", return_value=None):
                core = JarvisRuntime()
            self.addCleanup(memory_extractor.remove_listener, core._on_memory_change)
            self.addCleanup(event_bus.reset_for_tests)
            confirm_gate.clear_for_tests()
            self.addCleanup(confirm_gate.clear_for_tests)
            phone = history.start_new_session()
            seen = []

            def turn(text):
                seen.append(core.agent._session_id)
                yield confirm_gate.propose(core.agent._session_id, "sell 1 share", lambda: "sold", money=True)

            with patch.object(core.agent, "respond_streaming", side_effect=turn):
                list(core.respond_streaming_in_session(phone, "sell one share", spoken=True))
            self.assertEqual(seen, [phone])
            self.assertNotEqual(phone, core.room_session_id)
            self.assertEqual(len(confirm_gate.pending(phone)), 1)
            self.assertEqual(confirm_gate.pending(core.room_session_id), [])


class SchwabTransportTests(unittest.TestCase):
    """Finding 2 (the integration side) and finding 6."""

    def setUp(self) -> None:
        for p in (patch.object(schwab, "account_hash", return_value="HASH"),
                  patch.object(schwab, "access_token", return_value="tok")):
            p.start()
            self.addCleanup(p.stop)

    def test_lost_answer_is_unknown_and_no_connection_is_refused(self) -> None:
        with patch.object(schwab.requests, "post", side_effect=schwab.requests.ReadTimeout("slow")):
            with self.assertRaises(schwab.OrderOutcomeUnknown):
                schwab.place_order("1234", {})
        with patch.object(schwab.requests, "post", side_effect=schwab.requests.ConnectionError("reset")):
            with self.assertRaises(schwab.OrderOutcomeUnknown):
                schwab.place_order("1234", {})
        with patch.object(schwab.requests, "post", side_effect=schwab.requests.ConnectTimeout("no route")):
            with self.assertRaises(RuntimeError) as caught:
                schwab.place_order("1234", {})
            self.assertNotIsInstance(caught.exception, schwab.OrderOutcomeUnknown)
        ok = Mock(status_code=201, headers={}, text="")
        with patch.object(schwab.requests, "post", return_value=ok):
            self.assertEqual(schwab.place_order("1234", {}), "")  # no "?" id

    def test_recent_orders_carry_the_account(self) -> None:
        raw = [{"orderId": 5, "accountNumber": 11112222, "status": "WORKING", "quantity": 2, "filledQuantity": 1,
                "price": 10.5, "enteredTime": "2026-11-14T15:00:00+0000",
                "orderLegCollection": [{"instruction": "SELL", "instrument": {"symbol": "VOO"}}]},
               {"orderId": 6, "accountNumber": 11112222, "status": "FILLED", "quantity": 1,
                "orderLegCollection": [{"instruction": "BUY", "instrument": {"symbol": "VOO"}}]}]
        with patch.object(schwab, "_get", return_value=raw):
            recent = schwab.recent_orders()
            still_open = schwab.open_orders()
        self.assertEqual([o["id"] for o in recent], ["5", "6"])
        self.assertEqual([o["id"] for o in still_open], ["5"])
        self.assertEqual((still_open[0]["account"], still_open[0]["filled"], still_open[0]["side"]),
                         ("2222", 1, "sell"))

    def test_hub_hiccup_keeps_the_logged_out_clock(self) -> None:
        def lent(body, status):
            return Mock(status_code=status, headers={"Content-Type": "application/json"}, json=Mock(return_value=body))

        env = patch.dict(os.environ, {"SCHWAB_HUB_URL": "https://hub.example/token", "SCHWAB_HUB_SECRET": "s" * 40})
        env.start()
        self.addCleanup(env.stop)
        schwab._borrowed.clear()
        self.addCleanup(schwab._borrowed.clear)
        with patch.object(schwab.requests, "get", return_value=lent({"login_required": True}, 409)):
            schwab._borrow(force=True)
        since = schwab._borrowed["required_since"]
        with patch.object(schwab.requests, "get", side_effect=schwab.requests.ConnectionError("down")), \
                patch.object(schwab.time, "time", return_value=since + 300):
            schwab._borrow(force=True)
        with patch.object(schwab.requests, "get", return_value=lent({"login_required": True}, 409)), \
                patch.object(schwab.time, "time", return_value=since + 600):
            schwab._borrow(force=True)
        self.assertEqual(schwab._borrowed["required_since"], since)  # same alert key: no second alert


class MoneyVoiceTrustTests(unittest.TestCase):
    """Finding 4: money by room voice needs a positive voiceprint match."""

    def setUp(self) -> None:
        confirm_gate.clear_for_tests()
        self.addCleanup(confirm_gate.clear_for_tests)

    def test_who_may_move_money(self) -> None:
        self.assertTrue(voice_trust.may_move_money())  # authenticated channel (app, desktop)
        for speaker, allowed in ((ROOM_UNMEASURED, False), (ROOM_MATCHED, True), (GUEST, False)):
            with voice_trust.speaking(speaker):
                self.assertEqual(voice_trust.may_move_money(), allowed, speaker)
        with voice_trust.speaking(ROOM_UNMEASURED):
            self.assertIn(voice_trust.MONEY_REFUSAL_LINE, voice_trust.refusal("place_trade"))
            self.assertIn("Refused", voice_trust.refusal("decide_investment"))
            self.assertIsNone(voice_trust.refusal("activate_home_action"))  # everything else as before
        with voice_trust.speaking(ROOM_MATCHED):
            self.assertIsNone(voice_trust.refusal("place_trade"))

    def test_voice_id_off_or_unenrolled_is_unmeasured(self) -> None:
        audio = np.zeros(16000, dtype=np.int16)
        with patch.object(speaker_id, "enabled", return_value=False):
            verdict = speaker_id.identify(audio)
        self.assertTrue(verdict.verified and verdict.room)
        with voice_trust.speaking(verdict):
            self.assertFalse(voice_trust.may_move_money())
        with patch.object(speaker_id, "enabled", return_value=True), \
                patch.object(speaker_id, "enrolled", return_value=False):
            verdict = speaker_id.identify(audio)
        with voice_trust.speaking(verdict):
            self.assertFalse(voice_trust.may_move_money())

    def test_unconfirmed_room_yes_holds_a_money_proposal(self) -> None:
        ran = []
        confirm_gate.propose("room", "sell 1 SPCX", lambda: ran.append(1) or "sold", money=True)
        with voice_trust.speaking(ROOM_UNMEASURED):
            reply = confirm_gate.conversation_action("yes", "room")
        self.assertIn(voice_trust.MONEY_REFUSAL_LINE, reply)
        self.assertEqual(ran, [])
        self.assertEqual(len(confirm_gate.pending("room")), 1)  # still open for the app
        self.assertFalse(confirm_gate.awaiting_yes("room"))  # but a later "okay" can't approve it
        code = confirm_gate.pending("room")[0]["code"]
        with voice_trust.speaking(ROOM_MATCHED):
            self.assertEqual(confirm_gate.conversation_action(f"approve {code}", "room"), "sold")
        self.assertEqual(ran, [1])

    def test_non_money_proposals_still_approve_by_room_voice(self) -> None:
        confirm_gate.propose("room", "restart Radarr", lambda: "restarted")
        with voice_trust.speaking(ROOM_UNMEASURED):
            self.assertEqual(confirm_gate.conversation_action("yes", "room"), "restarted")

    def test_reply_kind(self) -> None:
        for text, kind in (("yes", "yes"), ("Okay.", "yes"), ("never mind", "cancel"), ("forget it", "cancel"),
                           ("nothing", "cancel"), ("approve 1234", "codes"), ("deny 1234", "codes"),
                           ("turn on the lights", None)):
            self.assertEqual(confirm_gate.reply_kind(text), kind, text)


class SpeakerIdLockTests(unittest.TestCase):
    """Finding 7: take_sample with no model used to deadlock on its own lock."""

    def test_take_sample_without_a_model_returns(self) -> None:
        with patch.object(speaker_id, "_SAMPLE_RATE", 16000), \
                patch.object(speaker_id, "embed", return_value=None), \
                patch.object(speaker_id, "_extractor", None), \
                patch.object(speaker_id, "model_path", return_value=Path("/nonexistent/model.onnx")):
            speaker_id._enrolling = {"needed": 3, "samples": [], "until": time.monotonic() + 60}
            self.addCleanup(setattr, speaker_id, "_enrolling", None)
            out = []
            worker = threading.Thread(target=lambda: out.append(speaker_id.take_sample(np.zeros(20000))),
                                      daemon=True)
            worker.start()
            worker.join(5)
        self.assertFalse(worker.is_alive(), "take_sample deadlocked")
        self.assertIn("voice model isn't installed", out[0])


class RoomVoiceGateTests(unittest.TestCase):
    """Findings 3 and 8, through RoomVoice._handle with everything mocked."""

    def setUp(self) -> None:
        voice_presence.reset_for_tests()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(voice_shortcuts, "_FILE", Path(tmp.name) / "shortcuts.json").start()
        self.sounds = patch.object(room_sounds, "play").start()
        self.speak = patch("speech.tts.speak").start()
        patch("speech.tts.stop_playback").start()
        patch.object(room_voice.time, "sleep").start()
        patch.dict(os.environ, {"STREAMING_TTS": "false"}).start()
        self.speaker = ROOM_UNMEASURED
        patch.object(speaker_id, "identify", side_effect=lambda audio: self.speaker).start()
        patch.object(speaker_id, "enrolling", return_value=False).start()
        self.addCleanup(patch.stopall)
        confirm_gate.clear_for_tests()
        self.addCleanup(confirm_gate.clear_for_tests)
        self.audio = np.concatenate([LOUD] * 5)
        self.runtime = Mock(last_used_tools=False, room_session_id="room-s")
        self.ran = []
        confirm_gate.propose("room-s", "buy 1 VOO", lambda: self.ran.append(1) or "bought", money=True)

    def test_cancel_reaches_the_gate(self) -> None:
        for said in ("never mind", "forget it", "cancel"):
            confirm_gate.clear_for_tests()
            confirm_gate.propose("room-s", "buy 1 VOO", lambda: "bought", money=True)
            make_voice(said, self.runtime)._handle(self.audio)
            self.assertEqual(confirm_gate.pending("room-s"), [], said)
            self.assertIn("Cancelled", self.speak.call_args.args[0])
        self.runtime.respond.assert_not_called()

    def test_an_unrelated_quick_command_disarms_the_yes(self) -> None:
        with patch.object(quick_commands, "run", return_value=quick_commands.QuickResult(True, "light.lamp")):
            make_voice("lamp on", self.runtime)._handle(self.audio)
        self.assertFalse(confirm_gate.awaiting_yes("room-s"))
        self.runtime.respond.return_value = "Of course, sir."
        with patch.object(quick_commands, "run", return_value=None):
            make_voice("okay", self.runtime)._handle(self.audio)
        self.assertEqual(self.ran, [])

    def test_a_yes_goes_to_the_model_turn_in_the_room_session(self) -> None:
        self.runtime.respond.return_value = voice_trust.MONEY_REFUSAL_LINE
        with patch.object(quick_commands, "run") as quick:
            make_voice("okay", self.runtime)._handle(self.audio)
        quick.assert_not_called()
        self.runtime.respond.assert_called_once_with("okay", spoken=True, room=True)

    def test_a_guest_gets_the_scene_but_not_the_alarm_question(self) -> None:
        self.speaker = GUEST
        confirm_gate.clear_for_tests()
        with patch.object(goodnight.wake_alarm, "set_alarm") as set_alarm, \
                patch.object(goodnight.wake_alarm, "skip") as skip, \
                patch.object(room_voice.RoomVoice, "_lights_out") as lights, \
                patch.object(room_voice.RoomVoice, "_listen_for_answer", return_value="skip it") as listen:
            make_voice("goodnight", self.runtime)._handle(self.audio)
        lights.assert_called_once()
        listen.assert_not_called()
        set_alarm.assert_not_called()
        skip.assert_not_called()

    def test_nick_still_gets_the_alarm_question_with_voice_id_off(self) -> None:
        confirm_gate.clear_for_tests()
        with patch.object(goodnight, "run", return_value="kept") as run, \
                patch.object(goodnight, "is_goodnight", return_value=True):
            make_voice("goodnight", self.runtime)._handle(self.audio)
        self.assertTrue(run.call_args.kwargs["ask_alarm"])

    def test_a_guest_cannot_end_the_wake_up_ring(self) -> None:
        self.speaker = GUEST
        confirm_gate.clear_for_tests()
        with patch.object(room_voice.RoomVoice, "_wake_ringing", return_value=True), \
                patch.object(room_voice.RoomVoice, "_dismiss_alarms") as dismiss:
            make_voice("stop", self.runtime)._handle(self.audio)
            dismiss.assert_not_called()
            self.speaker = ROOM_UNMEASURED  # Nick with voice ID off: as before
            make_voice("stop", self.runtime)._handle(self.audio)
            dismiss.assert_called_once()


if __name__ == "__main__":
    unittest.main()
