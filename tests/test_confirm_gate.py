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


if __name__ == "__main__":
    unittest.main()
