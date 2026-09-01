"""Safe, reviewable Home Assistant automation drafting and deployment.

Changes are staged locally first. Applying or rolling back always requires an
explicit confirmation flag, checks for concurrent edits, and verifies the
result through Home Assistant before reporting success.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations.home_assistant import (
    delete_automation_config,
    get_automation_config,
    get_states,
    list_automations,
    list_services,
    save_automation_config,
)
from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILE = _PROJECT_ROOT / "memory" / "automation_workshop.json"
_LOCK = threading.RLock()
_MODES = {"single", "restart", "queued", "parallel"}


def _empty_state() -> dict[str, list[dict[str, Any]]]:
    return {"drafts": [], "changes": []}


def _load_state() -> dict[str, list[dict[str, Any]]]:
    with _LOCK:
        if not _STATE_FILE.exists():
            return _empty_state()
        try:
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not load automation workshop state: %s", exc)
            return _empty_state()
        if not isinstance(data, dict):
            return _empty_state()
        return {
            "drafts": data.get("drafts", []) if isinstance(data.get("drafts"), list) else [],
            "changes": data.get("changes", []) if isinstance(data.get("changes"), list) else [],
        }


def _save_state(data: dict[str, list[dict[str, Any]]]) -> None:
    with _LOCK:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = _STATE_FILE.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(_STATE_FILE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _config_hash(config: dict[str, Any] | None) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _pretty(config: dict[str, Any] | None) -> str:
    if config is None:
        return ""
    return json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False)


def _diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    before_lines = _pretty(before).splitlines(keepends=True)
    after_lines = _pretty(after).splitlines(keepends=True)
    rendered = "".join(difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="current",
        tofile="proposed",
    ))
    return rendered[:12000] or "No content changes."


def _resolve_automation(identifier: str) -> tuple[dict[str, Any] | None, str]:
    identifier = (identifier or "").strip()
    if not identifier:
        return None, "Missing automation id or name."
    success, result = list_automations()
    if not success:
        return None, str(result)

    lowered = identifier.lower()
    exact = [
        item for item in result
        if lowered in {
            str(item.get("id", "")).lower(),
            str(item.get("entity_id", "")).lower(),
            str(item.get("alias", "")).lower(),
        }
    ]
    if len(exact) == 1:
        return exact[0], ""
    partial = [item for item in result if lowered in str(item.get("alias", "")).lower()]
    if len(partial) == 1:
        return partial[0], ""
    if len(exact) > 1 or len(partial) > 1:
        matches = exact or partial
        names = ", ".join(str(item.get("alias")) for item in matches[:8])
        return None, f"Multiple automations match: {names}."
    return None, f"No automation matched '{identifier}'."


def inventory_automations() -> str:
    success, result = list_automations()
    if not success:
        return f"Could not list Home Assistant automations: {result}"
    if not result:
        return "No Home Assistant automations found."
    return "\n".join(
        f"{item['alias']} | id={item['id'] or '(read-only)'} | "
        f"entity={item['entity_id']} | state={item['state']}"
        for item in result
    )


def inspect_automation(identifier: str) -> str:
    item, error = _resolve_automation(identifier)
    if item is None:
        return error
    automation_id = str(item.get("id", ""))
    if not automation_id:
        return "That automation has no editor ID and is read-only through this workshop."
    success, config = get_automation_config(automation_id)
    if not success:
        return f"Could not read automation {automation_id}: {config}"
    return _pretty(config)


def _collect_references(value: Any) -> tuple[set[str], set[str]]:
    entities: set[str] = set()
    services: set[str] = set()

    def walk(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, str(child_key))
            return
        if isinstance(node, list):
            for child in node:
                walk(child, key)
            return
        if not isinstance(node, str):
            return
        if key == "entity_id":
            entities.add(node)
        elif key in {"action", "service"} and "." in node and "{{" not in node:
            services.add(node)

    walk(value)
    return entities, services


def _validate_config(config: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(config, dict):
        return ["Automation configuration must be an object."], warnings
    try:
        json.dumps(config)
    except (TypeError, ValueError):
        errors.append("Automation configuration must be JSON-serializable.")
        return errors, warnings

    if not str(config.get("alias", "")).strip():
        errors.append("Automation alias is required.")
    triggers = config.get("triggers", config.get("trigger"))
    actions = config.get("actions", config.get("action"))
    if not isinstance(triggers, (list, dict)) or not triggers:
        errors.append("At least one trigger is required.")
    if not isinstance(actions, (list, dict)) or not actions:
        errors.append("At least one action is required.")
    mode = str(config.get("mode", "single"))
    if mode not in _MODES:
        errors.append(f"Unsupported automation mode '{mode}'.")

    entities, services = _collect_references(config)
    if entities:
        success, states = get_states()
        if success:
            known = {str(item.get("entity_id", "")) for item in states}
            missing = sorted(entity for entity in entities if entity not in known)
            if missing:
                errors.append("Unknown entities: " + ", ".join(missing))
        else:
            warnings.append(f"Entity validation unavailable: {states}")
    if services:
        success, advertised = list_services()
        if success:
            missing = sorted(service for service in services if service not in advertised)
            if missing:
                errors.append("Unknown services: " + ", ".join(missing))
        else:
            warnings.append(f"Service validation unavailable: {advertised}")
    return errors, warnings


def draft_automation(
    operation: str,
    *,
    identifier: str | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Stage a create, update, or delete and return its reviewable diff."""
    operation = (operation or "").strip().lower()
    if operation not in {"create", "update", "delete"}:
        return "Operation must be create, update, or delete."

    old_config: dict[str, Any] | None = None
    automation_id = ""
    if operation in {"update", "delete"}:
        item, error = _resolve_automation(identifier or "")
        if item is None:
            return error
        automation_id = str(item.get("id", ""))
        if not automation_id:
            return "That automation has no editor ID and cannot be changed here."
        success, current = get_automation_config(automation_id)
        if not success or not isinstance(current, dict):
            return f"Could not read the current automation: {current}"
        old_config = current
    else:
        automation_id = str(int(time.time() * 1000))

    new_config = None if operation == "delete" else config
    if operation == "create" and isinstance(new_config, dict) and new_config.get("id"):
        return "New automation drafts must not reuse an existing id."
    if operation != "delete":
        errors, warnings = _validate_config(new_config)
        if errors:
            return "Draft rejected: " + " ".join(errors)
    else:
        warnings = []

    draft = {
        "id": uuid.uuid4().hex[:8],
        "operation": operation,
        "automation_id": automation_id,
        "identifier": identifier or "",
        "before": old_config,
        "after": new_config,
        "baseline_hash": _config_hash(old_config),
        "warnings": warnings,
        "status": "pending",
        "created_at": _now(),
    }
    state = _load_state()
    state["drafts"].append(draft)
    state["drafts"] = state["drafts"][-100:]
    _save_state(state)

    warning_text = f" Warnings: {' '.join(warnings)}" if warnings else ""
    return (
        f"Automation draft {draft['id']} is ready for review. Operation: {operation}."
        f"{warning_text}\n{_diff(old_config, new_config)}\n"
        "Do not apply it until Nick explicitly approves this exact draft."
    )


def _find_record(records: list[dict[str, Any]], record_id: str) -> dict[str, Any] | None:
    return next((record for record in records if record.get("id") == record_id), None)


def _restore(operation: str, automation_id: str, before: dict[str, Any] | None) -> tuple[bool, Any]:
    if operation == "create":
        return delete_automation_config(automation_id)
    if before is None:
        return False, "No backup configuration is available."
    return save_automation_config(automation_id, before)


def _contains_config(actual: Any, expected: Any) -> bool:
    """Return whether HA's normalized response still contains the proposal."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_config(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _contains_config(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected


def _restore_and_verify(
    operation: str,
    automation_id: str,
    before: dict[str, Any] | None,
) -> tuple[bool, Any]:
    success, result = _restore(operation, automation_id, before)
    if not success:
        return False, result
    verify_success, verify_config = get_automation_config(automation_id)
    if operation == "create":
        return (not verify_success and "404" in str(verify_config)), verify_config
    return (
        verify_success
        and isinstance(verify_config, dict)
        and _contains_config(verify_config, before),
        verify_config,
    )


def apply_automation_draft(draft_id: str, *, confirmed: bool = False) -> str:
    if not confirmed:
        return "Confirmation required. Ask Nick to approve the exact automation draft first."
    state = _load_state()
    draft = _find_record(state["drafts"], (draft_id or "").strip())
    if draft is None:
        return f"No automation draft found with id {draft_id}."
    if draft.get("status") != "pending":
        return f"Draft {draft_id} is already {draft.get('status')}."

    operation = str(draft["operation"])
    automation_id = str(draft["automation_id"])
    before = draft.get("before")
    after = draft.get("after")

    if operation in {"update", "delete"}:
        success, current = get_automation_config(automation_id)
        if not success or not isinstance(current, dict):
            return f"Apply stopped because the current automation could not be read: {current}"
        if _config_hash(current) != draft.get("baseline_hash"):
            return "Apply stopped: the automation changed after this draft was created. Draft it again."

    if operation == "delete":
        success, result = delete_automation_config(automation_id)
    else:
        success, result = save_automation_config(automation_id, after)

    verified = False
    if success and operation == "delete":
        verify_success, verify_result = get_automation_config(automation_id)
        verified = not verify_success and "404" in str(verify_result)
    elif success:
        verify_success, verify_config = get_automation_config(automation_id)
        verified = (
            verify_success
            and isinstance(verify_config, dict)
            and _contains_config(verify_config, after)
        )

    if not success or not verified:
        rollback_ok, rollback_result = _restore_and_verify(operation, automation_id, before)
        draft["status"] = "failed_rolled_back" if rollback_ok else "failed_rollback_failed"
        _save_state(state)
        return (
            f"Automation apply failed verification: {result}. "
            f"Rollback {'succeeded' if rollback_ok else f'failed: {rollback_result}'}."
        )

    change = {
        "id": uuid.uuid4().hex[:8],
        "draft_id": draft["id"],
        "operation": operation,
        "automation_id": automation_id,
        "before": before,
        "after": after,
        "status": "applied",
        "applied_at": _now(),
    }
    draft["status"] = "applied"
    state["changes"].append(change)
    state["changes"] = state["changes"][-100:]
    _save_state(state)
    return (
        f"Automation {operation} applied and verified. Change id {change['id']} "
        "is available for rollback."
    )


def rollback_automation_change(change_id: str, *, confirmed: bool = False) -> str:
    if not confirmed:
        return "Confirmation required before rolling back an automation change."
    state = _load_state()
    change = _find_record(state["changes"], (change_id or "").strip())
    if change is None:
        return f"No automation change found with id {change_id}."
    if change.get("status") != "applied":
        return f"Change {change_id} is already {change.get('status')}."

    success, result = _restore_and_verify(
        str(change["operation"]),
        str(change["automation_id"]),
        change.get("before"),
    )
    if not success:
        return f"Rollback failed: {result}"
    change["status"] = "rolled_back"
    change["rolled_back_at"] = _now()
    _save_state(state)
    return f"Automation change {change_id} was rolled back."
