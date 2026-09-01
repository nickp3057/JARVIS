"""The Claude-powered agent that drives JARVIS conversations.

A single :class:`JarvisAgent` instance lives for the duration of the REPL.
It maintains conversation history, builds the system prompt with current
memory each turn, and runs the tool-calling loop until Claude returns a
plain text reply.

The ``last_used_tools`` flag is set after every ``respond()`` call so the
voice runner can decide whether to keep the follow-up window open
(conversational reply) or close it immediately (task-completed via tool).

Failure modes:
- If Anthropic isn't configured or the SDK is missing, ``is_available``
  returns False and main.py falls back to the keyword router.
- If a request raises mid-conversation, the error is logged and a friendly
  message is returned so the REPL keeps running.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from dotenv import load_dotenv

from brain.history import append_turn, load_history, load_session_messages, start_new_session
from brain.intents import routed_offline_reply
from brain.local_model import is_configured as local_model_configured
from brain.memory import render_memory_block
from brain.personality import build_system_prompt
from brain.tools import execute_tool, get_tool_specs
from jarvis_logger import get_logger

# Load .env from the project root, regardless of where the user launched from.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

log = get_logger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

_MAX_TOKENS = 1024
_MAX_TOOL_TURNS = 6
_HISTORY_KEEP_TURNS = 20


def _serialize_content_block(block: Any) -> dict[str, Any]:
    """Convert an SDK response block back into API-safe message content.

    Anthropic SDK 0.120 adds ``parsed_output`` to parsed text response models.
    Pydantic's ``model_dump`` includes that response-only field, but the
    Messages API rejects it when the assistant content is sent back after a
    tool call. Strip it recursively while preserving future legitimate fields.
    """
    if isinstance(block, dict):
        dumped: Any = dict(block)
    else:
        dumped = block.model_dump(exclude_none=True)

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if key != "parsed_output"
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    result = clean(dumped)
    if not isinstance(result, dict):
        raise TypeError("Anthropic content block did not serialize to an object")
    return result


def _get_client() -> Any | None:
    if not ANTHROPIC_API_KEY:
        log.warning(
            "ANTHROPIC_API_KEY not set in environment (.env) - agent disabled."
        )
        return None
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        log.warning(
            "anthropic SDK not installed (run: pip install -r requirements.txt) - agent disabled."
        )
        return None
    try:
        return Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("Anthropic client init failed: %s - agent disabled.", exc)
        return None


class JarvisAgent:
    """Holds conversation state across turns within one session."""

    def __init__(self) -> None:
        self._client = _get_client()
        self._lock = RLock()
        # Create a new session for this launch and seed in-memory history
        # from that session (empty on first run, populated on resume).
        self._session_id: str = start_new_session()
        self._history: list[dict[str, Any]] = load_history(
            _HISTORY_KEEP_TURNS, session_id=self._session_id
        )
        if self._history:
            log.info(
                "Loaded %d turns of conversation history from disk.",
                len(self._history) // 2,
            )
        # Set after each respond() call: True if any custom tool was invoked
        # this turn. Voice runner reads this to decide follow-up window length.
        self.last_used_tools: bool = False
        self._recent_tool_context: dict[str, str] = {}

    # --- Public ----------------------------------------------------------

    def is_available(self) -> bool:
        return self._client is not None or local_model_configured()

    @property
    def brain_label(self) -> str:
        if self._client is not None:
            return "Claude (online)"
        if local_model_configured():
            return "Local model (fallback)"
        return "Keyword router (fallback)"

    def respond(self, user_input: str) -> str:
        """Run one turn of conversation. Returns Jarvis's spoken reply."""
        with self._lock:
            return self._respond_locked(user_input)

    def respond_streaming(self, user_input: str) -> Iterator[str]:
        """Yield the reply in text fragments as the model produces it.

        Opt-in path used for streaming TTS. Falls back to the offline keyword
        reply when the brain is unavailable. Tool rounds run silently between
        text; only spoken text is yielded. History is persisted at the end,
        exactly like respond().
        """
        with self._lock:
            if self._client is None:
                reply = routed_offline_reply(user_input, self._history)
                self.last_used_tools = False
                self._history.extend((
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": reply},
                ))
                self._trim_history()
                append_turn(user_input, reply, session_id=self._session_id)
                yield reply
                return

            self._history.append({"role": "user", "content": user_input})
            try:
                full = yield from self._run_tool_loop_streaming()
            except Exception:  # noqa: BLE001
                log.exception("Agent error during respond_streaming()")
                self.last_used_tools = False
                if self._history and self._history[-1].get("role") == "user":
                    self._history.pop()
                yield "I ran into a problem reaching my reasoning, sir. Try that again."
                return

            self._history.append({"role": "assistant", "content": full})
            self._trim_history()
            append_turn(user_input, full, session_id=self._session_id)

    def start_session(self) -> str:
        """Start a fresh conversation session and clear in-memory context."""
        with self._lock:
            self._session_id = start_new_session()
            self._history.clear()
            self.last_used_tools = False
            log.info("Started new session %s", self._session_id)
            return self._session_id

    def _respond_locked(self, user_input: str) -> str:
        if self._client is None:
            self.last_used_tools = False
            reply = routed_offline_reply(user_input, self._history)
            self._history.extend((
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": reply},
            ))
            self._trim_history()
            append_turn(user_input, reply, session_id=self._session_id)
            return reply

        self._history.append({"role": "user", "content": user_input})

        try:
            reply = self._run_tool_loop()
        except Exception:  # noqa: BLE001
            log.exception("Agent error during respond()")
            self.last_used_tools = False
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            return "I ran into a problem reaching my reasoning, sir. Try that again."

        self._history.append({"role": "assistant", "content": reply})
        self._trim_history()
        # Persist this turn so history survives restarts.
        append_turn(user_input, reply, session_id=self._session_id)
        return reply

    def switch_session(self, session_id: str) -> None:
        """Load an existing session into the active context."""
        with self._lock:
            self._session_id = session_id
            self._history = load_session_messages(session_id)
            self._trim_history()
            log.info("Switched to session %s (%d msgs)", session_id, len(self._history))

    def reset(self) -> None:
        self.start_session()

    # --- Internals -------------------------------------------------------

    def _run_tool_loop(self) -> str:
        """Call Claude, execute any tools it wants, repeat until done."""
        latest_user_text = str(self._history[-1].get("content", "")) if self._history else ""
        system = self._system_prompt(latest_user_text)
        messages: list[dict[str, Any]] = list(self._history)
        used_tools_this_turn = False
        tool_context: list[dict[str, Any]] = []

        for _ in range(_MAX_TOOL_TURNS):
            response = self._client.messages.create(  # type: ignore[union-attr]
                model=ANTHROPIC_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=get_tool_specs(),
                messages=messages,
            )

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]

            if response.stop_reason == "tool_use" and tool_uses:
                used_tools_this_turn = True
                messages.append(
                    {
                        "role": "assistant",
                        "content": [_serialize_content_block(block) for block in response.content],
                    }
                )

                tool_results: list[dict[str, Any]] = []
                for use in tool_uses:
                    tool_input = dict(use.input)
                    result_text = execute_tool(use.name, tool_input, session_id=self._session_id)
                    tool_context.append({"tool": use.name, "input": tool_input, "result": result_text})
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": use.id,
                            "content": result_text,
                        }
                    )
                messages.append({"role": "user", "content": tool_results})
                continue

            text = "".join(getattr(b, "text", "") for b in text_blocks).strip()
            self.last_used_tools = used_tools_this_turn
            self._save_tool_context(tool_context)
            return text or "..."

        log.warning("Tool loop exceeded %d turns; bailing out.", _MAX_TOOL_TURNS)
        self.last_used_tools = used_tools_this_turn
        return "I got tangled up working on that, sir. Try rephrasing?"

    def _run_tool_loop_streaming(self) -> Iterator[str]:
        """Streaming twin of _run_tool_loop. Yields text deltas, runs tools.

        Returns (via ``return value`` in the generator) the full concatenated
        text so the caller can persist it to history.
        """
        latest_user_text = str(self._history[-1].get("content", "")) if self._history else ""
        system = self._system_prompt(latest_user_text)
        messages: list[dict[str, Any]] = list(self._history)
        used_tools_this_turn = False
        text_parts: list[str] = []
        tool_context: list[dict[str, Any]] = []

        for _ in range(_MAX_TOOL_TURNS):
            with self._client.messages.stream(  # type: ignore[union-attr]
                model=ANTHROPIC_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=get_tool_specs(),
                messages=messages,
            ) as stream:
                for delta in stream.text_stream:
                    if delta:
                        text_parts.append(delta)
                        yield delta
                final = stream.get_final_message()

            tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
            if final.stop_reason == "tool_use" and tool_uses:
                used_tools_this_turn = True
                messages.append(
                    {
                        "role": "assistant",
                        "content": [_serialize_content_block(block) for block in final.content],
                    }
                )
                tool_results: list[dict[str, Any]] = []
                for use in tool_uses:
                    tool_input = dict(use.input)
                    result_text = execute_tool(use.name, tool_input, session_id=self._session_id)
                    tool_context.append({"tool": use.name, "input": tool_input, "result": result_text})
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": use.id,
                            "content": result_text,
                        }
                    )
                messages.append({"role": "user", "content": tool_results})
                continue

            self.last_used_tools = used_tools_this_turn
            self._save_tool_context(tool_context)
            return "".join(text_parts).strip() or "..."

        self.last_used_tools = used_tools_this_turn
        return "".join(text_parts).strip() or "I got tangled up working on that, sir. Try rephrasing?"

    def _trim_history(self) -> None:
        if len(self._history) <= _HISTORY_KEEP_TURNS * 2:
            return
        self._history = self._history[-_HISTORY_KEEP_TURNS * 2 :]

    def _system_prompt(self, latest_user_text: str) -> str:
        prompt = build_system_prompt(render_memory_block(latest_user_text))
        context = self._recent_tool_context.get(self._session_id)
        if context:
            prompt += (
                "\n\n# Recent trusted tool results for this session\n"
                "Use the exact identifiers below for follow-ups. If Nick says yes after one exact "
                "media match, add it immediately with the default quality; do not reconfirm.\n"
                f"{context}"
            )
        return prompt

    def _save_tool_context(self, entries: list[dict[str, Any]]) -> None:
        if entries:
            self._recent_tool_context[self._session_id] = json.dumps(entries, ensure_ascii=False)[:6000]
