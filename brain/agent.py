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

import contextlib
import contextvars
import os
import json
import re
from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
from threading import RLock
import time
from typing import Any, Iterator

from dotenv import load_dotenv

try:
    import anthropic
except ImportError:  # the agent disables itself in __init__
    anthropic = None  # type: ignore[assignment]

from brain import memory_extractor
from brain import model_select
from brain.habits import render_habits_block
from brain.history import append_turn, load_history, load_session_messages, start_new_session
from brain.intents import routed_offline_reply
from brain.local_model import is_configured as local_model_configured
from brain.memory import render_memory_block
from brain import standing_orders
from brain.personality import (
    build_habits_section,
    build_systems_health_section,
    build_memory_section,
    build_persona_prompt,
    build_standing_orders_section,
)
from brain.tools import execute_tool, get_tool_specs, reject_server_tool
from brain import tool_health
from jarvis_logger import get_logger

# Load .env from the project root, regardless of where the user launched from.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

log = get_logger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _api_key() -> str:
    """Read live so a key added to .env after launch is picked up on re-init."""
    return (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()


def _model() -> str:
    """Read live per request so `update_setting ANTHROPIC_MODEL` takes effect without a restart.

    Empty or ``auto`` follows the newest model in ANTHROPIC_MODEL_FAMILY
    (haiku by default); see brain/model_select.py.
    """
    return model_select.resolve(os.getenv("ANTHROPIC_MODEL", "") or "", _DEFAULT_MODEL)

_MAX_TOKENS = 1024
# Marks the start of a new model round in a streamed reply (ASCII record
# separator). Only emitted when a client opts in via stream_round_breaks
# (the iOS companion), and replaced by a space before history is sent back
# to the model.
ROUND_BREAK = "\x1e"
stream_round_breaks: contextvars.ContextVar[bool] = contextvars.ContextVar("stream_round_breaks", default=False)
# With round breaks on, the stream also carries "\x1dSearching Radarr\x1d"
# (ASCII group separator) while tools run: a status the app shows in place of
# the typing dots and drops once text resumes.
TOOL_STATUS = "\x1d"
_TOOL_LABELS = {
    "find_movie": "Searching Radarr",
    "add_movie": "Adding to Radarr",
    "find_series": "Searching Sonarr",
    "add_series": "Adding to Sonarr",
    "diagnose_media": "Tracing the media pipeline",
    "download_status": "Checking downloads",
    "media_queue": "Checking the media queue",
    "control_device": "Talking to Home Assistant",
    "home_status": "Checking the house",
    "get_weather": "Checking the weather",
    "todays_agenda": "Checking your calendar",
    "arr_stack_status": "Checking the NAS",
}


def tool_status_label(tool_uses: list[Any]) -> str:
    names = [str(getattr(use, "name", "")) for use in tool_uses]
    for name in names:
        if name in _TOOL_LABELS:
            return _TOOL_LABELS[name]
    return "Working on it"

# Newer models can decline (stop_reason "refusal") with no text at all.
_REFUSAL_REPLY = "I'm afraid that one's off the table for me, sir."
_MAX_TOOL_TURNS = 6
# A tool call written out as text ("[calls control_device with ...]"): the
# model imitating a transcript instead of calling the tool, so nothing ran.
_PSEUDO_CALL_RE = re.compile(r"\[\s*call(?:s|ed|ing)?\s+[a-z_]+\b[^\]]*\]", re.IGNORECASE)
_PSEUDO_CALL_NUDGE = ("(JARVIS runtime: your last reply wrote a tool call out as text, so nothing "
                      "ran and Nick heard nothing. Make the real tool call now, then reply.)")
_HISTORY_KEEP_TURNS = 20


def _rejected_server_tool(exc: Exception) -> str | None:
    """The web tool a 400 complains about, if any (account or model without it)."""
    text = str(exc).lower()
    return next((name for name in ("web_fetch", "web_search") if name in text), None)


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


# The persona and tool schemas are identical across turns, but Nick's messages
# are often more than 5 minutes apart, so a 5-minute entry expired between
# nearly every exchange and each first reply re-processed ~12k tokens. The
# 1-hour entry costs 2x once to write and 0.1x per read after that.
_STATIC_CACHE = {"type": "ephemeral", "ttl": "1h"}
_TAIL_CACHE = {"type": "ephemeral"}


def _as_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [dict(block) for block in content]


class _PseudoCallGuard:
    """Holds streamed text inside an open "[" until it closes, so a fake tool
    call ("[calls control_device ...]") never reaches the speaker or screen.
    Once one is seen, the rest of that round (narration of a call that never
    happened) is dropped too."""

    def __init__(self) -> None:
        self.held = ""
        self.tripped = False

    def feed(self, delta: str) -> str:
        if self.tripped:
            return ""
        text = self.held + delta
        start = text.find("[")
        if start < 0:
            self.held = ""
            return text
        close = text.find("]", start)
        if close < 0 and len(text) - start < 200:
            self.held = text[start:]
            return text[:start]
        if _PSEUDO_CALL_RE.match(text, start):
            self.tripped = True
            self.held = ""
            return text[:start]
        # An ordinary bracket: release it, keep scanning after it.
        self.held = ""
        cut = (close if close >= 0 else start) + 1
        return text[:cut] + self.feed(text[cut:])

    def flush(self) -> str:
        held, self.held = self.held, ""
        return "" if self.tripped else held


def _clean_history_text(text: str, role: str | None) -> str:
    """Drop round markers and, from replies, fake tool calls written as text.

    Left in history, a "[calls ...]" reply teaches the model to keep writing
    them instead of calling tools."""
    text = text.replace(ROUND_BREAK, " ")
    if role == "assistant" and _PSEUDO_CALL_RE.search(text):
        text = " ".join(_PSEUDO_CALL_RE.sub(" ", text).split()) or "..."
    return text


def _turn_messages(history: list[dict[str, Any]], turn_context: str) -> list[dict[str, Any]]:
    """History as sent this turn: per-turn context rides on the newest user
    message instead of the system prompt, so the system prompt and earlier
    history stay byte-identical from turn to turn and can be read from cache."""
    messages = [
        {**m, "content": _clean_history_text(m["content"], m.get("role"))}
        if isinstance(m.get("content"), str) else m
        for m in history
    ]
    if turn_context and messages and messages[-1].get("role") == "user":
        messages[-1] = {
            "role": "user",
            "content": _as_blocks(messages[-1]["content"])
            + [{"type": "text", "text": "<turn_context>\n(Supplied by JARVIS's own memory systems, "
                "not typed by Nick.)" + turn_context + "\n</turn_context>"}],
        }
    return messages


def _with_cache_breakpoints(messages: list[dict[str, Any]], anchor: int) -> list[dict[str, Any]]:
    """Mark the conversation for caching without touching stored history.

    Two markers (the persona and tool schemas hold the other two of four):
    the newest block, so later rounds of a tool loop re-read everything
    before it, and the reply just before this turn's user message (``anchor``),
    which next turn's request finds by lookback because that message is sent
    unchanged while the turn context moves on to the new user message."""
    # Every message goes out in block form, so a message is rendered the same
    # whether or not it carries a marker this time.
    out = [{**message, "content": _as_blocks(message["content"])} for message in messages]
    for index in {len(out) - 1, anchor}:
        if 0 <= index < len(out) and out[index]["content"]:
            blocks = out[index]["content"]
            blocks[-1] = {**copy.deepcopy(blocks[-1]), "cache_control": _TAIL_CACHE}
    return out


def _log_round(started: float, first_token_at: float | None, usage: Any, round_no: int) -> None:
    """One line per API round so slow turns can be diagnosed from the journal."""
    total = time.monotonic() - started
    ttft = (first_token_at - started) if first_token_at else total
    log.info(
        "model round %d: first token %.2fs, total %.2fs, input %s, cache read %s, cache write %s, output %s",
        round_no, ttft, total,
        getattr(usage, "input_tokens", "?"), getattr(usage, "cache_read_input_tokens", "?"),
        getattr(usage, "cache_creation_input_tokens", "?"), getattr(usage, "output_tokens", "?"),
    )


def _stream_round_separator(text_parts: list[str], delta: str, at_round_start: bool) -> str:
    """Separate text emitted by distinct API rounds without rewriting deltas."""
    if (
        at_round_start
        and text_parts
        and delta
        and not text_parts[-1][-1].isspace()
        and not delta[0].isspace()
    ):
        return " "
    return ""


def _get_client() -> Any | None:
    if not _api_key():
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
        return Anthropic(api_key=_api_key())
    except Exception as exc:  # noqa: BLE001
        log.warning("Anthropic client init failed: %s - agent disabled.", exc)
        return None


class JarvisAgent:
    """Holds conversation state across turns within one session."""

    # Set by the room voice for one turn: the reply is spoken aloud, so short.
    spoken_turn = False
    # The tools the last finished turn ran, as (name, input); the room voice
    # learns shortcuts from it (brain/voice_shortcuts.py).
    turn_tools: tuple = ()

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
        # Tools executed during the most recent turn, for background learning.
        self._last_turn_tools: list[tuple[str, dict[str, Any]]] = []

    # --- Public ----------------------------------------------------------

    def is_available(self) -> bool:
        return self._client is not None or local_model_configured()

    @property
    def session_id(self) -> str:
        return self._session_id

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
            self.turn_tools = ()
            return self._respond_locked(user_input)

    def respond_streaming(self, user_input: str) -> Iterator[str]:
        """Yield the reply in text fragments as the model produces it.

        Opt-in path used for streaming TTS. Falls back to the offline keyword
        reply when the brain is unavailable. Tool rounds run silently between
        text; only spoken text is yielded. History is persisted at the end,
        exactly like respond().
        """
        with self._lock:
            direct = self._arr_stack_action(user_input)
            if direct is not None:
                yield direct
                return
            if self._client is None:
                reply = routed_offline_reply(user_input, self._history)
                self.last_used_tools = False
                self._history.extend((
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": reply},
                ))
                self._trim_history()
                append_turn(user_input, reply, session_id=self._session_id)
                self._after_turn(user_input, reply)
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
            self._after_turn(user_input, full)

    def start_session(self) -> str:
        """Start a fresh conversation session and clear in-memory context."""
        with self._lock:
            self._session_id = start_new_session()
            self._history.clear()
            self.last_used_tools = False
            log.info("Started new session %s", self._session_id)
            return self._session_id

    def _arr_stack_action(self, user_input: str) -> str | None:
        from brain import confirm_gate, voice_trust
        from brain.arr_stack import conversation_action
        if not voice_trust.trusted():  # only Nick approves fixes; a guest's message still disarms "yes"
            confirm_gate.conversation_action(user_input, self._session_id, handled=True)
            return None
        reply = conversation_action(user_input, self._session_id)
        # Always called so every message disarms a pending bare "yes".
        gate_reply = confirm_gate.conversation_action(
            user_input, self._session_id, handled=reply is not None,
        )
        reply = reply if reply is not None else gate_reply
        if reply is not None:
            self.last_used_tools = True
            self._last_turn_tools = []
            self._history.extend((
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": reply},
            ))
            self._trim_history()
            append_turn(user_input, reply, session_id=self._session_id)
            self._after_turn(user_input, reply)
        return reply

    def _respond_locked(self, user_input: str) -> str:
        direct = self._arr_stack_action(user_input)
        if direct is not None:
            return direct
        if self._client is None:
            self.last_used_tools = False
            reply = routed_offline_reply(user_input, self._history)
            self._history.extend((
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": reply},
            ))
            self._trim_history()
            append_turn(user_input, reply, session_id=self._session_id)
            self._after_turn(user_input, reply)
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
        self._after_turn(user_input, reply)
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
        system, messages, anchor = self._prepare_turn()
        used_tools_this_turn = False
        nudged = False
        tool_context: list[dict[str, Any]] = []
        self._last_turn_tools = []

        for round_no in range(1, _MAX_TOOL_TURNS + 1):
            started = time.monotonic()
            model = _model()
            tools = get_tool_specs(model)
            request = dict(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=tools,
                messages=_with_cache_breakpoints(messages, anchor),
            )
            try:
                response = self._client.messages.create(  # type: ignore[union-attr]
                    **request, **model_select.request_kwargs(model),
                )
            except anthropic.BadRequestError as exc:
                rejected = _rejected_server_tool(exc)
                if rejected:
                    reject_server_tool(rejected)
                    request["tools"] = get_tool_specs(model)
                    response = self._client.messages.create(  # type: ignore[union-attr]
                        **request, **model_select.request_kwargs(model),
                    )
                elif not (model_select.request_kwargs(model) and model_select.is_settings_rejection(exc)):
                    raise
                else:
                    model_select.mark_plain(model)
                    response = self._client.messages.create(**request)  # type: ignore[union-attr]
            _log_round(started, None, getattr(response, "usage", None), round_no)

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]

            if response.stop_reason == "refusal":
                self.last_used_tools = used_tools_this_turn
                return _REFUSAL_REPLY

            if response.stop_reason == "pause_turn":
                # A long server-side search/fetch paused; sending its content
                # back (no new user message) lets the server carry on.
                messages.append({"role": "assistant",
                                 "content": [_serialize_content_block(b) for b in response.content]})
                continue

            if response.stop_reason == "tool_use" and tool_uses:
                used_tools_this_turn = True
                messages.append(
                    {
                        "role": "assistant",
                        "content": [_serialize_content_block(block) for block in response.content],
                    }
                )
                messages.append({"role": "user", "content": self._run_tools(tool_uses, tool_context)})
                continue

            text = "".join(getattr(b, "text", "") for b in text_blocks).strip()
            if _PSEUDO_CALL_RE.search(text) and not nudged and round_no < _MAX_TOOL_TURNS:
                nudged = True
                log.warning("Model wrote a tool call as text; asking for the real call: %s", text[:120])
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": _PSEUDO_CALL_NUDGE})
                continue
            text = " ".join(_PSEUDO_CALL_RE.sub(" ", text).split())
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
        system, messages, anchor = self._prepare_turn()
        used_tools_this_turn = False
        nudged = False
        text_parts: list[str] = []
        tool_context: list[dict[str, Any]] = []
        self._last_turn_tools = []

        for round_no in range(1, _MAX_TOOL_TURNS + 1):
            at_round_start = True
            guard = _PseudoCallGuard()

            def emit(delta: str) -> Iterator[str]:
                nonlocal at_round_start
                # Claude can emit a short sentence before a tool call,
                # then begin the post-tool answer without leading
                # whitespace. Those are separate API rounds, so direct
                # concatenation produced text such as ``Done.Here's``
                # in the companion and persisted history. Keep normal
                # deltas byte-for-byte intact, but separate distinct
                # model rounds when neither side supplied whitespace.
                separator = _stream_round_separator(text_parts, delta, at_round_start)
                if at_round_start and text_parts and stream_round_breaks.get():
                    # The companion shows each round as its own
                    # bubble; the marker is saved too, so history
                    # replays split the same way.
                    separator = ROUND_BREAK
                if separator:
                    text_parts.append(separator)
                    yield separator
                at_round_start = False
                text_parts.append(delta)
                yield delta

            started = time.monotonic()
            first_token_at: float | None = None
            model = _model()
            request = dict(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=get_tool_specs(model),
                messages=_with_cache_breakpoints(messages, anchor),
            )
            with contextlib.ExitStack() as stack:
                try:
                    stream = stack.enter_context(self._client.messages.stream(  # type: ignore[union-attr]
                        **request, **model_select.request_kwargs(model),
                    ))
                except anthropic.BadRequestError as exc:
                    rejected = _rejected_server_tool(exc)
                    if rejected:
                        reject_server_tool(rejected)
                        request["tools"] = get_tool_specs(model)
                        stream = stack.enter_context(self._client.messages.stream(  # type: ignore[union-attr]
                            **request, **model_select.request_kwargs(model),
                        ))
                    elif not (model_select.request_kwargs(model) and model_select.is_settings_rejection(exc)):
                        raise
                    else:
                        model_select.mark_plain(model)
                        stream = stack.enter_context(self._client.messages.stream(**request))  # type: ignore[union-attr]
                for delta in stream.text_stream:
                    if delta:
                        first_token_at = first_token_at or time.monotonic()
                        delta = guard.feed(delta)
                        if delta:
                            yield from emit(delta)
                tail = guard.flush()
                if tail:
                    yield from emit(tail)
                final = stream.get_final_message()
            _log_round(started, first_token_at, getattr(final, "usage", None), round_no)

            tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
            if final.stop_reason == "refusal" and not text_parts:
                self.last_used_tools = used_tools_this_turn
                yield _REFUSAL_REPLY
                return _REFUSAL_REPLY
            if final.stop_reason == "pause_turn":
                messages.append({"role": "assistant",
                                 "content": [_serialize_content_block(b) for b in final.content]})
                continue
            if final.stop_reason == "tool_use" and tool_uses:
                used_tools_this_turn = True
                messages.append(
                    {
                        "role": "assistant",
                        "content": [_serialize_content_block(block) for block in final.content],
                    }
                )
                if stream_round_breaks.get():
                    # Transient status for the companion; never saved or
                    # counted as reply text.
                    yield TOOL_STATUS + tool_status_label(tool_uses) + TOOL_STATUS
                messages.append({"role": "user", "content": self._run_tools(tool_uses, tool_context)})
                continue
            if guard.tripped and not nudged and round_no < _MAX_TOOL_TURNS:
                nudged = True
                said = "".join(getattr(b, "text", "") for b in final.content
                               if getattr(b, "type", None) == "text").strip()
                log.warning("Model wrote a tool call as text; asking for the real call: %s", said[:120])
                messages.append({"role": "assistant", "content": said or "..."})
                messages.append({"role": "user", "content": _PSEUDO_CALL_NUDGE})
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

    def _system_blocks(self) -> list[dict[str, Any]]:
        """The system prompt: the stable persona, plus standing orders.

        Both change only when Nick changes them, so the whole system prompt is
        byte-stable and cacheable. Anything that varies per turn goes through
        ``_turn_context`` onto the newest user message instead.
        """
        blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": build_persona_prompt(),
                "cache_control": _STATIC_CACHE,
            }
        ]
        orders = build_standing_orders_section(standing_orders.render_block())
        if orders.strip():
            blocks.append({"type": "text", "text": orders})
        return blocks

    def _turn_context(self, latest_user_text: str) -> str:
        """Memory recalled for this message, habits, tool health, recent tool results."""
        dynamic = build_memory_section(render_memory_block(latest_user_text))
        dynamic += build_habits_section(render_habits_block())
        dynamic += build_systems_health_section(tool_health.render_block())
        context = self._recent_tool_context.get(self._session_id)
        if context:
            dynamic += (
                "\n\n# Recent trusted tool results for this session\n"
                "Use the exact identifiers below for follow-ups. If Nick says yes after one exact "
                "media match, add it immediately with the default quality; do not reconfirm.\n"
                f"{context}"
            )
        if self.spoken_turn:
            dynamic += (
                "\n\n# Spoken reply\n"
                "This turn came through the room microphone and your reply is played on a speaker. "
                "Answer in one short sentence (two at most), no lists or markdown. If you only "
                "carried out a home action and it worked, reply exactly \"Done.\""
            )
        from brain import voice_trust
        dynamic += voice_trust.context_note()
        return dynamic if dynamic.strip() else ""

    def _prepare_turn(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """System blocks, this turn's messages, and the cache anchor index."""
        latest_user_text = str(self._history[-1].get("content", "")) if self._history else ""
        messages = _turn_messages(self._history, self._turn_context(latest_user_text))
        return self._system_blocks(), messages, len(self._history) - 2

    def _run_tools(self, tool_uses: list[Any], tool_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Execute one round's tool calls, concurrently when there are several.

        Claude only batches calls that don't depend on each other, and most
        tools are network round trips (HA, Radarr, weather), so running them
        side by side cuts a multi-tool round to its slowest call."""
        def run(use: Any) -> tuple[dict[str, Any], str]:
            tool_input = dict(use.input)
            return tool_input, execute_tool(use.name, tool_input, session_id=self._session_id)

        if len(tool_uses) == 1:
            outcomes = [run(tool_uses[0])]
        else:
            # Each worker gets a copy of this turn's context (the speaker, the
            # request id), which threads don't inherit on their own.
            contexts = [contextvars.copy_context() for _ in tool_uses]
            with ThreadPoolExecutor(max_workers=min(4, len(tool_uses))) as pool:
                outcomes = list(pool.map(lambda pair: pair[0].run(run, pair[1]), zip(contexts, tool_uses)))
        results: list[dict[str, Any]] = []
        for use, (tool_input, result_text) in zip(tool_uses, outcomes):
            tool_context.append({"tool": use.name, "input": tool_input, "result": result_text})
            results.append({"type": "tool_result", "tool_use_id": use.id, "content": result_text})
        return results

    def _save_tool_context(self, entries: list[dict[str, Any]]) -> None:
        self._last_turn_tools = [(str(e["tool"]), dict(e["input"])) for e in entries]
        if entries:
            self._recent_tool_context[self._session_id] = json.dumps(entries, ensure_ascii=False)[:6000]

    def _after_turn(self, user_input: str, reply: str) -> None:
        """Hand the finished turn to background learning (habits + memory).

        Runs on a worker thread so a slow extraction never delays the reply;
        the offline paths still record habit signals with no model call.
        """
        self.turn_tools = tuple(getattr(self, "_last_turn_tools", ()))
        from brain import voice_trust
        if not voice_trust.trusted():  # a guest's words never become facts about Nick or JARVIS
            self._last_turn_tools = []
            return
        try:
            memory_extractor.observe_turn(self._client, user_input, reply, self._last_turn_tools)
        except Exception:  # noqa: BLE001
            log.exception("Could not queue turn for background learning")
        finally:
            self._last_turn_tools = []
