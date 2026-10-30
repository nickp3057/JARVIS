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
- Keep the request prefix cacheable end to end. Tool schemas and the persona
  carry 1-hour `cache_control` breakpoints; the system prompt is only the
  persona plus standing orders (both change only when Nick changes them).
  Everything that varies per turn (recalled memory facts, habits, degraded-tool
  health, recent tool results) is appended as a `<turn_context>` block to the
  newest user message of the request only, never stored in history. Two
  5-minute breakpoints sit on the newest block and on the reply before this
  turn's message, so later tool rounds and the next turn read the whole
  conversation from cache. `brain/agent.py` logs first-token time, total time
  and cache read/write tokens for every API round. Never splice anything
  per-turn into the system prompt: it would break the cache for all history.
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
- Self-diagnosis is shipped at the tool layer: `brain/tool_health.py` scores
  every tool call and every home-monitor outage, and degraded tools are
  injected into the prompt so the model stops retrying, names the fault, and
  clears it on recovery. `brain/self_repair.py` is the acting half: probe,
  stage one bounded repair, apply only on approval of that id, verify, and roll
  back a setting change that did not help. Repairs never touch source code.
- Runtime locking rule: `_turn_lock` wraps a whole turn or session switch;
  `_lock` is a short-held state lock and must never wrap a network or model
  call.
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
   - Companion dictation is a permission-gated input adapter only: recognized
     speech populates a reviewable draft and never bypasses the ordinary send,
     session, request-ID, or retry path.
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

`GET /api/capabilities` is the authenticated, versioned negotiation contract
for rolling upgrades. Clients should use its grouped chat, dashboard, and
action flags before assuming a newer Core route exists; the health endpoint
remains a reachability check rather than a feature manifest.

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

`POST /api/alerts/{id}/report` (admin token) is the tap-to-chat entry for
a failure. It takes the bus alert id plus its `created_at` (bus ids restart
at 1 with jarvis-api, so a mismatch is a 404 and the app falls back to the
alert text), gathers read-only evidence through `brain/incident_reports.py`,
stores it as an incident debug report, and returns `{report_id, prompt,
suspected_causes, sections}`. The prompt goes into Chat's composer; nothing
is sent or fixed until Nick sends it and approves a proposed fix through the
existing gates. Suspected causes are deterministic rules, keeping model calls
outside conversation turns limited to the memory extractor and study brief.

`GET /api/repairs` lists pending and recent self-repair proposals;
`POST /api/repairs/{id}/apply` is Nick's Approve tap (the tap is the
approval, like torrent pause/resume) and `/reject` dismisses. Stack fixes
(`brain/stack_fixes.py`) refuse `apply_repair` from the model: they need
`approved_by` "chat" (confirm gate) or "app". They run on a background
thread and report through `self_repair.set_notifier`, which the runtime
points at `raise_alert("repair", ...)`.

The dashboard endpoints (`/api/status/torrents`, `/api/status/media`,
`/api/status/storage`, `/api/status/services`, `/api/links` (each link has
`url` plus `remote_url` from `LINK_REMOTE_HOSTS`; the app picks by probing
the home address),
`/api/home/entities`, `/api/now`, `/api/study/brief`,
`/api/status/media/diagnose` — the read-only media doctor) are a second
non-conversational surface, aggregated in `app.status_hub` directly over the
integration modules. They keep the thin-client principle: all service
credentials and URLs stay server-side (read from the same `.env` values the
integrations use), responses are TTL-cached in process so companion polling
cannot hammer the NAS, and a down service degrades to a 200 with an `error`
field — never a hard failure. These endpoints are not registered as LLM tools.

`/api/now` is the first coherent, typed snapshot rather than a service-shaped
feed. It combines runtime-owned timers and reminders with the next calendar
event plus title-matched quiz/test/exam countdowns. The companion persists the
last successful response, schedules those assessment countdowns locally on the
phone (no push infrastructure), and explicitly marks it stale after relaunch
or a failed refresh; cached state is never
silently presented as live.

The companion namespaces its Now cache by a hash of the resolved server and
credential identity. Reads reject responses from a superseded connection and
coalesce overlapping requests for each section. Fast sections poll every five
seconds while visible; media polls every third cycle and storage every sixth.
Calendar queries behind the Now snapshot have a five-minute cache and cannot
start interactive OAuth from a dashboard request.

The phone registers the briefs from `/api/study/brief` as one local
notification per morning, retaining unchanged requests. Only a good payload
(empty `error`) can replace the schedule; a calendar outage, a transport
failure, or a pre-endpoint Core (which falls back to a phone-side template plan
from the Now countdown list) cannot clear it. JARVIS owns the hour, warning
window, keywords, and tone as `STUDY_BRIEF_*` settings; the phone keeps only an
on/off delivery switch, which requests notification permission the moment it is
enabled. Changes reach the phone on refresh; remote push delivery remains
future work.
Chat, Hub, and Settings are peer destinations. Hub prompts only populate a
reviewable draft; dictation callbacks are bound to one capture generation and
invalidated on stop, backgrounding, or conversation changes.

Two guarded quick-action routes extend that surface with bounded writes:
`POST /api/home/control` (on/off only, and only for entities on the
`HOME_DASHBOARD_ENTITIES` allowlist — the allowlist is the security boundary
for the app's home surface, not just a display filter; anything else is a
400; it waits up to two seconds for Home Assistant to report the new state) and `POST /api/torrents/control` (pause/resume only). Both delegate to
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

- Schwab monitor + streamer: on when `SCHWAB_APP_KEY`/`SECRET`/`CALLBACK_URL`
  are set (`integrations/schwab.py`). Holdings every `SCHWAB_POLL_SECONDS`
  (30) in market hours, prices streamed live; raises `schwab_login` a day
  before Schwab's 7-day login lapses.
- Wake-up monitor: always on; idle unless the wake-up schedule or a one-off
  is set (`brain/wake_alarm.py`). Publishes `wake_up` (`push=False`) for the TV.

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
- `CALENDAR_MONITOR_ENABLED`: true/false, defaults to true when Calendar is configured.
- `CALENDAR_MONITOR_INTERVAL_S`: calendar polling interval, defaults to 120 seconds.
- `CALENDAR_ALERT_LEAD_MIN`: meeting warning lead time, defaults to 10 minutes.
- `PRICE_WATCH_MONITOR_ENABLED`: true/false, defaults to true.
- `PRICE_WATCH_INTERVAL_S`: saved public-page refresh interval, defaults to six hours
  and is clamped to at least five minutes.
- `PRICE_WATCH_DROP_PERCENT`: decrease threshold for a one-time transition alert,
  defaults to 5 percent.

## Alert Bus

Every proactive alert goes through `brain/event_bus.py` (`runtime.raise_alert`
or, for grouped arr incidents, `runtime.push_to_phone`). Publishers never pick a
channel. Subscribers: the phone (`runtime._deliver_alert_to_phone` →
`brain/notifications.py`: APNs companion push, HA notify only as fallback;
`push=False` alerts skip it) and each open `GET /api/clients/events` SSE
stream (replay via `Last-Event-ID`, 15 s keep-alives). `GET /api/alerts/recent` (status:read) returns the same in-memory history newest first for polling clients; the Hub's Needs attention card uses it. History is in memory only and resets when jarvis-api restarts. New monitors should
call `raise_alert`, not `notifications.send`.

Clients other than the iOS companion use their own scoped tokens
(`app/client_tokens.py`, hashed in `memory/client_tokens.json`):
`events:read` for the stream, `status:read` for the read-only `/api/status/*`
and `/api/now` endpoints, `chat` for `POST /chat`, and `settings:write` for
the shared colour theme (`PUT /api/theme`). Everything else stays admin-only
(`JARVIS_API_TOKEN`).

- `JARVIS_WATCH_DEVICES`: comma-separated HA entity ids to watch; empty
  disables the device watch.
- `DEVICE_OFFLINE_ALERT_MIN`: minutes unavailable before alerting, default 10.
- `DEVICE_WATCH_INTERVAL_S`: poll interval, default 60.
- `ARR_STACK_INDEXER_ALERT_AFTER_S`: one indexer must be down this long before
  it pushes, default 1200 (20 minutes).
- NAS host monitor (`integrations/nas_host.py`, runtime `_nas_host_monitor_loop`):
  reads the `host_health` block the read-only status key returns (written by
  root cron `ops/nas/host_health.py`, every 5 min; SMART every 30 min with
  `-n standby` so sleeping disks stay asleep). Alerts as `nas_host` on a
  degraded RAID, failed SMART, bad sectors, new kernel disk errors, a restart
  loop (first sight), or after `NAS_HOST_CONFIRM_CHECKS` polls a stopped or
  unhealthy container, a full volume, hot disk/CPU, or a stale snapshot.
  Two clean polls give one quiet "recovered". An unreachable NAS is left to
  the arr monitor. Settings: `NAS_HOST_MONITOR_ENABLED` (true; also needs the
  status key), `NAS_HOST_MONITOR_INTERVAL_S` (300), `NAS_HOST_CONFIRM_CHECKS`
  (2), `NAS_HOST_IGNORE_CONTAINERS` (the gluetun-namespace arr containers),
  `NAS_VOLUME_ALERT_PCT` (90), `NAS_DISK_TEMP_ALERT_C` (55),
  `NAS_CPU_TEMP_ALERT_C` (85), `NAS_CONTAINER_RESTARTS_ALERT` (3 per hour).

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

## Standing Orders

`brain/standing_orders.py` is a fourth durable store beside history, profile
facts, and habits: free-form directives from Nick about how JARVIS should
behave ("stop saying certainly"). The model writes them through
`add_standing_order` / `remove_standing_order`; `build_standing_orders_section`
renders them into a system block after the persona each turn (never into
the persona text itself). The persona's hard rules require a behavioural correction to be
persisted this way, or as a setting, rather than obeyed once.

## Study Brief Settings

All editable through `update_setting`, so Nick changes the brief by talking to
JARVIS. `brain/study_brief.py` plans one brief per future morning that has an
assessment inside the window and composes the next three bodies with the cheap
extraction model (one call, memoised for six hours on settings + events +
calendar day; template fallback). It is the second sanctioned out-of-turn model
caller after `memory_extractor`.

- `STUDY_BRIEF_ENABLED`: true/false, defaults to true.
- `STUDY_BRIEF_HOUR`: local hour 0–23 the brief fires, defaults to 9.
- `STUDY_BRIEF_WINDOW_DAYS`: 1–60 days of warning, defaults to 14.
- `STUDY_BRIEF_KEYWORDS`: comma-separated calendar-title words counted as an
  assessment (quiz, test, testlet, exam, midterm, final; plurals tolerated);
  also drives the Hub's academic countdown.
- `STUDY_BRIEF_ASSIGNMENT_KEYWORDS` / `STUDY_BRIEF_ASSIGNMENT_WINDOW_DAYS`:
  words that mark work due (homework, lab, project...) and their own, shorter
  warning window, default 7 days. Items carry a `kind` so the brief can say
  "due" for assignments.
- `STUDY_BRIEF_STYLE`: free-form tone note handed to the composer.
- `STUDY_BRIEF_MODEL`: composer model; falls back to `MEMORY_EXTRACT_MODEL`.

## NAS recovery boundary

The NAS owns Docker execution through `ops/nas/recover_stack.sh`. The common
chain serializes against existing cron via `/volume1/docker/watchdog/stack.lock`.
An inherited lock descriptor is reused, avoiding nested flock deadlocks. Only
gluetun and its five named dependents are recreated, with `--no-deps`; other
containers, including mooney-plaid-backend, are outside this capability.
A status file records each phase, result, and verified DHT count. Root cron
retains its current triggers; Jarvis will request recovery only after approval.

Jarvis authenticates as a dedicated non-admin NAS user (`Jarvis`, not Nick's
own admin account) with distinct forced-command recovery and read-only status
keys. UGOS ships a global `ForceCommand` that overrides per-key `command=`
restrictions and gives admin/root-group logins an unrestricted shell instead,
so the account must stay non-admin, and a `Match User Jarvis` /
`ForceCommand none` drop-in (`ops/nas/sshd-jarvis.conf`) is required to
restore normal restriction enforcement for it. The recovery entry validates a
UUID, invokes only a fixed sudo launcher, and detaches a worker while
retaining the flock. Durable receipts prevent duplicate requests; completion
is correlated by request ID through the read-only key. SSH transport failure
cannot imply recovery success. The installer protects script/Compose
ancestors and records ACL backups before adding sudo. Neither a Docker socket
nor a root SSH identity reaches Jarvis.

## Arr monitoring and dead-man heartbeat

Heartbeat (2026-10-27): the loop waits for the arr monitor's first poll
before judging or pinging (it used to raise "Dead-man heartbeat not
delivered" on every jarvis-api start), and a missed ping is an inbox line
only, never a bus/phone alert: Healthchecks.io is what pages Nick when pings
stop.

`JarvisRuntime` owns the opt-in arr monitor and heartbeat threads. The polling
loop calls only Python HTTP/SSH integrations: qBit transfer info (with
sync/maindata fallback when DHT is absent), Radarr/Sonarr v3 health, Prowlarr v1
health when configured with a key (otherwise public readiness), and Byparr
readiness. Unknown/malformed DHT never counts as zero. Three consecutive zeros
trigger an alert; unreachable APIs and health errors alert on the first check.
Per-condition 30-minute cooldowns persist in `memory/arr_stack_alerts.json`.
Alerts use the existing runtime inbox/voice path and the configured HA mobile
notification service. Failed phone delivery remains visible in the inbox.
Each alert adds watchdog cooldown timestamps and latest recovery status using
the separate read-only SSH key; unavailable status is explicitly unknown.

Alert hysteresis: a condition alerts only after `ARR_STACK_ALERT_CONFIRM_CHECKS`
consecutive failing polls and recovers only after
`ARR_STACK_RECOVERY_CONFIRM_CHECKS` clean ones. While the NAS reports a
recovery running (or finished within the settle window), or Jarvis is
following its own recovery, new conditions keep counting but are held; one
that outlasts the maintenance alerts on the next poll. The status SSH is only
used when something already looks wrong. Found 2026-09-22 from HA's recorder:
every "unreachable" page in the preceding two days was either a one-poll blip
or the NAS DHT watchdog's own container recreation.

Phone delivery: every pushed alert goes through `brain/notifications.py`
(`runtime.push_to_phone`). Channel order is JARVIS companion push (APNs),
then Home Assistant `notify.mobile_app_*` as fallback only. APNs uses an ES256
provider JWT (signed with `cryptography`, reused 50 minutes, refreshed once on
`ExpiredProviderToken`) over one persistent HTTP/2 `httpx` client. Each
registered device is tried; `BadDeviceToken` retries the other environment
once and remembers it, `410`/`Unregistered` prunes the device, and delivery
succeeds if any device accepts. The app registers through
`POST /api/push/register`; `GET /api/push/status` reports the active channel
and the first missing setup piece. A failed delivery on every channel records
a "Phone alert delivery failed" inbox event.

Incident push policy (`brain/arr_incidents.py`, 2026-09-29): the tracker
decides what is failing; the policy decides what is pushed. Root cause of the
noise: a 2-5 minute upstream outage restarts gluetun's VPN, qBittorrent's DHT
goes stale, the NAS DHT watchdog acts ~15 minutes later, and the recreate it
runs made Radarr/Sonarr/qBit look unreachable and Prowlarr report "all
indexer proxies unavailable" while Byparr cold-starts, i.e. 6-10 pushes per
incident, about six incidents a day.
- Watchdog hold: when the read-only status shows a new watchdog/shared
  recovery action (recovery record `started_at`, or the `.last_action` /
  `.rotate_cooldown` stamps; NAS clock skew cancels via `observed_at`), every
  arr push is held for `ARR_STACK_WATCHDOG_HOLD_S`. At the end the stack is
  re-checked: healthy gives one passive "Recovered after watchdog: <what>,
  took N min" (N = action to first clean poll); anything failing gives one
  alert listing it. Prowlarr indexer backoff is excluded from that check: it
  persists after a recreate until the indexers are re-saved in the Prowlarr
  UI, so those indexers are noted in the summary, stay quiet, and reach the
  daily digest if they last. A Jarvis-requested recovery gets no summary (its
  own follow-up reports). A stamp with no matching recovery record is described
  as a qBittorrent restart (the NAS's stage 1).
- Minimum downtime: a condition pushes only while still present and after
  `ARR_STACK_MIN_DOWNTIME_S`; zero DHT and "qBittorrent disconnected" wait
  `ARR_STACK_DHT_ALERT_AFTER_S` instead, so the watchdog gets its turn. A
  condition that never pushed never sends "Recovered".
- Grouping: every push in one incident carries the same APNs `thread-id` and
  `apns-collapse-id` (`arr-stack-<id>`; HA fallback: `group`/`tag`) and
  restates what is still failing, since it replaces the previous banner.
  Recoveries are passive (no sound). A new incident starts once nothing is
  failing or held.
- Escalation: one "Still failing after Nh" per `ARR_STACK_ESCALATION_INTERVAL_S`
  for stack conditions; never for indexers.
- Indexer digest: an indexer backed off for `ARR_STACK_INDEXER_DIGEST_AFTER_S`
  leaves the incident silently and is listed in one passive daily digest at
  `ARR_STACK_DIGEST_HOUR` saying it may need re-saving in the Prowlarr UI.
- Urgent: all four APIs plus the status SSH down for
  `ARR_STACK_NAS_DOWN_ALERT_S` pushes even during a hold.
State persists in `memory/arr_stack_incidents.json`. Advisory Radarr/Sonarr
health lines still go to the inbox only. Replay tests: `tests/test_arr_incidents.py`.

Per-indexer alerts: with a Prowlarr key, each poll reads
`/api/v1/indexerstatus`; every enabled indexer whose failure backoff
(`disabledTill`) is in the future becomes its own `indexer:<id>` condition
with the same hysteresis, severity `warning` (pushed like critical, but it
does not open the post-recovery grace window). Prowlarr's aggregate
"Indexers unavailable due to failures" health line is dropped when that
succeeds, and kept as the fallback when it does not. Indexer names are cached
for 15 minutes.

The restart tool only stages a session-bound, ten-minute proposal. Original
user text is checked at the agent boundary, before model/offline routing, in
both ordinary and streaming turns. Only the exact returned confirmation phrase
can consume the proposal and invoke SSH. Tool arguments, model-generated
`confirmed=true`, stale tokens, and another conversation cannot authorize it.
The runtime's existing conversation lock serializes the turn; the short state
lock remains free for monitoring. After launching (never retried) the turn
replies immediately and a background `arr-recovery-follow` thread takes over
the restart lock. It polls every 15 s for up to `ARR_RECOVERY_FOLLOW_S`
(default 720): the NAS's correlated status when the read-only key works, and
live HTTP probes of qBittorrent/DHT, gluetun (NAS health, or inferred from
qBit DHT since qBit shares its network namespace), Radarr, Sonarr and
Prowlarr either way, never judging success before the ~160 s chain minimum.
The result (per-service up / still starting / failed) goes to the inbox and
the phone via `runtime._report_arr_recovery`. Near-miss confirmations (bare
token, typo, wrong token) re-prompt with the same token and quote what was
sent; re-asking reuses the open request. Canonical `restart arr stack` works
without an LLM.

Heartbeat pings use the Healthchecks success API (HTTPS GET, no redirects, ten
second timeout), every five minutes by default. When arr monitoring is enabled,
pings stop if its thread dies or the latest complete polling cycle is stale.
A completed cycle reporting broken NAS services still counts as a live Jarvis.
This measures process/monitor liveness, not whether every conversation/model
call can complete. No ping URL means no heartbeat thread; configure the
Healthchecks account's notification integration and grace period separately.

Admin-only environment controls (not model-editable settings):

| Variable | Default / purpose |
| --- | --- |
| `ARR_STACK_MONITOR_ENABLED` | false; enable runtime polling |
| `ARR_STACK_MONITOR_INTERVAL_S` | 60; minimum 30 seconds |
| `ARR_STACK_ALERT_CONFIRM_CHECKS` | 3; consecutive failing polls before a new condition alerts (one-poll blips stay silent) |
| `ARR_STACK_RECOVERY_CONFIRM_CHECKS` | 2; consecutive clean polls before "Recovered" |
| `ARR_STACK_DHT_ZERO_CHECKS` | 10; zero-DHT polls before alerting (a normal recovery holds DHT at zero for ~8 min) |
| `ARR_STACK_MAINTENANCE_SETTLE_S` | 300; hold new alerts this long after a NAS recovery finishes (needs the status key) |
| `ARR_STACK_ESCALATION_INTERVAL_S` | 21600; one reminder per window while unresolved (stack conditions only; indexers go to the digest) |
| `ARR_STACK_POST_RECOVERY_GRACE_S` | 300; tracker-internal grace after a critical recovery (pushes are decided by the incident policy) |
| `ARR_STACK_WATCHDOG_HOLD_S` | 1200; hold all arr pushes this long after a NAS watchdog/shared recovery action, then one summary or one alert; 0 disables |
| `ARR_STACK_MIN_DOWNTIME_S` | 240; a condition must be failing this long before it pushes (shorter blips send nothing, including no "Recovered") |
| `ARR_STACK_DHT_ALERT_AFTER_S` | 1500; zero DHT / qBit disconnected wait this long from the first zero so the NAS watchdog (acts at ~15 min) goes first |
| `ARR_STACK_NAS_DOWN_ALERT_S` | 600; all APIs and the status SSH down this long pushes even during a hold |
| `ARR_STACK_INDEXER_DIGEST_AFTER_S` | 21600; indexer backoff older than this moves to the daily digest |
| `ARR_STACK_DIGEST_HOUR` | 9; local hour of the passive daily indexer digest |
| `ARR_STACK_NOTIFY_SERVICE` | blank; HA `notify.mobile_app_*` service (fallback channel; `JARVIS_NOTIFY_HA_SERVICE` overrides) |
| `APNS_ENABLED`, `APNS_KEY_PATH`, `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_BUNDLE_ID` | companion push (token-based APNs over HTTP/2); primary once enabled and a device has registered |
| `PROWLARR_URL`, `PROWLARR_API_KEY` | API base and optional key; v1 health plus per-indexer `indexerstatus` with key, `/ping` without |
| `BYPARR_URL` | Byparr base URL, usually NAS port 8191 |
| `ARR_STACK_RESTART_ENABLED` | false; enable only after restricted-key tests |
| `NAS_SSH_HOST`, `NAS_SSH_USER` | host required; user defaults to the dedicated non-admin account `Jarvis` |
| `NAS_RECOVERY_SSH_KEY`, `NAS_STATUS_SSH_KEY` | separate absolute private-key paths |
| `NAS_SSH_KNOWN_HOSTS` | dedicated pinned NAS host-key file |
| `JARVIS_HEALTHCHECKS_URL` | secret `https://hc-ping.com/...` success URL; blank disables |
| `JARVIS_HEALTHCHECKS_INTERVAL_S` | 300; minimum 60 seconds |

URLs and credentials reuse existing qBit/Radarr/Sonarr and Home Assistant
settings. Configure monitor startup flags/heartbeat URL before starting the
runtime; interval/cooldown/delivery values are read each cycle. Editing `.env`
externally requires reloading the environment or restarting Jarvis.


When arr monitoring or a Healthchecks URL is configured, the companion server's
ASGI lifespan creates the runtime before accepting requests. Monitor startup
therefore does not depend on opening the phone app after a reboot. A creation
lock prevents simultaneous first requests from creating duplicate runtimes;
server shutdown signals the monitor stop event.

## Room voice (2026-10-28)

`speech/room_voice.py` is a Presence-layer front end like the GUI and the
apps: it runs inside Core on the shared runtime (`ROOM_VOICE=1`), uses
`runtime.respond(..., spoken=True)` for conversation and `app.status_hub`
for its quick home commands, and reports its state through
`brain/voice_presence.py` (`/api/voice/stream`) so screens can show it.

### Review corrections (2026-10-29)

- Repair command definitions are administrative `.env` configuration only, never editable through `update_setting`. Command repairs require Nick’s session-bound chat confirmation or app approval, and are bound to the exact command digest at diagnosis; changed/legacy commands require a fresh proposal.
- Room voice selects its own runtime session atomically for every spoken turn. Phone/TV session switches cannot move room transcripts or consume another session’s pending confirmation.
- NAS host monitoring waits out startup grace before advancing persisted alert state, so disk/RAID faults present at boot are delivered after grace instead of swallowed.
