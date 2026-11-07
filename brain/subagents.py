"""Subagents: focused, background tool loops that JARVIS hands work to.

The conversation agent (``brain/agent.py``) is built for quick turns: every
tool, Nick's whole context, six rounds. A subagent is the same loop shape,
started fresh for one job:

- its own short system prompt and only the tools that job needs (the email
  sorter can trash mail; it cannot touch the lights or ``.env``);
- its own model, usually the cheap fast one (``auto:haiku``), read live from
  an env setting so it can be changed by voice;
- its own scratch context, so 200 emails never land in Nick's chat history;
  the conversation only ever sees the one-line summary.

``run`` executes a spec synchronously and returns a ``SubagentResult``.
``start`` / ``run_job`` wrap it as a *job*: one at a time per subagent name,
shown in the runtime's task lane, finished with an inbox event (and, when a
person asked for it, a spoken announcement). The runtime registers itself
with ``attach`` at startup; without a runtime (tests, scripts) jobs still run.

Subagent tool handlers enforce their own rules. The model chooses, the
handler decides whether the choice is allowed, so a confused model or a
prompt-injected email can only pick among safe, reversible actions.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from brain import model_select
from jarvis_logger import get_logger

log = get_logger(__name__)


@dataclass
class SubagentTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class SubagentSpec:
    name: str
    system: str
    tools: list[SubagentTool]
    model_setting: str = ""  # env var holding a model id or auto:<family>
    default_model: str = "auto:haiku"
    fallback_model: str = "claude-haiku-5-5"
    max_rounds: int = 12
    max_tokens: int = 2048

    def model(self) -> str:
        raw = os.getenv(self.model_setting, "") if self.model_setting else ""
        return model_select.resolve(raw.strip() or self.default_model, self.fallback_model)


@dataclass
class SubagentResult:
    ok: bool
    text: str
    rounds: int
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    error: str = ""


def _client() -> Any:
    key = (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()
    if not key:
        return None
    try:
        from anthropic import Anthropic  # type: ignore

        return Anthropic(api_key=key)
    except Exception as exc:  # noqa: BLE001
        log.warning("Subagent model client unavailable: %s", exc)
        return None


def _block_dict(block: Any) -> dict[str, Any]:
    """An SDK content block as a plain dict, to replay in the next request."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    kind = getattr(block, "type", "")
    if kind == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {"type": "text", "text": getattr(block, "text", "")}


def _get(block: Any, key: str, default: Any = None) -> Any:
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def run(spec: SubagentSpec, task: str, *, client: Any = None) -> SubagentResult:
    """Run one subagent to completion: model -> tools -> model ... -> text."""
    client = client if client is not None else _client()
    if client is None:
        return SubagentResult(False, "", 0, error="No Anthropic API key, so no subagents.")
    handlers = {tool.name: tool for tool in spec.tools}
    tool_specs = [tool.spec() for tool in spec.tools]
    model = spec.model()
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    calls: list[tuple[str, dict[str, Any]]] = []
    for round_no in range(1, spec.max_rounds + 1):
        request = {"model": model, "max_tokens": spec.max_tokens, "system": spec.system,
                   "messages": messages, "tools": tool_specs}
        try:
            response = client.messages.create(**request, **model_select.request_kwargs(model))
        except Exception as exc:  # noqa: BLE001
            if model_select.is_settings_rejection(exc):
                model_select.mark_plain(model)
                try:
                    response = client.messages.create(**request)
                except Exception as retry_exc:  # noqa: BLE001
                    return SubagentResult(False, "", round_no, calls, f"{type(retry_exc).__name__}: {retry_exc}")
            else:
                log.warning("Subagent %s model call failed: %s", spec.name, exc)
                return SubagentResult(False, "", round_no, calls, f"{type(exc).__name__}: {exc}")
        blocks = list(_get(response, "content", []) or [])
        messages.append({"role": "assistant", "content": [_block_dict(b) for b in blocks]})
        uses = [b for b in blocks if _get(b, "type") == "tool_use"]
        if not uses:
            text = "".join(_get(b, "text", "") or "" for b in blocks if _get(b, "type") == "text").strip()
            return SubagentResult(True, text, round_no, calls)
        results: list[dict[str, Any]] = []
        for use in uses:
            name = _get(use, "name", "")
            args = dict(_get(use, "input", {}) or {})
            calls.append((name, args))
            tool = handlers.get(name)
            if tool is None:
                output, is_error = f"No such tool '{name}'.", True
            else:
                try:
                    output, is_error = str(tool.handler(args)), False
                except Exception as exc:  # noqa: BLE001
                    log.warning("Subagent %s tool %s raised: %s", spec.name, name, exc)
                    output, is_error = f"Tool error: {exc}", True
            item: dict[str, Any] = {"type": "tool_result", "tool_use_id": _get(use, "id", ""), "content": output}
            if is_error:
                item["is_error"] = True
            results.append(item)
        messages.append({"role": "user", "content": results})
    log.warning("Subagent %s hit its %d-round cap", spec.name, spec.max_rounds)
    return SubagentResult(False, "", spec.max_rounds, calls, f"Stopped after {spec.max_rounds} rounds.")


# --- Jobs: background runs the runtime can see ----------------------------------

@dataclass
class Job:
    name: str
    title: str
    state: str  # running | done | failed
    started_at: float
    finished_at: float = 0.0
    summary: str = ""


_runtime: Any = None
_jobs: dict[str, Job] = {}
_history: list[Job] = []
_lock = threading.Lock()


def attach(runtime: Any) -> None:
    """Called once by ``JarvisRuntime`` so jobs show up in its task lane."""
    global _runtime
    _runtime = runtime


def is_running(name: str) -> bool:
    with _lock:
        job = _jobs.get(name)
        return job is not None and job.state == "running"


def run_job(name: str, title: str, work: Callable[[], str], *, announce: bool = False) -> Job | None:
    """Run ``work`` (which returns a summary line) as a visible job, blocking.

    None when a job with this name is already running (one at a time, so a
    scheduled pass and a voice request never sort the same inbox twice)."""
    with _lock:
        current = _jobs.get(name)
        if current is not None and current.state == "running":
            return None
        job = Job(name=name, title=title, state="running", started_at=time.time())
        _jobs[name] = job
    runtime = _runtime
    task_id = None
    if runtime is not None:
        try:
            task_id = runtime.start_task(title)
        except Exception:  # noqa: BLE001 - the cockpit is a nicety
            task_id = None
    try:
        summary = work() or "Done."
        state = "done"
    except Exception as exc:  # noqa: BLE001
        log.exception("Subagent job %s failed", name)
        summary, state = f"Failed: {exc}", "failed"
    with _lock:
        job.state, job.summary, job.finished_at = state, summary, time.time()
        _history.insert(0, job)
        del _history[20:]
    if runtime is not None:
        try:
            if task_id is not None:
                runtime.finish_task(task_id, state="complete" if state == "done" else "failed", detail=summary[:300])
            if announce:
                runtime.announce(summary, source=f"subagent:{name}", title=title)
        except Exception:  # noqa: BLE001
            log.debug("Could not report job %s to the runtime", name, exc_info=True)
    return job


def start(name: str, title: str, work: Callable[[], str], *, announce: bool = True) -> bool:
    """``run_job`` on a background thread. False if that job is already running."""
    if is_running(name):
        return False
    threading.Thread(target=run_job, args=(name, title, work), kwargs={"announce": announce},
                     name=f"jarvis-subagent-{name}", daemon=True).start()
    return True


def describe_jobs() -> str:
    with _lock:
        running = [j for j in _jobs.values() if j.state == "running"]
        recent = list(_history[:5])
    if not running and not recent:
        return "No background agents have run since I started, sir."
    lines: list[str] = []
    for job in running:
        lines.append(f"Running: {job.title} (for {int(time.time() - job.started_at)}s)")
    for job in recent:
        ago = int((time.time() - job.finished_at) / 60)
        lines.append(f"{job.state.title()} {ago} min ago: {job.title} - {job.summary}")
    return "\n".join(lines)


def reset_for_tests() -> None:
    global _runtime
    with _lock:
        _jobs.clear()
        _history.clear()
    _runtime = None
