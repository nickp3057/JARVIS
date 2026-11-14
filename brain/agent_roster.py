"""Who works for JARVIS: the agent roster behind the companion's Agents map.

JARVIS (the conversation agent, ``brain/agent.py``) is the head. Everything
else that thinks with the model outside a conversation turn reports to him,
in two kinds (``kind``):

- ``subagent``: a real agent (``brain/subagents.py``): its own instructions
  and tools, several steps on its own (email sorter, investing agents);
- ``helper``: a single model call with no tools (memory keeper, study coach).

``roster()`` describes each one: what it does, when it runs, whether it's on,
and what it last did (from the subagent job history), for ``GET /api/agents``.

The map is fluid: a subagent job or spec name that matches nothing in
``_AGENTS`` still appears (as a plain "subagent" named after its job), so a new
agent shows up the first time it runs. Give it a proper ``_AGENTS`` entry for a
name, icon and description; ``tests/test_agent_roster.py`` fails until every
subagent name in the code is covered.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable


def _on(name: str, default: str = "true") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def _email_state() -> tuple[bool, str]:
    from integrations import gmail

    if not _on("EMAIL_TRIAGE_ENABLED"):
        return False, "Switched off (EMAIL_TRIAGE_ENABLED)."
    if not gmail.is_linked():
        return False, "Waiting for Gmail to be linked."
    return True, ""


def _investing_state() -> tuple[bool, str]:
    from brain import investing
    from integrations import schwab

    if not investing.enabled():
        return False, "Switched off (INVESTING_ENABLED)."
    if not schwab.configured():
        return False, "Schwab isn't connected."
    from brain import trading
    if trading.enabled():
        return True, "Proposes trades; real orders only after Nick taps Approve."
    return True, "Suggestions only: real orders are switched off (TRADING_ENABLED)."


def _investing_needs() -> int:
    from brain import investing

    return len(investing.pending_proposals())


# id, kind, name, role, SF Symbol, schedule, job/spec-name prefixes ("a|b"), state() -> (on, note), needs_you() -> count
_AGENTS: list[dict[str, Any]] = [
    {"id": "memory", "kind": "helper", "name": "Memory keeper", "symbol": "brain.head.profile",
     "role": "After every conversation, saves anything worth remembering about you.",
     "schedule": "After every conversation", "jobs": "",
     "state": lambda: (_on("MEMORY_AUTO_EXTRACT"), "" if _on("MEMORY_AUTO_EXTRACT") else "Switched off.")},
    {"id": "lessons", "kind": "helper", "name": "Reflector", "symbol": "arrow.triangle.2.circlepath",
     "role": "When a turn goes wrong (you correct or repeat yourself, a tool fails quietly), writes JARVIS a lesson.",
     "schedule": "After a turn that went badly", "jobs": "",
     "state": lambda: (_on("LESSONS_ENABLED"), "" if _on("LESSONS_ENABLED") else "Switched off.")},
    {"id": "study", "kind": "helper", "name": "Study coach", "symbol": "graduationcap",
     "role": "Writes the daily countdown to your quizzes and exams.",
     "schedule": "Daily", "jobs": "",
     "state": lambda: (_on("STUDY_BRIEF_ENABLED"), "" if _on("STUDY_BRIEF_ENABLED") else "Switched off.")},
    {"id": "donna", "kind": "subagent", "name": "Donna", "symbol": "heart.text.square",
     "role": "Your private counsellor (hidden Donna mode). Only her cost shows here, never what you talk about.",
     "schedule": "When you open Donna mode", "jobs": "", "state": lambda: (True, "")},
    {"id": "email", "kind": "subagent", "name": "Email sorter", "symbol": "envelope.badge",
     "role": "Trashes junk, files missionary letters, keeps anything important.",
     "schedule": "Every 15 minutes", "jobs": "email_triage", "state": _email_state},
    {"id": "advisor", "kind": "subagent", "name": "Portfolio advisor", "symbol": "chart.pie",
     "role": "Keeps your main Schwab account on its target weights and proposes trades for your OK.",
     "schedule": "Each market day, 10:30 AM ET", "jobs": "investing_review|investing_suggest",
     "state": _investing_state, "needs": _investing_needs},
    {"id": "crash", "kind": "subagent", "name": "Crash watch", "symbol": "exclamationmark.triangle",
     "role": "Checks any holding that drops 8% in a day: market dip or real bad news?",
     "schedule": "Market hours, every 5 minutes", "jobs": "investing_drop_|investing_emergency", "state": _investing_state},
    {"id": "playground", "kind": "subagent", "name": "Playground trader", "symbol": "dice",
     "role": "Trades its own small account however it likes; scored against plain VOO.",
     "schedule": "Mondays, 10:30 AM ET", "jobs": "investing_playground", "state": _investing_state},
]


def _matches(job_name: str, pattern: str) -> bool:
    return bool(pattern) and any(job_name.startswith(p) for p in pattern.split("|"))


def _discovered(jobs: list[dict[str, Any]], specs: dict[str, float]) -> list[dict[str, Any]]:
    """Subagents that ran but have no ``_AGENTS`` entry yet: one per job/spec name."""
    known = "|".join(a["jobs"] for a in _AGENTS if a["jobs"])
    names: dict[str, str] = {}
    for job in jobs:
        if not _matches(job["name"], known):
            names.setdefault(job["name"], job["title"])
    for name in specs:
        if not _matches(name, known):
            names.setdefault(name, name.replace("_", " ").capitalize())
    return [{"id": f"new:{name}", "kind": "subagent", "name": title[:28], "symbol": "sparkles",
             "role": "A new background agent JARVIS has started using (not described yet).",
             "schedule": "As needed", "jobs": name, "state": lambda: (True, "")}
            for name, title in sorted(names.items())]


def usage_report(days: float | None) -> dict[str, Any]:
    """Estimated spend per agent over the last ``days`` (None = since tracking
    began), for the Agents page. Ledger names (``brain/usage.py``) map to an
    agent by id, then by its job/spec-name prefixes; JARVIS is "jarvis"."""
    from brain import usage

    raw = usage.summary(days)
    specs = [{"id": "jarvis", "name": "JARVIS", "symbol": "sparkle", "jobs": ""}] + _AGENTS
    rows: dict[str, dict[str, Any]] = {
        s["id"]: {"id": s["id"], "name": s["name"], "symbol": s["symbol"], "usd": 0.0, "calls": 0,
                  "input_tokens": 0, "output_tokens": 0, "models": []} for s in specs}
    for ledger_name, stats in raw["agents"].items():
        spec = next((s for s in specs if s["id"] == ledger_name), None) \
            or next((s for s in specs if _matches(ledger_name, s["jobs"])), None)
        key = spec["id"] if spec else "other"
        row = rows.setdefault(key, {"id": "other", "name": "Other", "symbol": "sparkles", "usd": 0.0, "calls": 0,
                                    "input_tokens": 0, "output_tokens": 0, "models": []})
        row["usd"] = round(row["usd"] + stats["usd"], 4)
        row["calls"] += stats["calls"]
        row["input_tokens"] += stats["input_tokens"]
        row["output_tokens"] += stats["output_tokens"]
        row["models"] = sorted(set(row["models"]) | set(stats["models"]))
    agents = sorted(rows.values(), key=lambda r: (-r["usd"], r["name"]))
    return {"days": days, "since": raw["since"], "tracking_since": raw["tracking_since"],
            "total_usd": round(sum(r["usd"] for r in agents), 4), "agents": agents, "daily": raw["daily"]}


def roster() -> dict[str, Any]:
    from brain import model_select, subagents

    jobs = subagents.jobs_snapshot()
    agents = []
    for spec in _AGENTS + _discovered(jobs, subagents.specs_seen()):
        try:
            on, note = spec["state"]()
        except Exception as exc:  # noqa: BLE001 - one broken check never hides the map
            on, note = False, f"Couldn't check: {exc}"[:120]
        mine = [j for j in jobs if _matches(j["name"], spec["jobs"])]
        running = next((j for j in mine if j["state"] == "running"), None)
        finished = [j for j in mine if j["state"] != "running"]
        needs: Callable[[], int] | None = spec.get("needs")
        try:
            waiting = needs() if needs and on else 0
        except Exception:  # noqa: BLE001
            waiting = 0
        agents.append({
            "id": spec["id"], "kind": spec["kind"], "name": spec["name"], "role": spec["role"],
            "symbol": spec["symbol"],
            "schedule": spec["schedule"], "boss": "jarvis",
            "state": "running" if running else ("idle" if on else "off"),
            "note": note, "needs_you": waiting,
            "task": running["title"] if running else None,
            "recent": [{"title": j["title"], "state": j["state"], "at": j["finished_at"],
                        "summary": j["summary"][:500]} for j in finished[:5]],
        })
    head_model = os.getenv("ANTHROPIC_MODEL", "") or "auto"
    try:
        head_model = model_select.resolve(head_model, head_model)
    except Exception:  # noqa: BLE001
        pass
    return {"head": {"id": "jarvis", "name": "JARVIS", "role": "The head: talks to you and hands work to the others.",
                     "model": head_model},
            "agents": agents, "at": time.time()}
