# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

This repo maintains its own living docs — read them before making changes:

- `JARVIS.md` — full project context: file map, stack, .env variables, tool list, how a turn works, UI layout, roadmap. The canonical reference.
- `ARCHITECTURE.md` — the target layered architecture (Perception / Memory / Reasoning / Agency / Presence) and the runtime design rule.
- `FUTURE_SESSIONS.md` — Nick's goals for the self-learning/self-healing direction, his ever-growing wishlist (integrations, JARVIS TV launcher checklist, loose ends), and the prioritized backlog for future sessions. `SELF_HEALING_HANDOFF.md` is the review checklist for the most recent self-healing work.
- `OVERNIGHT_HANDOFF.md` — status, evidence and review checklist for the 2026-09-22 overnight overhaul (alerts, confirmation gate, notifications, performance).
- `IOS_REDESIGN_PLAN.md` — the hub-first JarvisCompanion redesign (Phase 4): what was built, what's left, and open questions.

Keep both updated when you change behavior they describe (new tools, new .env vars, new monitors, shipped roadmap items).

`AGENTS.md` is a symlink to this file. Keep these instructions and every other
relevant Markdown document synchronized with repository changes; documentation
updates are part of the change, not a later cleanup task.

## Commit versioning

Every commit must increase both iOS companion version fields in
`ios/JarvisCompanion/JarvisCompanion.xcodeproj/project.pbxproj`: increment the
user-facing `MARKETING_VERSION` and the integer `CURRENT_PROJECT_VERSION` build
number. Keep the Debug and Release values identical.

## What this is

JARVIS is a fully local Python desktop AI assistant (PyQt6 GUI + voice + text REPL) built on the Anthropic API, with a persistent character (calls the user "sir", dry wit, never breaks persona). No web server, no build step, no compiled artifacts — it's a long-running desktop process.

## Commands

```bash
# Install deps (use the repo's venv)
.venv/bin/pip install -r requirements.txt

# Run — GUI is the default daily driver
python -m app.main            # GUI (PyQt6 orb + chat + status panels)
python -m app.main --voice    # headless wake-word voice loop
python -m app.main --text     # keyboard REPL, no mic/GUI required — best for quick manual testing
python -m app.main --server   # authenticated HTTP API for the iOS companion

# One-time: create a Desktop shortcut for the GUI
python create_shortcut.py

# JARVIS TV (Android launcher for the TCL TV; see JARVIS.md "JARVIS TV")
TV=10.0.0.46:5555 tv/launcher/deploy.sh
```

Tests use the standard-library runner: the full suite is `.venv/bin/python -m unittest` from the repo root (not `discover -s tests`, which skips `tests/__init__.py`'s isolation of `memory/tool_health.json`); one module is e.g. `.venv/bin/python -m unittest tests.test_server -v`. There is no linter, formatter, or CI configured. Verify desktop changes by running `python -m app.main --text` and exercising the relevant tool/command manually, or `--voice`/GUI mode when the change touches speech or UI. Type-check iOS sources with the device SDK when a matching Simulator runtime is unavailable.

Config lives in `.env` at the project root (never commit it — see `JARVIS.md` for the full variable list). Settings are read live via `os.getenv()` inside functions, not cached at import time, so `.env` edits (including ones JARVIS makes to itself via `update_setting`) take effect without a restart.

The companion server requires `JARVIS_API_TOKEN`; its bind address and port are
controlled by `JARVIS_SERVER_HOST` and `JARVIS_SERVER_PORT`. Non-companion clients (TV, dashboards) get their own scoped tokens via `python -m app.client_tokens create <name> events:read status:read` (add `chat` only for a client that should talk to JARVIS, like the TV's mic); never hand them `JARVIS_API_TOKEN`.

## Architecture

**Everything routes through `brain.runtime.JarvisRuntime`.** GUI (`ui/`), voice (`speech/voice_loop.py`), and text REPL (`app/main.py`) are three thin front ends over the same runtime instance — they should never call low-level modules (history, memory, tools) directly. When adding a feature, ask whether it belongs on the runtime as a capability or background monitor rather than in a UI widget or the speech loop.

**Conversation loop** (`brain/agent.py`, `JarvisAgent`):
- One `JarvisAgent` per process. `respond()` / `respond_streaming()` run `_run_tool_loop()`: call Claude with the full tool spec (`brain/tools.py:get_tool_specs()`), execute any `tool_use` blocks via `execute_tool()`, feed results back, repeat up to `_MAX_TOOL_TURNS` (6), then return the final text.
- If `ANTHROPIC_API_KEY` is missing or the SDK fails to init, `is_available()` is False and every mode falls back to `brain/intents.py` + `brain/router.py` — a dependency-free keyword matcher (`route_command`) so the app is never fully dark even with the brain offline.
- History persists to SQLite (`brain/history.py`) per session (`memory/history.db`); each launch starts a new session UUID via `start_new_session()`. In-memory history is trimmed to `_HISTORY_KEEP_TURNS` (20 turns) each turn.
- `last_used_tools` is set after every `respond()` call — the voice runner reads it to choose a short vs. long follow-up listening window (task done via tool vs. open-ended conversation).

**Tools** (`brain/tools.py`): single dispatch point (`execute_tool(name, input) -> str`) for 45+ tools spanning Home Assistant, Spotify, timers, reminders/calendar, media library (Radarr/Sonarr), app launching, clipboard, vision, and self-modifying settings (`update_setting` edits `.env` directly). Adding a tool means: define the schema + handler in `tools.py`, and if it needs new state, back it with its own module (pattern: `brain/timers.py`, `brain/reminders.py`) rather than growing `tools.py`'s logic inline.

**Memory has three layers, don't conflate them**: `brain/history.py` (SQLite, short-term conversation transcript, session-scoped) vs. `brain/memory.py` (JSON `memory/profile.json`, durable cross-session facts via `remember_fact`/`forget_fact`, injected each turn by `render_memory_block()` into the `<turn_context>` block on the newest user message, not the system prompt, so history stays cacheable, capped at `MEMORY_MAX_FACTS`) vs. `brain/habits.py` (derived usage patterns, never written by the model). `brain/memory_extractor.py` is the background worker that fills the second layer automatically after every turn. It (which also distils pasted imports into the always-injected core profile, `Jarvis Memory/Core Profile.md`, via `refresh_core_profile`), `brain/study_brief.py` (composes the daily quiz countdown text), `brain/donna.py` (Donna, the private counsellor reached from the companion's hidden Donna mode; her conversation and notes never touch JARVIS's memory layers) and `brain/subagents.py` (background subagent jobs such as `brain/email_triage.py`; new model-driven background work should be a subagent, and gets an entry in `brain/agent_roster.py`'s `_AGENTS` so it shows on the companion's Agents map) are the only places that should call the model outside a conversation turn. Every model call records its usage with `brain/usage.py` (`record_response(agent, response)`), which feeds the Agents page's cost view; a new call site must do the same. Notes Nick dictates go to the Obsidian vault via `capture_note`, not to profile facts. Behavioural directives ("stop doing X") are a separate store, `brain/standing_orders.py`, injected as orders every turn; numeric/on-off knobs go through `brain/settings.py`.

**Background monitors** run on the runtime and feed a proactive event inbox (Home Assistant state polling, weather refresh, calendar alerts) — see `ARCHITECTURE.md`'s "Runtime Monitor Settings" for the relevant `.env` toggles. New sensors/proactive behavior should be added as a monitor here, not polled from the UI. Monitors raise alerts with `runtime.raise_alert` (→ `brain/event_bus.py`), never by calling `brain/notifications.py` directly; the phone (APNs, HA fallback) and `GET /api/clients/events` SSE streams are bus subscribers.

**JARVIS TV** (`tv/`, read `tv/README.md` first; a copy lives on the TV at `/sdcard/JARVIS_TV.md`) is a thin client like the phone: it reads Core's `/api/glance` and `/api/clients/events` with its own scoped token and never holds `JARVIS_API_TOKEN`. When you change TV behaviour, bump `versionCode`/`versionName` in `tv/launcher/AndroidManifest.xml`.

**Personality is centralized**: `brain/personality.py` builds the system prompt (tone, address form, humor level, tool descriptions) from `.env` settings — this is the one place that defines how JARVIS "sounds," so persona changes belong there, not scattered across tool reply strings.
