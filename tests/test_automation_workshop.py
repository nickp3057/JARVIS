"""Tests for staged, confirm-gated Home Assistant automation changes."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from brain import automation_workshop as workshop


def _config(alias: str = "Office motion") -> dict:
    return {
        "alias": alias,
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.office_motion", "to": "on"},
        ],
        "conditions": [],
        "actions": [
            {"action": "light.turn_on", "target": {"entity_id": "light.office"}},
        ],
        "mode": "single",
    }


class AutomationWorkshopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_patch = patch.object(
            workshop, "_STATE_FILE", Path(self.temp.name) / "automation_workshop.json",
        )
        self.state_patch.start()

    def tearDown(self) -> None:
        self.state_patch.stop()
        self.temp.cleanup()

    def _validation_patches(self):
        return (
            patch.object(workshop, "get_states", return_value=(True, [
                {"entity_id": "binary_sensor.office_motion"},
                {"entity_id": "light.office"},
            ])),
            patch.object(workshop, "list_services", return_value=(True, ["light.turn_on"])),
        )

    def _draft_id(self, result: str) -> str:
        match = re.search(r"draft ([0-9a-f]{8})", result)
        self.assertIsNotNone(match, result)
        return match.group(1)  # type: ignore[union-attr]

    def test_create_draft_validates_references_and_persists_diff(self) -> None:
        states_patch, services_patch = self._validation_patches()
        with states_patch, services_patch:
            result = workshop.draft_automation("create", config=_config())
        draft_id = self._draft_id(result)
        self.assertIn('"alias": "Office motion"', result)
        stored = json.loads(workshop._STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(stored["drafts"][0]["id"], draft_id)
        self.assertEqual(stored["drafts"][0]["status"], "pending")

    def test_draft_rejects_unknown_entities(self) -> None:
        with patch.object(workshop, "get_states", return_value=(True, [])), patch.object(
            workshop, "list_services", return_value=(True, ["light.turn_on"]),
        ):
            result = workshop.draft_automation("create", config=_config())
        self.assertIn("Unknown entities", result)
        self.assertFalse(workshop._STATE_FILE.exists())

    def test_apply_requires_confirmation(self) -> None:
        self.assertIn(
            "Confirmation required",
            workshop.apply_automation_draft("anything", confirmed=False),
        )

    def test_create_apply_verifies_and_can_be_rolled_back(self) -> None:
        states_patch, services_patch = self._validation_patches()
        with states_patch, services_patch:
            draft_id = self._draft_id(workshop.draft_automation("create", config=_config()))

        with patch.object(
            workshop, "save_automation_config", return_value=(True, {"result": "ok"}),
        ) as save, patch.object(
            workshop, "get_automation_config", return_value=(True, _config()),
        ):
            result = workshop.apply_automation_draft(draft_id, confirmed=True)
        self.assertIn("applied and verified", result)
        self.assertEqual(save.call_count, 1)

        change_id = re.search(r"Change id ([0-9a-f]{8})", result).group(1)  # type: ignore[union-attr]
        with patch.object(
            workshop, "delete_automation_config", return_value=(True, {"result": "ok"}),
        ) as delete, patch.object(
            workshop, "get_automation_config", return_value=(False, "Home Assistant error 404"),
        ):
            rollback = workshop.rollback_automation_change(change_id, confirmed=True)
        self.assertIn("rolled back", rollback)
        delete.assert_called_once()

    def test_update_stops_when_live_config_changed_after_draft(self) -> None:
        old = _config("Old")
        proposed = _config("Proposed")
        changed_elsewhere = _config("Changed elsewhere")
        item = {"id": "123", "entity_id": "automation.office", "alias": "Office", "state": "on"}
        states_patch, services_patch = self._validation_patches()
        with patch.object(workshop, "list_automations", return_value=(True, [item])), patch.object(
            workshop, "get_automation_config", side_effect=[(True, old), (True, changed_elsewhere)],
        ), states_patch, services_patch:
            draft_id = self._draft_id(workshop.draft_automation(
                "update", identifier="Office", config=proposed,
            ))
            result = workshop.apply_automation_draft(draft_id, confirmed=True)
        self.assertIn("changed after this draft", result)

    def test_failed_update_verification_restores_backup(self) -> None:
        old = _config("Old")
        proposed = _config("Proposed")
        item = {"id": "123", "entity_id": "automation.office", "alias": "Office", "state": "on"}
        states_patch, services_patch = self._validation_patches()
        with patch.object(workshop, "list_automations", return_value=(True, [item])), patch.object(
            workshop,
            "get_automation_config",
            side_effect=[
                (True, old),
                (True, old),
                (False, "verification failed"),
                (True, old),
            ],
        ), patch.object(
            workshop,
            "save_automation_config",
            side_effect=[(True, {"result": "ok"}), (True, {"result": "ok"})],
        ) as save, states_patch, services_patch:
            draft_id = self._draft_id(workshop.draft_automation(
                "update", identifier="Office", config=proposed,
            ))
            result = workshop.apply_automation_draft(draft_id, confirmed=True)
        self.assertIn("Rollback succeeded", result)
        self.assertEqual(save.call_args_list[-1].args, ("123", old))

    def test_invalid_operations_and_required_shape_are_rejected(self) -> None:
        self.assertIn("must be create", workshop.draft_automation("rename"))
        self.assertIn("must be an object", workshop.draft_automation("create", config=None))
        for key, expected in (
            ("alias", "alias is required"),
            ("triggers", "trigger is required"),
            ("actions", "action is required"),
        ):
            broken = _config()
            broken[key] = "" if key == "alias" else []
            with self.subTest(key=key):
                self.assertIn(expected, workshop.draft_automation("create", config=broken))
        broken_mode = _config()
        broken_mode["mode"] = "chaos"
        self.assertIn("Unsupported automation mode", workshop.draft_automation(
            "create", config=broken_mode,
        ))

    def test_new_draft_rejects_reused_id_and_unknown_service(self) -> None:
        reused = _config()
        reused["id"] = "existing"
        self.assertIn("must not reuse", workshop.draft_automation("create", config=reused))
        states_patch, _ = self._validation_patches()
        with states_patch, patch.object(
            workshop, "list_services", return_value=(True, []),
        ):
            self.assertIn("Unknown services", workshop.draft_automation(
                "create", config=_config(),
            ))

    def test_validation_outage_becomes_warning_not_silent_skip(self) -> None:
        with patch.object(workshop, "get_states", return_value=(False, "states offline")), patch.object(
            workshop, "list_services", return_value=(False, "services offline"),
        ):
            result = workshop.draft_automation("create", config=_config())
        self._draft_id(result)
        self.assertIn("Entity validation unavailable", result)
        self.assertIn("Service validation unavailable", result)

    def test_ambiguous_missing_and_read_only_automation_are_safe(self) -> None:
        matches = [
            {"id": "1", "entity_id": "automation.office_one", "alias": "Office One"},
            {"id": "2", "entity_id": "automation.office_two", "alias": "Office Two"},
        ]
        with patch.object(workshop, "list_automations", return_value=(True, matches)):
            self.assertIn("Multiple automations", workshop.inspect_automation("Office"))
            self.assertIn("No automation matched", workshop.inspect_automation("Kitchen"))
        read_only = [{"id": "", "entity_id": "automation.yaml", "alias": "YAML only"}]
        with patch.object(workshop, "list_automations", return_value=(True, read_only)):
            self.assertIn("read-only", workshop.inspect_automation("YAML only"))
            self.assertIn("cannot be changed", workshop.draft_automation(
                "delete", identifier="YAML only",
            ))

    def test_inventory_and_inspection_handle_empty_and_failed_ha(self) -> None:
        with patch.object(workshop, "list_automations", return_value=(True, [])):
            self.assertIn("No Home Assistant automations", workshop.inventory_automations())
        with patch.object(workshop, "list_automations", return_value=(False, "offline")):
            self.assertIn("offline", workshop.inventory_automations())
        item = {"id": "123", "entity_id": "automation.office", "alias": "Office", "state": "on"}
        with patch.object(workshop, "list_automations", return_value=(True, [item])), patch.object(
            workshop, "get_automation_config", return_value=(False, "denied"),
        ):
            self.assertIn("denied", workshop.inspect_automation("Office"))

    def test_corrupt_state_file_degrades_to_empty_state(self) -> None:
        workshop._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        workshop._STATE_FILE.write_text("{broken", encoding="utf-8")
        self.assertEqual(workshop._load_state(), {"drafts": [], "changes": []})

    def test_missing_and_reused_drafts_are_rejected(self) -> None:
        self.assertIn("No automation draft", workshop.apply_automation_draft(
            "missing", confirmed=True,
        ))
        states_patch, services_patch = self._validation_patches()
        with states_patch, services_patch:
            draft_id = self._draft_id(workshop.draft_automation("create", config=_config()))
        with patch.object(workshop, "save_automation_config", return_value=(True, {})), patch.object(
            workshop, "get_automation_config", return_value=(True, _config()),
        ):
            workshop.apply_automation_draft(draft_id, confirmed=True)
        self.assertIn("already applied", workshop.apply_automation_draft(
            draft_id, confirmed=True,
        ))

    def test_delete_requires_real_404_and_restores_on_ambiguous_failure(self) -> None:
        old = _config("Delete me")
        item = {"id": "123", "entity_id": "automation.delete_me", "alias": "Delete me", "state": "on"}
        with patch.object(workshop, "list_automations", return_value=(True, [item])), patch.object(
            workshop,
            "get_automation_config",
            side_effect=[(True, old), (True, old), (False, "network timeout"), (True, old)],
        ), patch.object(
            workshop, "delete_automation_config", return_value=(True, {}),
        ), patch.object(
            workshop, "save_automation_config", return_value=(True, {}),
        ) as restore:
            draft_id = self._draft_id(workshop.draft_automation(
                "delete", identifier="Delete me",
            ))
            result = workshop.apply_automation_draft(draft_id, confirmed=True)
        self.assertIn("Rollback succeeded", result)
        restore.assert_called_once_with("123", old)

    def test_rollback_confirmation_missing_change_and_repeat_are_safe(self) -> None:
        self.assertIn("Confirmation required", workshop.rollback_automation_change(
            "missing", confirmed=False,
        ))
        self.assertIn("No automation change", workshop.rollback_automation_change(
            "missing", confirmed=True,
        ))
        state = workshop._empty_state()
        state["changes"].append({
            "id": "done1234",
            "status": "rolled_back",
            "operation": "create",
            "automation_id": "123",
            "before": None,
        })
        workshop._save_state(state)
        self.assertIn("already rolled_back", workshop.rollback_automation_change(
            "done1234", confirmed=True,
        ))

    def test_contains_config_allows_extra_ha_fields_but_not_changed_values(self) -> None:
        expected = {"alias": "Office", "actions": [{"action": "light.turn_on"}]}
        actual = {
            "id": "123",
            "alias": "Office",
            "actions": [{"action": "light.turn_on", "metadata": {}}],
        }
        self.assertTrue(workshop._contains_config(actual, expected))
        actual["alias"] = "Wrong"
        self.assertFalse(workshop._contains_config(actual, expected))


if __name__ == "__main__":
    unittest.main()
