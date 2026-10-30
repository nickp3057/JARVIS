"""Generic human-only approval gate for side-effecting fixes."""
import unittest
from unittest.mock import Mock, patch

from brain import confirm_gate as gate


class ConfirmGateTests(unittest.TestCase):
    def setUp(self) -> None:
        gate.clear_for_tests()
        self.run = Mock(return_value="done")

    def test_bare_yes_only_as_next_message(self) -> None:
        gate.propose("s", "re-search Dune", self.run)
        self.assertIsNone(gate.conversation_action("what's the weather", "s"))
        self.assertIsNone(gate.conversation_action("yes", "s"))
        self.run.assert_not_called()

    def test_yes_runs_once(self) -> None:
        gate.propose("s", "re-search Dune", self.run)
        self.assertEqual(gate.conversation_action("Yes.", "s"), "done")
        self.assertIsNone(gate.conversation_action("yes", "s"))
        self.run.assert_called_once()

    def test_code_approves_later_and_wrong_code_does_not(self) -> None:
        reply = gate.propose("s", "re-search Dune", self.run)
        code = reply.split('"approve ')[1][:4]
        gate.conversation_action("hmm", "s")
        wrong = "0000" if code != "0000" else "1111"
        self.assertIn("does not match", gate.conversation_action(f"approve {wrong}", "s"))
        self.assertEqual(gate.conversation_action(f"approve {code}", "s"), "done")

    def test_session_bound_and_expiring(self) -> None:
        with patch.object(gate.time, "monotonic", side_effect=[0, 1, 700]):
            gate.propose("s", "x", self.run)
            self.assertIsNone(gate.conversation_action("yes", "other"))
            self.assertIsNone(gate.conversation_action("yes", "s"))
        self.run.assert_not_called()

    def test_handled_message_only_disarms(self) -> None:
        gate.propose("s", "x", self.run)
        self.assertIsNone(gate.conversation_action("yes", "s", handled=True))
        self.assertIsNone(gate.conversation_action("yes", "s"))
        self.run.assert_not_called()

    def test_cancel(self) -> None:
        gate.propose("s", "x", self.run)
        self.assertIn("Cancelled", gate.conversation_action("no", "s"))
        self.assertIsNone(gate.pending_description("s"))

    def test_failure_is_reported_not_retried(self) -> None:
        self.run.side_effect = RuntimeError("boom")
        gate.propose("s", "x", self.run)
        self.assertIn("failed", gate.conversation_action("yes", "s"))
        self.assertIsNone(gate.conversation_action("approve 1234", "s"))


    def _code(self, reply: str) -> str:
        return reply.split('"approve ')[1][:4]

    def test_two_proposals_keep_both_codes(self) -> None:
        first, second = Mock(return_value="one"), Mock(return_value="two")
        code1 = self._code(gate.propose("s", "fix Deadpool 2", first))
        code2 = self._code(gate.propose("s", "fix Ministry", second))
        reply = gate.conversation_action(f"Approve {code1}. Approve {code2}.", "s")
        self.assertEqual(reply, "one two")
        first.assert_called_once()
        second.assert_called_once()

    def test_bare_yes_approves_everything_staged_that_turn(self) -> None:
        first, second = Mock(return_value="one"), Mock(return_value="two")
        gate.propose("s", "fix A", first)
        gate.propose("s", "fix B", second)
        self.assertEqual(gate.conversation_action("yes", "s"), "one two")

    def test_later_code_still_works_after_other_approved(self) -> None:
        first, second = Mock(return_value="one"), Mock(return_value="two")
        code1 = self._code(gate.propose("s", "fix A", first))
        code2 = self._code(gate.propose("s", "fix B", second))
        self.assertEqual(gate.conversation_action(f"approve {code2}", "s"), "two")
        self.assertEqual(gate.conversation_action(f"approve {code1}", "s"), "one")

    def test_restaging_same_fix_replaces_its_code(self) -> None:
        old = self._code(gate.propose("s", "fix A", self.run))
        new = self._code(gate.propose("s", "fix A", self.run))
        if old != new:
            self.assertIn("does not match", gate.conversation_action(f"approve {old}", "s"))
        self.assertEqual(gate.conversation_action(f"approve {new}", "s"), "done")

    def test_wake_phrase_before_yes(self) -> None:
        gate.propose("s", "add Captain Phillips", self.run)
        self.assertEqual(gate.conversation_action("Hey, Jarvis. Yes", "s"), "done")


if __name__ == "__main__":
    unittest.main()
