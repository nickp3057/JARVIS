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
- Retain a bounded, session-isolated summary of recent tool results so short
  follow-ups such as “yes” retain exact identifiers without leaking between
  companion conversations.
- Sanitize Anthropic response blocks at the agent boundary before replaying
  them into a multi-round tool conversation; SDK-only parsed fields must never
  leak into Messages API input payloads.
- Keep the stable request prefix cacheable: tool schemas carry a
  `cache_control` breakpoint and the system prompt is sent as a stable persona
  block (cached) plus a separate per-turn block for memory facts and recent
  tool context (uncached). Anything that varies per turn must go in the
  dynamic block or in messages, never spliced into the persona text.
- Compose situational briefings (time, weather, agenda, reminders, and timers)
  via `compose_briefing` / `launch_briefing`. The launch method is a
  once-per-runtime claim shared by text, headless voice, and GUI front ends;
  the GUI performs potentially network-backed composition off its UI thread.
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
- Expose stable fact IDs for inspection and targeted correction. Contradicted
  facts can be updated in place with a bounded revision trail rather than
  accumulating incompatible duplicates. Optional semantic keys make this
  automatic for evolving attributes, while confidence and source labels retain
  basic provenance; legacy facts remain migration-free.
- Stage inferred memory as bounded persistent proposals, requiring exact-ID
  approval before durable writes. Typed profile records use stable keys;
  unkeyed contradiction detection is advisory; confidence changes are explicit
  and timestamped. Optional local abstractive summaries always retain the
  extractive digest as fallback.
- Expose bounded Radarr/Sonarr history, missing, and upcoming views. Mutating
  qBittorrent operations require an exact live hash plus explicit confirmation;
  deleting downloaded files is separately explicit.
- Accept explicit, authenticated companion debug reports as a separate durable
  diagnostic store. Reports retain bounded transcript/app context and a focused
  response, but are never promoted automatically into personality memory or
  treated as executable instructions.
- Learn continuously without being asked: every finished turn is queued to a
  background memory worker that records a habit signal, runs one cheap
  structured extraction against existing memories, writes confident facts
  (revising superseded ones), stages low-confidence inferences as proposals,
  and journals notable outcomes into the vault's daily note. The runtime
  subscribes to memory changes and surfaces them as ``memory`` inbox events.
  A derived habits digest (peak times, most-used capabilities, frequent
  targets) is injected into the dynamic prompt block once enough turns exist.
- Atomically switch and answer named sessions for remote clients through
  `respond_in_session` / `respond_streaming_in_session`.
- Maintain a bounded extractive digest alongside each SQLite session and expose
  explicit topic-ranked retrieval. Digests are never injected across sessions
  automatically, preserving companion isolation; deletion and history clearing
  remove them transactionally with their owning session.
- Route unknown conversation to an optional OpenAI-compatible local model when
  Claude is unavailable. Deterministic offline intents take precedence, the
  local adapter has no tools, history is bounded, and every transport/schema
  failure falls through to the keyword router. Backend labels remain visible to
  every front end through the runtime. Optional semantic classification accepts
  only exact structured output from a read-only intent allowlist, then delegates
  execution to deterministic handlers. Read-only Home Assistant state questions
  are filtered by domain, state, and friendly-name tokens and return bounded
  natural-language results; common forms also route without either model. A
  failure cooldown prevents cascading classifier/conversation timeouts while the
  endpoint is unavailable.
- Permit one tightly bounded state-changing local route: high-confidence on/off
  for lights, switches/plugs, and fans. The generated target must overlap the
  original request and resolve uniquely through the existing Home Assistant
  matcher. Sensitive physical domains, broad services, scenes, scripts,
  automations, media, and administrative actions never enter this path.
- Permit explicit 0–100 percent brightness changes through the same
  high-confidence, token-overlap, unique-light matching boundary. Zero maps to
  off; malformed, ambiguous, unrelated, or out-of-range requests perform no
  action.
- Permit named light colors from a small fixed allowlist only when the color
  and a specific non-generic target token both occur in the original request.
  Resolve exactly one light; unsupported colors, generic targets, ambiguity,
  low confidence, or Home Assistant failure perform no broader fallback call.
- Permit explicit thermostat setpoints only when the local classifier supplies
  a named thermostat, unit, conservative residential value, confidence above
  the configured boundary, and a specific target token copied from the request.
  Resolve exactly one climate entity, read its live unit, convert as needed,
  and fail closed when the unit or state payload cannot be verified.

Near-term responsibilities:

- Narrow runtime lock scope: `_lock` should guard session switching and
  shared-state mutation, not span an entire Claude turn or stream. Today
  `respond()` holds it for the full API call (blocking monitor events and UI
  reads) while `respond_streaming()` takes none — the granularity should be
  made consistent, leaning on the agent's own conversation lock.
- Push panel updates instead of polling: cockpit panels currently own QTimers
  and re-read disk/SQLite on fixed intervals. They should subscribe to runtime
  change events (the inbox already exists), with disk reads cached behind
  mtime checks. Any remaining panel work that can block (network, disk) runs
  off the UI thread, as Now Playing does.
- Run proactive monitors for calendar, Home Assistant, weather, files, and
  system state.
- Add more monitor workers that feed the event inbox: calendar, files, and
  system health.
- Store task state for multi-step work.
- Decide when to interrupt, notify quietly, or stay silent.
- Grow the Obsidian baseline into indexed episodic and semantic memory, with the
  Optiplex as the live store and the NAS as synchronized storage. Stable-ID
  inspection, revision-backed correction, keyed contradiction upserts, basic
  provenance, and isolated extractive session digests are present.
- Expand the initial tool-free local fallback into governed tool calling and
  summarization; memory extraction could also move to the local model to
  make background learning free. Also add a governed
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
     write tool; the write never fires without explicit user confirmation or
     a fresh session-bound receipt proving its ID appeared in the latest lookup.
   - Home Assistant automation changes follow a stricter draft/diff/approve/
     apply/verify workflow with optimistic concurrency and rollback records.

5. Presence
   - GUI cockpit, orb state, voice pacing, interruption, proactive notices, and
     a stable personality.
   - The iOS companion retains an offline device cache while the server/runtime
     owns synchronized sessions, conversation context, titles, and deletion.
- Long-running streamed tool turns reconcile with authoritative server
  history so a client timeout cannot permanently hide a completed result.
- Companion reachability is independent from per-turn activity: an active chat
  keeps a known-good connection online, while the transcript/Stop control owns
  generation progress. Stream UI updates are batched and durable local history
  is written at turn boundaries rather than for every received character.

## Companion API

`app.server` is the authenticated HTTP edge over the runtime. It provides
health, session CRUD/history, blocking chat, and streamed chat endpoints. Every
chat request carries a server session ID; the API selects that session and runs
the turn under the runtime lock so contexts cannot bleed into each other.

The bearer token comes from `JARVIS_API_TOKEN`. The API is a transport adapter,
not a second brain: it must route conversation through `JarvisRuntime` and use
`brain.history` for persisted session metadata.

Companion turns optionally carry a `request_id`. The runtime claims it in
SQLite under its conversation lock, sets a per-worker request context, and
persists the receipt's answer atomically with the transcript pair. Reusing a
completed ID returns the stored answer; changed input/session IDs or an
unfinished receipt cannot execute tools again. History responses expose stable
message `id` and `request_id` fields for exact reconciliation. Receipt lifetime
follows session deletion/history clearing. Older callers may omit the ID.

Stream generators are consumed entirely on a single worker thread. An async
queue bridges bytes to HTTP, so an RLock is never resumed on another pool
thread. Client cancellation detaches delivery; the worker finishes the turn
and history persistence. Companion Stop therefore stops reception, not server
actions. Session deletion uses the runtime lock to wait for an active turn.

The companion preserves unfinished local turns during synchronization and
binds stream callbacks to the originating conversation/generation. Rename and
delete operations use a durable local retry queue with visible failures.

`POST /debug-reports` is the deliberate exception to conversation routing: it
stores user-approved diagnostic evidence without asking a model to analyze it
at submission time. Later review still happens through the normal runtime and
read-only debug-report tools.

The dashboard endpoints (`/api/status/torrents`, `/api/status/media`,
`/api/status/services`, `/api/links`, `/api/home/entities`) are a second
non-conversational surface, aggregated in `app.status_hub` directly over the
integration modules. They keep the thin-client principle: all service
credentials and URLs stay server-side (read from the same `.env` values the
integrations use), responses are TTL-cached in process so companion polling
cannot hammer the NAS, and a down service degrades to a 200 with an `error`
field — never a hard failure. These endpoints are not registered as LLM tools.

Two guarded quick-action routes extend that surface with bounded writes:
`POST /api/home/control` (on/off only, and only for entities on the
`HOME_DASHBOARD_ENTITIES` allowlist — the allowlist is the security boundary
for the app's home surface, not just a display filter; anything else is a
400) and `POST /api/torrents/control` (pause/resume only). Both delegate to
the integrations' existing guarded paths — `call_discovered_service` with its
blocked-domain and confirmation gates, and `manage_torrent` with its
exact-hash and explicit-confirmation gates; `delete_files` is never passed
from the HTTP surface. A successful action invalidates the corresponding
status cache so the app's next poll reflects the change; a refused or failed
call returns 200 with an `error` field.

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

## Memory Learning Settings
- `MEMORY_AUTO_EXTRACT`: true/false, defaults to true. Background post-turn
  fact extraction.
- `MEMORY_EXTRACT_MODEL`: model for extraction, defaults to Haiku 4.5.
- `MEMORY_AUTO_CONFIDENCE`: 0–1, defaults to 0.75. Facts at or above it are
  written directly; below it they become reviewable proposals.
- `MEMORY_EXTRACT_MIN_CHARS`: skip extraction for shorter user turns, default 12.
- `HABITS_MIN_TURNS`: turns logged before the habits digest is injected, default 10.
- `OBSIDIAN_JOURNAL_FOLDER` / `OBSIDIAN_NOTES_FOLDER`: vault-relative folders
  for daily journal notes and titled captures; paths outside the vault fall
  back to the defaults.

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
