# Pearson Stark JARVIS Architecture

This project is moving toward an ambient assistant: one runtime, many senses,
many tools, one coherent personality.

## Runtime Spine

`brain.runtime.JarvisRuntime` is the app-level coordination layer. GUI, text
mode, and voice mode should talk to the runtime first, not directly to low-level
modules.

Current responsibilities:

- Own the active `JarvisAgent`.
- Route conversation through the best available brain.
- Start and switch sessions.
- Provide status snapshots for the UI.
- Expose timers and due reminders for proactive announcement.
- Maintain a runtime event inbox for observations and proactive notices.
- Maintain a reconnecting Home Assistant WebSocket subscription for live state
  changes, with REST polling available as a fallback transport.
- Monitor qBittorrent for completions and new errors while exposing live
  qBittorrent and Radarr/Sonarr queue status on demand.
- Keep structured home and weather snapshots for cockpit panels.
- Track whether the last turn used tools.
- Compose situational briefings (time, weather, running timers) via
  `compose_briefing` / `launch_briefing`.
- Stream replies fragment-by-fragment (`respond_streaming`) for streaming TTS.
- Pipeline streamed replies into sentence-level TTS in text, GUI chat, GUI
  voice, and standalone headless voice when `STREAMING_TTS=true`.
- Discover and safely call the connected Home Assistant instance's live service
  catalogue, with confirmation gates and blocked administrative domains.
- Stage Home Assistant automation changes locally, validate and diff them,
  require exact-draft approval, detect concurrent edits, preserve backups, and
  verify both application and rollback.
- Mirror structured profile facts to managed Obsidian Markdown and locally
  retrieve size-bounded excerpts only from explicitly authorized folders, with
  note-path provenance and JSON remaining authoritative if storage is offline.
- Atomically switch and answer named sessions for remote clients through
  `respond_in_session` / `respond_streaming_in_session`.

Near-term responsibilities:

- Run proactive monitors for calendar, Home Assistant, weather, files, and
  system state.
- Add more monitor workers that feed the event inbox: calendar, files, and
  system health.
- Store task state for multi-step work.
- Decide when to interrupt, notify quietly, or stay silent.
- Grow the Obsidian baseline into indexed episodic and semantic memory,
  contradiction handling, and user-facing inspect/edit controls, with the
  Optiplex as the live store and the NAS as synchronized storage.
- Route small offline tasks to an optional local model, and add a governed
  patch/test/approve/rollback workflow for self-modification.

## Target Layers

1. Perception
   - Voice, chat, Home Assistant state, timers, calendar, weather, computer
     state, files, and future sensors.

2. Memory
   - Durable facts, preferences, people, projects, routines, and active goals.
   - Short-term conversation context stays separate from long-term memory.

3. Reasoning
   - Conversational response, tool planning, verification, and follow-up.
   - The agent should report outcomes, not narrate internals.

4. Agency
   - Tools that can change the world: home, music, media library
     (Radarr/Sonarr), reminders, files, browser, research, coding, and
     automations.
   - Home Assistant owns device definitions and automation logic. JARVIS may
     discover and activate existing scenes, scripts, and automations through
     the runtime rather than duplicating their definitions.
   - World-changing tools that are hard to undo (e.g. adding to the media
     library) are split into a read-only lookup tool and a confirm-gated
     write tool; the write never fires without explicit user confirmation.
   - Home Assistant automation changes follow a stricter draft/diff/approve/
     apply/verify workflow with optimistic concurrency and rollback records.

5. Presence
   - GUI cockpit, orb state, voice pacing, interruption, proactive notices, and
     a stable personality.
   - The iOS companion retains an offline device cache while the server/runtime
     owns synchronized sessions, conversation context, titles, and deletion.

## Companion API

`app.server` is the authenticated HTTP edge over the runtime. It provides
health, session CRUD/history, blocking chat, and streamed chat endpoints. Every
chat request carries a server session ID; the API selects that session and runs
the turn under the runtime lock so contexts cannot bleed into each other.

The bearer token comes from `JARVIS_API_TOKEN`. The API is a transport adapter,
not a second brain: it must route conversation through `JarvisRuntime` and use
`brain.history` for persisted session metadata.

## Design Rule

New features should plug into the runtime as capabilities or monitors. Avoid
making UI widgets or speech loops own business logic directly. The more the
runtime knows, the more JARVIS feels like one mind instead of several scripts.

## Runtime Monitor Settings

- `HOME_MONITOR_ENABLED`: true/false, defaults to true.
- `HOME_MONITOR_INTERVAL_S`: polling interval in seconds, defaults to 20.
- `HOME_MONITOR_TRANSPORT`: `websocket` (default) or `polling`/`rest` fallback.
- `HOME_MONITOR_RECONNECT_S`: WebSocket reconnect delay, defaults to 5 seconds.
- `HOME_MONITOR_DOMAINS`: comma-separated domains, defaults to
  `light,switch,fan,media_player,binary_sensor,person,device_tracker,lock,cover,climate`.
- `WEATHER_MONITOR_ENABLED`: true/false, defaults to true.
- `WEATHER_MONITOR_INTERVAL_S`: weather refresh interval in seconds, defaults
  to 1800.
- `QBITTORRENT_MONITOR_ENABLED`: true/false, defaults to true when qBittorrent
  credentials are configured.
- `QBITTORRENT_MONITOR_INTERVAL_S`: download polling interval, defaults to 30.
- `QBITTORRENT_ANNOUNCE_COMPLETIONS`: speak completed downloads, defaults true.

## Memory Retrieval Settings

- `MEMORY_MAX_FACTS`: legacy cap for the full profile rendering, defaults to 60.
- `MEMORY_RELEVANT_FACTS`: maximum relevant/recent profile facts injected into
  a normal turn, defaults to 12.
- `OBSIDIAN_VAULT_PATH`: Obsidian vault root; defaults to `memory/obsidian`.
- `OBSIDIAN_INDEX_FOLDERS`: comma-separated, vault-relative folder allowlist;
  defaults to the managed `Jarvis Memory` folder.
- `OBSIDIAN_MAX_RESULTS` / `OBSIDIAN_MAX_CHARS`: per-turn retrieval caps,
  defaulting to four excerpts and 4,000 characters.
- `OBSIDIAN_MAX_FILE_BYTES`: per-note indexing limit, defaults to 1 MB.
