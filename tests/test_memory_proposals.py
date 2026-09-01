"""Governed staging and approval tests for extracted memories."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import memory_proposals as proposals
from brain.tools import execute_tool, get_tool_specs


class MemoryProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_file = proposals._PROPOSAL_FILE
        proposals._PROPOSAL_FILE = Path(self.tempdir.name) / "proposals.json"

    def tearDown(self) -> None:
        proposals._PROPOSAL_FILE = self.old_file
        self.tempdir.cleanup()

    def _proposal_id(self) -> str:
        result = proposals.propose_memory_fact(
            "preference", "Nick prefers warm lighting.",
            key="preferences.lighting", confidence=0.8, evidence="I like warm lights",
        )
        return result.split()[2].rstrip(":")

    def test_proposal_is_staged_without_writing_memory(self) -> None:
        with patch("brain.memory_proposals.remember_fact") as remember:
            proposal_id = self._proposal_id()
        remember.assert_not_called()
        data = json.loads(proposals._PROPOSAL_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["proposals"][0]["id"], proposal_id)
        self.assertEqual(data["proposals"][0]["status"], "pending")
        self.assertIn(proposal_id, proposals.describe_memory_proposals())

    def test_apply_requires_confirmation_and_exact_pending_id(self) -> None:
        proposal_id = self._proposal_id()
        with patch("brain.memory_proposals.remember_fact", return_value="Remembered (preference): fact") as remember:
            self.assertIn("Confirmation required", proposals.apply_memory_proposal(proposal_id))
            remember.assert_not_called()
            self.assertIn("No memory proposal", proposals.apply_memory_proposal("missing", confirmed=True))
            self.assertIn("Applied", proposals.apply_memory_proposal(proposal_id, confirmed=True))
            self.assertIn("already applied", proposals.apply_memory_proposal(proposal_id, confirmed=True))
        remember.assert_called_once_with(
            "preference", "Nick prefers warm lighting.",
            key="preferences.lighting", confidence=0.8,
            source=f"approved_proposal:{proposal_id}",
        )

    def test_reject_never_writes_and_cannot_be_applied(self) -> None:
        proposal_id = self._proposal_id()
        self.assertIn("Rejected", proposals.reject_memory_proposal(proposal_id))
        with patch("brain.memory_proposals.remember_fact") as remember:
            self.assertIn("already rejected", proposals.apply_memory_proposal(proposal_id, confirmed=True))
        remember.assert_not_called()

    def test_validation_deduplication_and_bounding(self) -> None:
        self.assertIn("category", proposals.propose_memory_fact("secret", "value"))
        self.assertIn("empty", proposals.propose_memory_fact("general", ""))
        first = proposals.propose_memory_fact("goal", "Ship JARVIS")
        second = proposals.propose_memory_fact("goal", "Ship JARVIS")
        self.assertIn("Memory proposal", first)
        self.assertIn("Already proposed", second)
        with patch.object(proposals, "_MAX_PROPOSALS", 2):
            proposals.propose_memory_fact("goal", "Second")
            proposals.propose_memory_fact("goal", "Third")
        data = json.loads(proposals._PROPOSAL_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(data["proposals"]), 2)

    def test_tools_are_registered_and_preserve_confirmation_gate(self) -> None:
        names = {item.get("name") for item in get_tool_specs()}
        self.assertTrue({
            "propose_memory_fact", "list_memory_proposals",
            "apply_memory_proposal", "reject_memory_proposal",
        }.issubset(names))
        proposal_id = self._proposal_id()
        with patch("brain.memory_proposals.remember_fact") as remember:
            result = execute_tool("apply_memory_proposal", {
                "proposal_id": proposal_id, "confirmed": False,
            })
        self.assertIn("Confirmation required", result)
        remember.assert_not_called()


if __name__ == "__main__":
    unittest.main()
