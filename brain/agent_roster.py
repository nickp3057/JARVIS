"""Who works for JARVIS: the agent roster behind the companion's Agents map.

JARVIS (the conversation agent, ``brain/agent.py``) is the head. Everything
else that thinks with the model outside a conversation turn reports to him:
the memory keeper, the study coach, and the subagents (``brain/subagents.py``)
such as the email sorter and the investing agents. ``roster()`` describes
each one: what it does, when it runs, whether it's on, and what it last did
(from the subagent job history), for ``GET /api/agents``.

When you add a subagent, add it to ``_AGENTS`` so it appears on the map.
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
    return True, "Suggestions only: no real orders yet."


def _investing_needs() -> int:
    from brain import investing

    return len(investing.pending_proposals())


# id, name, role, SF Symbol, schedule, job-name prefix, state() -> (on, note), needs_you() -> count
_AGENTS: list[dict[str, Any]] = [
    {"id": "memory", "name": "Memory keeper", "symbol": "brain.head.profile",
     "role": "After every conversation, saves anything worth remembering about you.",
     "schedule": "After every conversation", "jobs": "",
     "state": lambda: (_on("MEMORY_AUTO_EXTRACT"), "" if _on("MEMORY_AUTO_EXTRACT") else "Switched off.")},
    {"id": "study", "name": "Study coach", "symbol": "graduationcap",
     "role": "Writes the daily countdown to your quizzes and exams.",
     "schedule": "Daily", "jobs": "",
     "state": lambda: (_on("STUDY_BRIEF_ENABLED"), "" if _on("STUDY_BRIEF_ENABLED") else "Switched off.")},
    {"id": "email", "name": "Email sorter", "symbol": "envelope.badge",
     "role": "Trashes junk, files missionary letters, keeps anything important.",
     "schedule": "Every 15 minutes", "jobs": "email_triage", "state": _email_state},
    {"id": "advisor", "name": "Portfolio advisor", "symbol": "chart.pie",
     "role": "Keeps your main Schwab account on its target weights and proposes trades for your OK.",
     "schedule": "Each market day, 10:30 AM ET", "jobs": "investing_review|investing_suggest",
     "state": _investing_state, "needs": _investing_needs},
    {"id": "crash", "name": "Crash watch", "symbol": "exclamationmark.triangle",
     "role": "Checks any holding that drops 8% in a day: market dip or real bad news?",
     "schedule": "Market hours, every 5 minutes", "jobs": "investing_drop_", "state": _investing_state},
    {"id": "playground", "name": "Playground trader", "symbol": "dice",
     "role": "Trades its own small account however it likes; scored against plain VOO.",
     "schedule": "Mondays, 10:30 AM ET", "jobs": "investing_playground", "state": _investing_state},
]


def _matches(job_name: str, pattern: str) -> bool:
    return bool(pattern) and any(job_name.startswith(p) for p in pattern.split("|"))


def roster() -> dict[str, Any]:
    from brain import model_select, subagents

    jobs = subagents.jobs_snapshot()
    agents = []
    for spec in _AGENTS:
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
            "id": spec["id"], "name": spec["name"], "role": spec["role"], "symbol": spec["symbol"],
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
