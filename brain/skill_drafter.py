"""Skill drafter: a subagent that turns one skill request into a draft skill.

Started from the app (Skill requests → Draft a skill, ``POST
/api/skills/requests/{id}/draft``). It sees the wish and the tools skills may
use (``brain.skills.ALLOWED_TOOLS``, with their real schemas) and ends one of
two ways:

* ``draft_skill`` — a recipe of those tools, stored as a *draft* in
  ``brain/skills.py``; nothing runs until Nick approves it;
* ``needs_more`` — no recipe can do it; a short "what it'd take" note (the
  device, integration or API missing) goes on the request.

Model: ``SKILL_DRAFTER_MODEL`` (default ``auto:sonnet``). Usage is recorded
by ``brain/subagents.run`` under ``skill_drafter``.
"""
from __future__ import annotations

import json
from typing import Any

from brain import skill_requests, skills, subagents
from jarvis_logger import get_logger

log = get_logger(__name__)

NAME = "skill_drafter"


def _catalog() -> str:
    from brain import tools
    lines = []
    for spec in tools._CUSTOM_TOOLS:
        if spec["name"] in skills.ALLOWED_TOOLS:
            lines.append(f"- {spec['name']}: {' '.join(str(spec.get('description', '')).split())[:300]}\n"
                         f"  input_schema: {json.dumps(spec.get('input_schema', {}), ensure_ascii=False)[:600]}")
    return "\n".join(lines)


def _spec(request: dict[str, Any], outcome: dict[str, Any]) -> subagents.SubagentSpec:
    def draft(args: dict[str, Any]) -> str:
        try:
            record = skills.propose(str(args.get("name") or ""), str(args.get("description") or ""),
                                    args.get("params"), args.get("steps") or [], request["id"], proposed_by="drafter")
        except ValueError as exc:
            return f"Rejected: {exc}. Fix it and call draft_skill again, or call needs_more."
        outcome["skill"] = record
        return f"Draft {record['id']} saved for Nick's approval. Finish with one line saying what it does."

    def needs_more(args: dict[str, Any]) -> str:
        note = " ".join(str(args.get("note") or "").split())[:400]
        if not note:
            return "Say what's missing in note."
        outcome["note"] = note
        return "Noted on the request. Finish with one line."

    system = (
        "You draft new skills for JARVIS, Nick's home assistant. A skill is a short recipe (1-"
        f"{skills.MAX_STEPS} steps) of existing tool calls with optional {{param}} placeholders in string "
        "inputs. Only these tools may be used, with inputs matching their schemas exactly:\n\n"
        f"{_catalog()}\n\n"
        "If the wish can be met by such a recipe, call draft_skill once (snake_case name, one-line "
        "description, params each with a description, steps as {tool, input}). Use real entity ids or "
        "scene names only when the wish names them; otherwise make them parameters. If it can't be done "
        "with these tools (it needs a device, integration, API or new code JARVIS doesn't have), call "
        "needs_more with one or two sentences on exactly what it'd take. Never both. Don't invent tools."
    )
    return subagents.SubagentSpec(
        name=NAME, system=system, model_setting="SKILL_DRAFTER_MODEL", default_model="auto:sonnet",
        fallback_model="claude-sonnet-5-5", max_rounds=4, max_tokens=2048,
        tools=[
            subagents.SubagentTool("draft_skill", "Save a draft skill (a recipe of allowed tools) for Nick to approve.", {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "params": {"type": "array", "items": {"type": "object", "properties": {
                        "name": {"type": "string"}, "description": {"type": "string"}}, "required": ["name"]}},
                    "steps": {"type": "array", "items": {"type": "object", "properties": {
                        "tool": {"type": "string"}, "input": {"type": "object"}}, "required": ["tool"]}},
                },
                "required": ["name", "description", "steps"],
            }, draft),
            subagents.SubagentTool("needs_more", "No recipe can do it: say what it'd take.", {
                "type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"],
            }, needs_more),
        ],
    )


def draft(request_id: str, *, client: Any = None) -> str:
    """Run the drafter for one request (synchronously). Returns a one-line outcome."""
    request = next((r for r in skill_requests.listing("all") if r["id"] == request_id), None)
    if request is None:
        return "No such skill request."
    outcome: dict[str, Any] = {}
    asks = "; ".join(request.get("asks") or [])
    task = (f"Wish: {request['summary']}\nWhat Nick said: {asks or '(not recorded)'}\n"
            f"Why JARVIS couldn't: {request.get('why') or '(not recorded)'}\nAsked {request.get('count', 1)} time(s).")
    result = subagents.run(_spec(request, outcome), task, client=client)
    if "skill" in outcome:
        skill_requests.annotate(request_id, draft_skill_id=outcome["skill"]["id"], note="", drafting=False)
        return f"Drafted the {outcome['skill']['name']} skill for “{request['summary']}”. Approve it on the Control tab."
    if "note" in outcome:
        skill_requests.annotate(request_id, note=outcome["note"], drafting=False)
        return f"“{request['summary']}” needs more than a recipe: {outcome['note']}"
    reason = result.error or "the drafter didn't decide"
    skill_requests.annotate(request_id, note=f"Drafting didn't finish ({reason[:120]}). Try again later.", drafting=False)
    return f"Couldn't draft “{request['summary']}”: {reason}"


def start(request_id: str) -> bool:
    """Background job (one at a time); the outcome lands in the inbox."""
    return subagents.start(NAME, "Drafting a skill", lambda: draft(request_id), announce=False)
