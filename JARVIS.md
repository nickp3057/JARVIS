# Pearson Stark JARVIS — Project Context

> **Read this first in every new session.** It covers what JARVIS is, how it's built, every file that matters, and what to do next.

---

## What This Is

JARVIS is Nick's personal AI assistant — a fully local Python desktop app modelled on Tony Stark's JARVIS crossed with TARS from Interstellar. It lives on his Windows machine as a PyQt6 GUI with an animated red orb, iMessage-style chat bubbles, a voice wake-word pipeline, and a growing set of tools that let it actually control his computer and smart home.

The character never breaks. It calls Nick "sir", uses dry British wit, answers in 1-3 spoken sentences, and never refers to itself as Claude or an AI.

---

## Stack

| Layer | Tech |
|---|---|
| LLM brain | Anthropic Claude (Haiku 4.5 by default, configurable) |
| Local fallback | Optional OpenAI-compatible endpoint for tool-free conversation |
| STT | faster-whisper (small.en model) |
| Wake word | openWakeWord ("hey jarvis") |
| TTS | ElevenLabs Flash v2.5 (Piper as fallback) |
| UI | PyQt6 — custom QPainter orb, QTextEdit chat, status column |
| Home control | Home Assistant REST API |
| Media library | Radarr + Sonarr v3 REST API (movie / TV requests) |
| Music | Spotify Web API via spotipy (Premium required) |
| Memory | SQLite history + JSON facts + selective Obsidian Markdown retrieval |
| Settings | `.env` file, hot-reloaded at runtime |

---

## File Map

```
Pearson-Stark-Jarvis/
├── app/
│   ├── main.py              Entry point. GUI / text / voice / server modes
│   ├── client_tokens.py     Scoped per-client tokens (events:read, status:read) + CLI; hashes in memory/client_tokens.json
│   └── server.py            Authenticated HTTP bridge for the iOS companion (+ /api/clients/events SSE)
│
├── brain/
│   ├── agent.py             JarvisAgent: session management, tool loop, history
│   ├── apps.py              Windows app launcher (open/close/focus via PowerShell)
│   ├── automation_workshop.py Staged HA automation drafts, diffs, apply + rollback
│   ├── clipboard_tool.py    Read/write clipboard via PowerShell Get/Set-Clipboard
│   ├── debug_reports.py     Durable user-submitted companion failure reports
│   ├── incident_reports.py  Tap-to-chat incident report: live health, NAS watchdog logs, app log warnings, recent changes, rule-based suspected cause
│   ├── history.py           SQLite conversation history with session support
│   ├── intents.py           Keyword intent router (fallback if brain offline)
│   ├── memory.py            JSON long-term memory, correction history, inspection
│   ├── memory_extractor.py  Background post-turn learning: auto-saves facts, journals
│   ├── memory_proposals.py  Reviewable staging for low-confidence inferred facts
│   ├── habits.py            Usage-pattern log + derived "what you've noticed" digest
│   ├── knowledge_vault.py   Authorized Obsidian search, profile mirror, capture + journal
│   ├── local_model.py       Optional OpenAI-compatible conversational fallback
│   ├── maintenance.py       Recurring chores: countdowns + completion log (SQLite)
│   ├── briefing.py          Spoken situational briefing (time, weather, agenda, timers)
│   ├── personality.py       System prompt builder (humor%, address, tool descriptions)
│   ├── price_watch.py       Guarded public-page price checks + durable targets
│   ├── reminders.py         Persistent reminders & to-dos (timed + open list)
│   ├── router.py            Top-level request dispatcher
│   ├── runtime.py           JarvisRuntime — wraps agent + status snapshot
│   ├── settings.py          Whitelist-based .env editor (update_setting tool)
│   ├── standing_orders.py   Durable behavioural directives from Nick, injected every turn
│   ├── tool_health.py       Self-diagnosis ledger: per-tool failures, degraded flags, prompt warning
│   ├── notifications.py     Phone alert router: companion push (APNs, stubbed) then HA notify fallback
│   ├── event_bus.py         Single alert bus: publish once; phone (APNs→HA) and SSE client streams subscribe
│   ├── arr_stack.py         Arr alert hysteresis/per-indexer tracking, restart confirmation gate, async recovery follow-up
│   ├── confirm_gate.py      Generic session-bound "yes"/"approve <code>" gate for side-effecting fixes (media doctor)
│   ├── arr_incidents.py     Arr push policy: watchdog-recovery hold + summary, minimum downtime, grouped incidents, daily indexer digest
│   ├── stack_fixes.py       Tier-2 allowlist of NAS-stack fixes (HA restart, indexer re-test, Plex rescan, arr recovery), each with a post-fix check
│   ├── self_repair.py       Governed self-repair: diagnose a subsystem, stage a bounded fix, apply on approval
│   ├── study_brief.py       Daily quiz/exam countdown: settings, planning, JARVIS-composed text
│   ├── timers.py            Timer store: set/cancel/pause/resume/dismiss/alarming state
│   ├── tools.py             All tool schemas + dispatch (45+ tools)
│   ├── vision.py            Screenshot via PyQt6 + Claude vision API call
│   └── weather.py           Open-Meteo weather lookup
│
├── integrations/
│   ├── home_assistant.py    HA REST API: control_device / list_entities
│   ├── google_calendar.py   Read-only Google Calendar: agenda / next event / alerts
│   ├── qbittorrent.py       Live torrent progress, speed, ETA + completion monitor
│   ├── servarr_common.py    Shared Radarr/Sonarr HTTP client + quality/root-folder
│   │                        resolver + NL quality-phrase fuzzy matching
│   ├── radarr.py            find_movie (lookup) / add_movie (confirm-gated)
│   ├── sonarr.py            find_series (lookup) / add_series (confirm-gated)
│   ├── nas_host.py          NAS host-health snapshot (containers, RAID, SMART, volumes, temps): debounced alerts + incident-report lines
│   ├── media_doctor.py      Traces one title request→arr→indexer→qBit→import→Plex; names the stalled stage; fixes via confirm_gate
│   ├── plex.py              Minimal Plex client (title search + library rescan) for the media doctor
│   └── spotify.py           Spotipy wrapper: play/pause/skip/volume/transfer/now-playing
│
├── speech/
│   ├── audio.py             MicStream rolling buffer, record_until_silence, rms()
│   ├── chime.py             Activation chime playback
│   ├── stt.py               faster-whisper Transcriber
│   ├── tts.py               ElevenLabs TTS with RMS envelope computation
│   ├── voice_loop.py        Headless voice loop (--voice mode)
│   └── wake.py              openWakeWord WakeDetector
│
├── ui/
│   ├── chat_widget.py       iMessage-style bubbles, QThread agent/speak workers
│   ├── main_window.py       JarvisMainWindow: header buttons, orb, chat, status column
│   ├── hud.py               HUDFrame: cyan corner-bracket panel chrome
│   ├── orb.py               Animated QPainter orb (cyan; amber THINKING, red ALERT)
│   ├── status_panels.py     Right column: NowPlaying, Runtime, Tasks, Home, Weather,
│   │                        Events, Sessions, Memory, Timers
│   └── voice_runner.py      VoiceRunner QThread: wake→listen→think→speak loop
│
├── memory/
│   ├── history.db           SQLite conversation history (auto-created)
│   ├── maintenance.db       Maintenance tasks + log (auto-created, seeded once)
│   ├── profile.json         Long-term memory facts (auto-created)
│   ├── habits.json          Bounded per-turn usage log for habit learning
│   ├── memory_proposals.json Low-confidence facts awaiting approval
│   └── obsidian/            Default vault: Jarvis Memory/{Profile.md,Journal/,Notes/}
│
├── tv/                    JARVIS TV: the TCL Google TV's home screen (see "JARVIS TV")
│   ├── launcher/            Hand-built Android app com.tvhome (no Gradle): build.sh, deploy.sh
│   │   └── src/com/tvhome/  HomeActivity (glance + Plex/Settings), ReactorView (phone's mark), Jarvis (Core client), AlertStream
│   │                        + Banner (live alerts over any app), NightlyRefresh (no-sound fix),
│   │                        RemoteBridgeService (voice -> Plex, boot cleanup), SelfAdb
│   ├── provision.sh         Creates the TV's scoped token on the Optiplex and hands it to the TV
│   └── restore.sh           Undo: stock Google TV launcher and the removed TCL apps
│
├── ios/
│   └── JarvisCompanion/     Xcode project for the iOS companion app
│
├── .env                     API keys and runtime settings (never commit)
├── AGENTS.md                Agent guidance (symlink to CLAUDE.md)
├── ARCHITECTURE.md          Runtime layers and architectural direction
├── CLAUDE.md                Shared repository/agent working instructions
├── JARVIS.md                This file
├── create_shortcut.py       One-time: creates JARVIS.lnk on the Desktop
└── requirements.txt         Python deps
```

Every commit increments both iOS Xcode version fields: the user-facing
`MARKETING_VERSION` and the integer `CURRENT_PROJECT_VERSION` build number.
Debug and Release configurations must stay identical.

---

## How a Turn Works

### Chat mode (typed)
1. User types → `ChatWidget._on_send()` → disables input
2. `_AgentWorker` thread calls `agent.respond(text)`
3. `JarvisAgent._run_tool_loop()` → Claude API → tool calls → final reply
   (several tool calls in one round run concurrently; each round logs
   `model round N: first token …, cache read …` to the journal)
4. Reply appended to transcript, `input re-enabled immediately` (non-blocking)
5. If not muted: `_SpeakWorker` thread calls `speak(reply)` → ElevenLabs TTS → RMS envelope → orb reacts

### Voice mode
1. `VoiceRunner` (QThread) waits for wake word
2. Snapshots rolling mic buffer pre-chime (fixes trailing wake word)
3. Plays chime → captures utterance → transcribes
4. `agent.respond(text)` → `_speak_interruptible()` with RMS envelope driving orb
5. Follow-up window: short (2.5s) after tools, long (10s) after conversation

---

## The Orb

State machine with 4 states:

| State | Color | Behavior |
|---|---|---|
| IDLE | Dim cyan `(0,150,190)` | Slow 0.25Hz pulse |
| LISTENING | Bright cyan `(0,205,245)` | 1.4Hz pulse, fast rings |
| THINKING | Amber `(240,172,40)` | Fast ring spin (38°/s), 4 spikes — "targeting" mode |
| SPEAKING | Electric cyan `(40,220,255)` | 12 RMS-reactive spikes, breathes with voice |
| ALERT | Hot red `(255,70,50)` | 8 spikes, fast — errors / warnings |

Background is dark blue-black `(8,16,24)` → `(2,5,10)` with faint cyan scanlines.

RMS envelope from ElevenLabs audio drives the orb in real time using a monotonic clock to avoid timer drift. The orb goes THINKING when user sends, SPEAKING when TTS starts, IDLE when TTS finishes or when muted.

---

## Tools (45+)

### Time & Info
- `get_time` — current local time/date
- `get_weather(location?)` — Open-Meteo current + forecast
- `morning_briefing` — spoken greeting + time + weather + running timers

### Home Assistant
- `control_device(command)` — fuzzy natural-language on/off device control;
  explicit off phrases take precedence over location wording such as "on my desk"
- `activate_home_action(kind, name)` — fuzzy-match and activate an existing HA
  scene, script, or automation
- `list_devices(domain?)` — list HA entities, including scene/script/automation
  domains when requested
- `home_status(query?)` — answer read-only live-state questions with friendly
  names, including which lights are on, whether a named device is off, running
  fans, and current thermostat state/temperature
- `test_home_assistant` — ping HA
- `list_home_services(domain?)` — discover the live service/action catalogue
  advertised by the connected HA instance
- `call_home_service(domain, service, entity_id?, service_data?, confirmed?)` —
  generalized control for brightness, color, climate, media players, vacuums,
  and other discovered capabilities. Sensitive physical actions are
  confirm-gated; administrative/code-execution domains are blocked.
- `list_automations` / `inspect_automation(identifier)` — inventory and read
  complete configurations for HA automations managed by the HA editor
- `draft_automation(operation, identifier?, config?)` — locally validate and
  stage a create/update/delete with a reviewable diff; never changes HA
- `apply_automation_draft(draft_id, confirmed)` — apply an explicitly approved
  draft, detect concurrent edits, verify it, and automatically attempt rollback
  if verification fails
- `rollback_automation_change(change_id, confirmed)` — restore the persisted
  pre-change configuration after explicit approval

### Memory & History
- `remember_fact(category, content, key?, confidence?, source?)` — save to
  profile.json; reusing a semantic key revises the existing value in place
- `remember_profile_record(type, name, detail, ...)` — stable typed person,
  preference, project, routine, or goal record with revision-backed updates
- `propose_memory_fact` / `list_memory_proposals` /
  `apply_memory_proposal` / `reject_memory_proposal` — review inferred memories
  through exact-ID approval; proposals never write silently
- `find_memory_contradictions(category, content)` — advisory candidates among
  legacy unkeyed facts; never mutates memory
- `adjust_fact_confidence(id, signal, amount?)` — explicit, audited confidence
  reinforcement or decay
- `list_memory_facts(category?, query?)` — inspect durable facts with stable IDs
  and revision metadata
- `update_fact(fact_id, content, category?)` — correct a contradicted fact in
  place while retaining its last 20 revisions
- `forget_fact(fact_id)` — remove from profile.json
- `search_memory(query)` — search authorized Obsidian folders and return
  bounded excerpts with their note paths
- `capture_note(text, title?)` — "note this" / "write that down": appends to
  today's journal note in the vault, or creates/appends a titled note under
  the notes folder. Notes, not profile facts.
- `observed_habits` — the derived usage digest (when Nick talks, what he asks
  for most). Answers "what have you noticed about me?"
- `list_debug_reports` / `inspect_debug_report(report_id)` — review explicit
  companion-app feedback with the reported response, surrounding chat,
  frustration/correction follow-ups, and non-secret app context
- `clear_conversation` — wipe history.db
- `conversation_history_stats` — turn/session count
- `search_session_summaries(query?, limit?)` — deliberately retrieve bounded,
  topic-ranked digests of prior sessions when Nick asks about earlier work;
  summaries are never injected into unrelated isolated sessions

### Timers
- `set_timer(duration_*, message)` — creates timer
- `cancel_timer(id)` / `pause_timer(id)` / `resume_timer(id)` / `dismiss_timer(id)`
- `list_timers` — all active timers
- UI shows per-timer cards with PAUSE/CANCEL/DISMISS buttons + alarm sound

### Reminders & Calendar *(new)*
- `add_reminder(text, due_iso?/in_minutes?/in_hours?)` — timed reminder or open to-do
- `list_reminders` / `complete_reminder(id)` / `delete_reminder(id)`
- `todays_agenda` — Google Calendar schedule for the rest of today
- `next_event` — the next upcoming calendar event

### Price Watching
- `watch_price(url, label?, target_price?)` — monitor structured price metadata
  on an exact public product URL; private/local destinations are rejected
- `list_price_watches` / `check_prices` / `remove_price_watch(id)`

### Household maintenance
- `log_maintenance(name|task_id, note?, completed_on?)` — "I rotated the
  mattress"; resets the countdown, optionally backdated
- `list_maintenance` — every task, last done, next due
- `add_maintenance_task(name, schedule_type, ...)` — `interval` (every N
  days/months/years), `fixed_calendar` (month + optional day yearly), or
  `log_only`; optional `last_done` starts the countdown from then
- `remove_maintenance_task(name|task_id)`

### Spotify
- `play_music(query)` — natural-language play
- `pause_music` / `resume_music` / `skip_track` / `previous_track`
- `current_track` — what's playing
- `set_volume(percent)` / `list_speakers` / `transfer_playback(device_name)`

### Media Library (Radarr / Sonarr) *(new)*
- `find_movie(title)` — Radarr `/movie/lookup`. Search only; returns best
  match(es) with year + TMDB id, or "no match" / a numbered short-list.
- `add_movie(tmdb_id, quality?, root_folder?, confirmed)` — Radarr `/movie`
  POST with `monitored: true`, `addOptions.searchForMovie: true`. Refuses
  unless `confirmed: true` and the ID came from the same session's latest,
  unexpired lookup; quality is a natural phrase fuzzy-matched to a profile.
- `find_series(title)` — Sonarr `/series/lookup`. Same shape, TVDB id.
- `add_series(tvdb_id, quality?, root_folder?, monitor?, season_folder?, confirmed)`
  — Sonarr `/series` POST with `addOptions.searchForMissingEpisodes: true`.
  `monitor` defaults to `future` (new episodes only); accepts `all`,
  `firstSeason`, `pilot`, `latestSeason`, `none`, or natural phrasing.
- `list_quality_profiles(service?)` — the profile names defined in Radarr /
  Sonarr, for when a quality phrase doesn't match.
- `media_queue(service?)` — live Radarr/Sonarr activity with movie/show/episode,
  progress, size remaining, ETA, tracked status, and error detail
- `media_health(service?)` — current download-client, indexer, disk, and other
  Servarr health warnings
- `media_history(service, limit?)` / `missing_media(service, limit?)` /
  `upcoming_media(service, days?)` — bounded read-only operational views
- **Always confirm first**: the agent must surface the match and get an
  explicit yes before calling `add_*`; never auto-adds on ambiguous results.
  Selection receipts bind the allowed TMDB/TVDB IDs to the conversation for
  15 minutes, and are consumed after a successful add. One selection or “yes”
  is sufficient; configured quality/monitor defaults must not trigger more
  confirmation questions.
  Quality-phrase → profile-name mapping lives in `.env`
  (`RADARR_QUALITY_MAP` / `SONARR_QUALITY_MAP`), not in code.

### qBittorrent
- `download_status(scope?)` — live global transfer speed plus torrent progress,
  ETA, state, and errors; supports downloading/all/completed/seeding/stalled/etc.
- Expired qBittorrent WebUI sessions automatically reauthenticate once before
  reporting failure.
- qBittorrent 5.2's successful `204 No Content` login response is supported in
  addition to the older `200 Ok.` response.
- `test_qbittorrent` — verify WebUI authentication and report the version
- `torrent_inventory(limit?)` — list live torrents with exact hashes
- `manage_torrent(action, hash, confirmed, delete_files?)` — confirm-gated
  pause/resume/recheck/priority/delete; deletion of downloaded files is a
  separate explicit flag and every action revalidates the live exact hash
- Runtime monitor records errors and can announce newly completed downloads.

### Computer Control *(new)*
- `open_app(name)` — opens any app by natural name (Chrome, Cursor, Discord, etc.)
- `close_app(name)` — kills the process
- `focus_app(name)` — brings window to front
- `list_open_apps` — lists running windowed apps

### Clipboard *(new)*
- `read_clipboard` — reads current clipboard text (for "summarize what I copied")
- `write_clipboard(text)` — writes to clipboard

### Vision *(new)*
- `look_at_screen(question?)` — screenshot via PyQt6 → Claude vision API → spoken answer

### Settings
- `update_setting(key, value)` — live-edits `.env` (HUMOR_PERCENT, ADDRESS,
  ELEVENLABS_SPEED, the STUDY_BRIEF_* rules, etc.)
- `get_setting(key)` / `list_settings`
- `web_search` — Anthropic server tool (togglable via WEB_SEARCH_ENABLED)

### Standing orders *(new)*
- `add_standing_order(text)` — persist a behavioural directive from Nick
  ("stop opening with certainly", "temperatures in Celsius"); it is injected
  into every later prompt until removed, so a correction sticks
- `list_standing_orders` / `remove_standing_order(identifier)` — by id or a
  unique fragment of the wording. Stored in `memory/standing_orders.json`,
  capped at 40 short lines. Persona rules tell JARVIS to record one whenever
  Nick corrects his behaviour instead of complying once.

### Self-diagnosis *(new)*
- `system_health` — which tools/integrations are failing right now (consecutive
  errors, last error text, when), which recovered, and which are slow.
- `clear_tool_health(tool?)` — drop the fault flag on one tool (or all) after
  Nick fixes the cause. Backed by `brain/tool_health.py` and
  `memory/tool_health.json`: every `execute_tool` call is scored (a raised
  exception, a `Tool error` reply, or an outage phrase such as "not
  configured"/"unreachable"/"timed out" counts as a failure); two failures in a
  row, or four inside an hour, mark the tool degraded. Degraded tools are
  injected into the dynamic system block as a "Systems health" section telling
  JARVIS not to retry, to name the subsystem and likely fix, and to clear the
  flag once Nick says it's repaired. The section disappears when everything is
  healthy, so it costs no tokens in the normal case. The Home Assistant live
  monitor reports into the same ledger as `home_assistant_monitor`.

### Self-repair *(new)*
- `diagnose_tool(tool)` — maps the failing tool to its subsystem playbook
  (Home Assistant, Spotify, Radarr, Sonarr, media views, qBittorrent, weather,
  local model), probes it read-only (required `.env` keys present? host:port
  reachable?), and stages ONE repair proposal with an id. Never applies.
- `apply_repair(proposal_id, confirmed, value?)` — executes a staged proposal
  only with `confirmed=true` after Nick approves that id. Three bounded
  actions: `set_setting` (one schema-validated `.env` key, old value kept and
  rolled back automatically if the endpoint is still unreachable afterwards),
  `run_command` (a command Nick pre-approved by name in `SELF_REPAIR_COMMANDS`;
  JARVIS can never compose a command), or `clear_flag` (transient fault).
- `propose_stack_fix(fix, reason)` — stages one allowlisted NAS-stack fix
  (`brain/stack_fixes.py`): `restart_home_assistant` (then waits until HA
  answers), `retest_indexers` (Prowlarr test-all, then checks none is backed
  off), `refresh_plex`, `arr_recovery` (the existing restricted recovery via
  `arr_stack`, with its own follow-up). It never runs anything: in chat it
  opens the confirm gate ("yes" / code) and the app shows it on Home →
  Repairs with Approve / Dismiss. `apply_repair` from the model is refused
  for stack fixes. They run in the background and report the verified
  outcome as a `repair` alert. Nothing restarts without Nick (his rule).
- `list_repair_proposals(status?)` / `reject_repair(proposal_id)`.
- Stored in `memory/repair_proposals.json` via `brain/self_repair.py`. This is
  the "propose → approve → apply → verify → rollback" loop from the governed
  self-modification roadmap item, scoped to configuration and services, not
  source code.

### Lesson capture *(new)*
- `brain/memory_extractor.py` now also asks the extraction model for
  `corrections`: clear "from now on" behavioural instructions in the turn.
  Any correction at confidence 0.85+ that no existing standing order already
  covers is written with `add_standing_order` automatically and surfaces as a
  "Lesson learned" memory event, so a correction sticks even when the
  conversational model forgot to persist it. The existing orders are fed into
  the extraction prompt so wording is reused rather than duplicated.

### Study brief *(new)*
- `study_brief_preview` — read back the daily study brief's settings and what
  the next notification will say. The brief itself is served to the phone by
  `/api/study/brief`; JARVIS composes the next three mornings' text in
  character (template fallback if the model is unavailable). Changing any
  `STUDY_BRIEF_*` setting recomposes on the next request.

---

## Key Design Patterns

**Session-based history**: Every launch creates a new session UUID. `append_turn(user, reply, session_id=...)` persists turns to SQLite. The Sessions panel shows all past sessions as clickable cards — clicking one loads that session into the agent context and repopulates the transcript.

**Non-blocking input**: Input is re-enabled as soon as the agent replies, before TTS completes. Mute button skips TTS entirely and immediately emits `speak_finished` so the orb resets.

**Live settings**: `.env` is written directly by `update_setting`. All settings are read fresh at call time (`os.getenv()` inside functions, not at import time). JARVIS can change its own behavior mid-conversation.

**RMS envelope**: `tts.py` computes a per-frame RMS envelope from the ElevenLabs WAV before playback. `voice_runner.py` drives the orb's `rms_level` using a monotonic clock thread so audio and visual stay in sync.

**Automatic memory** (`brain/memory_extractor.py`): after every completed turn the agent hands the exchange to a single background worker thread. The worker logs a habit signal, then makes one cheap forced-tool Haiku call that compares the exchange against existing memories and returns zero or more facts (`explicit` vs `inferred`, with confidence and an optional stable key or `supersedes_id`) plus an optional one-line `episode`. Facts at or above `MEMORY_AUTO_CONFIDENCE` are written with `remember_fact` / `update_fact` (source `auto:explicit` / `auto:inferred`); lower ones are staged in `memory_proposals.py` for approval. The episode is appended under `## Log` in today's vault journal note. The runtime subscribes and records a `memory` event ("Memory updated") so the cockpit shows it. Extraction failures are logged and never affect the reply.

**Habits** (`brain/habits.py`): no model involved. Each turn appends `{time, hour, weekday, [tool, target]}` to a bounded JSON log (meta/memory tools excluded; targets come from the tool's natural argument such as `play_music.query`). `render_habits_block()` derives a few plain observations (peak period, weekday/weekend skew, most-used capabilities, frequent targets with their usual time of day) once `HABITS_MIN_TURNS` are logged, cached behind the file mtime, and the agent injects it as a "What you've noticed" section in the per-turn `<turn_context>` block on the newest user message.

**Vision**: `brain/vision.py` uses `QScreen.grabWindow(0)` (PyQt6, no extra deps) to capture the screen, encodes to PNG/base64, then makes a separate Claude API call with the image. Falls back to a PowerShell `System.Drawing` capture in headless mode.

---

## .env Variables

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...

# Spotify (optional but great)
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_PREFERRED_DEVICE=JARVIS  # device name substring

# Home Assistant (optional)
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=...
# Legacy HA_URL / HA_TOKEN names are also accepted during migration.
HOME_MONITOR_ENABLED=true
HOME_MONITOR_TRANSPORT=websocket  # use polling/rest only as a fallback
HOME_MONITOR_RECONNECT_S=5
HOME_MONITOR_DOMAINS=light,switch,fan,media_player,binary_sensor,person,device_tracker,lock,cover,climate

# Radarr / Sonarr (optional — movie & TV requests)
RADARR_URL=http://localhost:7878
RADARR_API_KEY=...
SONARR_URL=http://localhost:8989
SONARR_API_KEY=...
# root folders: leave blank to use the service's first root folder
RADARR_ROOT_FOLDER=
SONARR_ROOT_FOLDER=
# quality phrase used when a request omits one
RADARR_DEFAULT_QUALITY=1080p
SONARR_DEFAULT_QUALITY=1080p
# future | all | firstSeason | latestSeason | lastSeason | pilot | none
SONARR_MONITOR_STRATEGY=future
SONARR_LANGUAGE_PROFILE_ID=       # Sonarr v3 only; blank = first listed profile (v4 ignores it)
# NL phrase => quality-profile name (exact name from the app); ';'-separated
RADARR_QUALITY_MAP=4k efficient => UHD Bluray + WEB; 1080p => HD-1080p; 4k => Ultra-HD
SONARR_QUALITY_MAP=1080p => HD-1080p; 4k => Ultra-HD

# qBittorrent WebUI (optional, read-only integration)
QBITTORRENT_URL=http://localhost:8080
QBITTORRENT_USERNAME=
QBITTORRENT_PASSWORD=
QBITTORRENT_MONITOR_ENABLED=true
QBITTORRENT_MONITOR_INTERVAL_S=30
QBITTORRENT_ANNOUNCE_COMPLETIONS=true

# Tunable at runtime via JARVIS voice commands
HUMOR_PERCENT=30           # 0-100
ADDRESS=sir                # how JARVIS addresses Nick
ELEVENLABS_SPEED=1.155     # TTS speed; clamped to 0.7–1.2 in speech/tts.py (code default 1.05)
ELEVENLABS_MODEL_ID=eleven_flash_v2_5
DEFAULT_LOCATION=Herriman, Utah
ANTHROPIC_MODEL=auto        # empty/auto = newest Haiku (Models API, checked daily); auto:sonnet; or a full id to pin
ANTHROPIC_MODEL_FAMILY=haiku   # family `auto` follows
ANTHROPIC_EFFORT=low        # effort on 5.x+ models (low|medium|high); ignored on 4.x
ANTHROPIC_THINKING=off      # Haiku 5.x: off keeps replies fast and inside the 1024-token budget
SELF_REPAIR_COMMANDS=            # name=command;name2=command2 JARVIS may run by name after approval, e.g. restart_home_assistant=ssh nas 'docker restart homeassistant'
WEB_SEARCH_ENABLED=true
PRICE_WATCH_MONITOR_ENABLED=true # periodically refresh saved product URLs
PRICE_WATCH_INTERVAL_S=21600     # six hours; minimum five minutes
PRICE_WATCH_DROP_PERCENT=5       # alert after this percentage decrease
PRICE_WATCH_TIMEOUT_S=15

# Voice tuning
WAKE_THRESHOLD=0.5
PREROLL_SECONDS=4.0
FOLLOWUP_TIMEOUT_S=10.0
FOLLOWUP_TASK_S=2.5
INTERRUPT_RMS_THRESHOLD=1500
WHISPER_MODEL=small.en
# Default is one deterministic decode (fast, stable latency on CPU); true
# restores faster-whisper's re-decode-on-low-confidence temperature schedule.
WHISPER_TEMPERATURE_FALLBACK=false

# Feature flags / tuning (all optional, sensible defaults)
STARTUP_NETWORK_GRACE_S=120   # boot grace: monitors retry without recording outages
BRIEFING_ON_LAUNCH=false    # speak a briefing at startup
STREAMING_TTS=false         # opt-in sentence-streamed TTS in every interactive mode
MEMORY_MAX_FACTS=60         # cap facts injected into the prompt (0 = no cap)
MEMORY_RELEVANT_FACTS=12    # query-matched/recent profile facts per turn
OBSIDIAN_VAULT_PATH=        # optional vault root; defaults to memory/obsidian
OBSIDIAN_INDEX_FOLDERS=Jarvis Memory  # comma-separated folders JARVIS may read
OBSIDIAN_MAX_RESULTS=4      # maximum note excerpts retrieved per turn
OBSIDIAN_MAX_CHARS=4000     # hard cap on retrieved note text per turn
OBSIDIAN_MAX_FILE_BYTES=1000000 # skip unusually large Markdown files
OBSIDIAN_JOURNAL_FOLDER=Jarvis Memory/Journal  # vault-relative daily-note folder
OBSIDIAN_NOTES_FOLDER=Jarvis Memory/Notes      # vault-relative folder for titled captures
MEMORY_AUTO_EXTRACT=true    # background post-turn fact extraction (ChatGPT-style memory)
MEMORY_EXTRACT_MODEL=claude-haiku-4-5-20251001 # cheap model used for extraction
MEMORY_AUTO_CONFIDENCE=0.75 # facts at/above this write directly; below -> proposals
MEMORY_EXTRACT_MIN_CHARS=12 # skip extraction for very short user turns ("yes")
HABITS_MIN_TURNS=10         # turns logged before the habits digest is injected
VISION_MODEL=claude-sonnet-4-6
VISION_ALL_MONITORS=true    # capture the whole virtual desktop
WEB_SEARCH_MAX_USES=5       # max web_search calls per turn
BOOT_SEQUENCE=true          # movie-style power-up on GUI launch

# Optional local conversational fallback (OpenAI-compatible API)
LOCAL_MODEL_ENABLED=false
LOCAL_MODEL_URL=http://127.0.0.1:11434  # /v1 is appended when omitted
LOCAL_MODEL_NAME=            # e.g. qwen2.5:7b-instruct
LOCAL_MODEL_TIMEOUT_S=8      # clamped to 1–60 seconds
LOCAL_MODEL_FAILURE_COOLDOWN_S=30 # avoid repeated calls while endpoint is down
LOCAL_MODEL_SEMANTIC_ROUTING=true # classify read-only offline intents
LOCAL_MODEL_HOME_CONTROL_CONFIDENCE=0.85 # minimum for bounded home-control routing
LOCAL_MODEL_SUMMARIES=false # opt-in abstractive digest; extractive fallback remains
LOCAL_MODEL_API_KEY=         # optional; never logged

# iOS companion server
JARVIS_API_TOKEN=           # required bearer token
JARVIS_SERVER_HOST=0.0.0.0
JARVIS_SERVER_PORT=8000
# Companion dashboard (read-only status hub endpoints)
PLEX_URL=                   # Plex base URL for /api/links + /api/status/services
LINK_REMOTE_HOSTS=          # lan=tailscale host pairs; /api/links adds remote_url (e.g. 10.0.0.147=100.97.179.53)
PLEX_TOKEN=                 # X-Plex-Token; enables the media doctor's "is it on Plex" stage and Plex rescan fix
MEDIA_DOCTOR_STALL_MINUTES=30  # no torrent progress for this long counts as stalled (doctor + download monitor)
HOME_DASHBOARD_ENTITIES=    # comma-separated HA entity_ids for /api/home/entities
JARVIS_THEME=arc            # arc | markIII | hotRod: the TV's look, served in /api/glance
MEDIA_STORAGE_PATH=         # local mount of the video library; leave blank to
                            # measure the NAS volume via Radarr/Sonarr diskspace
MEDIA_STORAGE_LABEL=Media storage # friendly Hub label; no key required
# Daily study brief (all voice-editable via update_setting)
STUDY_BRIEF_ENABLED=true    # send the morning assessment countdown at all
STUDY_BRIEF_HOUR=9          # local hour (0-23) the brief fires
STUDY_BRIEF_WINDOW_DAYS=14  # days of warning before an assessment
STUDY_BRIEF_KEYWORDS=quiz,test,testlet,exam,midterm,final # assessment title words
STUDY_BRIEF_ASSIGNMENT_KEYWORDS=assignment,homework,hw,lab,project,paper,essay,due
STUDY_BRIEF_ASSIGNMENT_WINDOW_DAYS=7 # days of warning for work due
STUDY_BRIEF_STYLE=          # free-form tone/emphasis note for the composed text
STUDY_BRIEF_MODEL=          # composer model; falls back to MEMORY_EXTRACT_MODEL

# Google Calendar (optional, read-only)
GOOGLE_CREDENTIALS_FILE=    # path to OAuth client-secret JSON (Desktop app)
GOOGLE_TOKEN_FILE=          # token cache; defaults to memory/google_token.json
GOOGLE_CALENDAR_ID=all      # every calendar on the account (default), or comma-separated ids
CALENDAR_MONITOR_ENABLED=true
CALENDAR_MONITOR_INTERVAL_S=120
CALENDAR_ALERT_LEAD_MIN=10  # warn this many minutes before a meeting
```

## Google Calendar setup (one time)

1. Google Cloud Console -> create a project -> enable the Google Calendar API.
2. Create OAuth credentials of type **Desktop app**; download the JSON.
3. Put its path in `GOOGLE_CREDENTIALS_FILE` in `.env`.
4. On a desktop, launch JARVIS and ask "what's on my calendar" — a browser
   opens once for consent, then the token is cached to
   `memory/google_token.json` and refreshes silently after that. Access is
   read-only.
5. On the headless Optiplex, never rely on step 4 (there is no browser; the
   process just logs a one-line hint). Instead, from the Mac:
   `ssh -L 8765:localhost:8765 jarvis`, then in that session
   `cd ~/jarvis && .venv/bin/python -m integrations.google_calendar --auth`
   and open the printed URL in the Mac's browser. The running server picks up
   the token on its next calendar call. A token refresh that fails because the
   network is not up yet (boot) is retried, not treated as lost consent.
   `GOOGLE_OAUTH_HEADLESS=true` forces headless behaviour on any host.

---

## UI Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  PEARSON STARK // JARVIS     [+NEW CHAT | CHAT  VOICE]   [MUTE]  │
├─────────────────────────────┬────────────────────────────────────┤
│                             │  NOW PLAYING (Spotify, hides if    │
│         ORB                 │  nothing playing)                  │
│   (animated, red,           │  RUNTIME STATUS                    │
│    RMS-reactive)            │  ACTIVE TASKS                      │
│                             │  HOME (HA devices)                 │
│  [chat transcript]          │  WEATHER                           │
│  User bubbles →             │  EVENTS                            │
│  ← JARVIS bubbles           │  RECENT SESSIONS (clickable)       │
│                             │  MEMORY (scrollable facts)         │
│  [Type a message]  [SEND]   │  ACTIVE TIMERS (cards w/ buttons)  │
└─────────────────────────────┴────────────────────────────────────┘
```

---

## Recently Shipped

- **JARVIS TV: JARVIS is the TV's home screen (iOS 1.94 / build 96)** — The
  TCL TV's custom launcher (`com.tvhome`, previously outside the repo in
  `~/tcl-tv-cleanup`) now lives in `tv/` and is JARVIS: reactor mark,
  "Good evening, sir.", weather, clock, then three glance panels (Today:
  timers, next event, assessments within 14 days, maintenance due; To-dos;
  Needs you: the last day's warning/critical alerts and downloads in flight)
  over the app row: a JARVIS-coloured Plex tile and Settings (the escape
  hatch); YouTube and the HDMI inputs stay on the remote's own buttons. The
  reactor is a port of the phone's `JarvisCoreMark` (same rings, tick dial and
  timings).
  It boots straight into JARVIS now instead of jumping to Plex. Data comes
  from one new scoped endpoint, `GET /api/glance` (`status:read`), built from
  the same `status_hub` sections the phone uses plus weather (10-minute
  cache), `ADDRESS` and the theme (`JARVIS_THEME`, same palettes as the
  phone's `JarvisTheme.swift`). The TV follows `/api/clients/events` and
  shows fresh warnings/criticals as a non-focusable banner over any app,
  Plex included. It keeps the last snapshot for offline ("CORE UNREACHABLE ·
  LAST SEEN ...") and rescans its /24 when the Optiplex's DHCP address moves.
  Also fixes **no sound after the TV sat unused**: "off" on this TV is TCL's
  suspend-to-RAM (`tcl_sys_pms_str_enabled=1`; TCL's quirk table lists
  NO_SOUND_AFTER_STR_RESUME), so `NightlyRefresh` reboots it with
  `svc power reboot quiescent`, only while the screen is off: a minute after
  it's switched off if it has been up 20 h+, and from a 4am alarm. A plain
  reboot turns the screen on; a quiescent one stays dark (both verified on
  the TV, ~20 s). TCL converts every app's wakeup alarm to non-wakeup
  (`ro.feature.alarm_wakeup_control`), so the 4am alarm only runs if
  something else wakes the CPU; the screen-off trigger is the reliable one.
  See "JARVIS TV" below.

- **Home tiles, Hot Rod theme, switch fix (1.91 / build 93)** — Tab order is
  Media, Hub, Home, Chat, Settings (Home in the middle, still the default).
  Home's Next up and To do are now two half-width tiles: Next up shows the
  soonest assessment and its countdown ("+N more" if several share the day);
  To do is just the count with a + that opens the list sheet (`TodoSheet`)
  straight into the add field. Hub's Now card lists at most the next seven
  assessments within 14 days and no longer repeats maintenance; the
  Maintenance card is one tappable line ("N due", next task) that opens the
  full list, where swiping right logs a task done. Home switches are small
  three-across tap tiles. `POST /api/home/control` now waits up to two
  seconds for Home Assistant to report the requested state before
  answering (it used to read back the stale state, so the app's switch
  bounced), and the app re-reads entities three seconds after a toggle.
  The reactor's glow no longer breathes (the rings still turn). Chat's empty
  state dropped the suggested prompts. New theme: Hot Rod, Mark III's gold
  and red with arc-reactor cyan for the reactor and control tint (the new
  `reactor` palette role).

- **Home / Hub split, to-dos, smoother reactor, new icon (1.90 / build 92)** —
  Five tabs: Home, Hub, Media, Chat, Settings. Home is a quick look: the
  reactor, greeting, chips for systems, free space and "N things need you"
  (opens Hub). Below that, the next assessment day (every assessment on the
  soonest day) and a To do list with quick add. To-dos are JARVIS's open
  reminders (so "remind me to..." lands there too). The app adds them with
  `POST /api/todos {text}`, and `POST /api/todos/{id}/done` deletes one for
  good (`reminders.remove_reminder`). Due and overdue maintenance chores also
  appear as to-dos; checking one logs it. Hub holds everything Home used to:
  alerts, repairs, Now, maintenance and switches. The reactor animates with
  scoped `repeatForever` animations instead of a per-frame `TimelineView`,
  and no longer jumps when it scrolls or tab-switches back into view. The app
  icon is now the blue reactor wheel.

- **Home as JARVIS, fully themed colours (1.89 / build 91)** — Home opens on
  a large reactor (tap it for a briefing) with a time-of-day greeting ("Good
  evening, sir.") and two live chips: systems and free space. Each chip
  slides to Media, scrolls to the Services or Media drive card, and outlines
  it briefly. The briefing card and metric tiles are gone, and Services now
  sits at the bottom of Media. Every status colour now comes from the theme
  (`JarvisPalette.warning/critical/success`, used through `JarvisTheme.amber`, `.critical` and `.success`;
  no system red/orange/green left). Mark III was reworked: a gold HUD accent,
  hot-rod red for Nick's bubbles, molten-orange warnings, red criticals, and
  arc-reactor blue-white for "nominal". The reactor gains a slowly drifting
  60-tick HUD dial and a hot core.

- **Maintenance tracker + Home cleanup (1.88 / build 90)** — Recurring chores
  with a countdown and a log (`brain/maintenance.py`, `memory/maintenance.db`).
  Seeded once on first run with Rotate mattress (6 months), Replace car scent
  and Trim bonsai (quarterly); deleted seeds never return. Interval tasks are
  due N days/months/years after the last completion (never done = due today;
  month math clamps, so Aug 31 + 6 months = Feb 28). Fixed-calendar tasks are
  due on a month/day yearly, and doing one up to 30 days early counts for
  that year. Log-only tasks just show "done N days ago". Days are
  local calendar days computed on the server. API: `GET/POST /api/maintenance`,
  `PUT/DELETE /api/maintenance/{id}`, `POST /api/maintenance/{id}/complete`
  (optional `note`, `completed_on`), `GET /api/maintenance/{id}/history`,
  `DELETE /api/maintenance/{id}/history/{entry}` (undo a mistaken tap).
  `/api/now` gains `maintenance` (overdue and due within 7 days), so due chores
  show in the Now feed next to assessments. Home gets a Maintenance card (the
  soonest four, a check button to log with an optional note/earlier day,
  rows open the history; add/edit/delete in the app). Voice/chat tools:
  `log_maintenance`, `list_maintenance`, `add_maintenance_task`,
  `remove_maintenance_task`. No push alerts by design. App cleanup: the Launch
  links card and the Media drive card now live only on Media (Home keeps the
  free-space tile), the unused "Control Room" hub mode is gone, and switching
  servers now also clears alerts and repairs.

- **Themes and a living reactor mark (1.86 / build 88)** — Settings → Theme:
  Arc (original cyan) and Mark III (red and gold on warm charcoal; a
  Reactor ice-blue/violet option was tried and removed in 1.87), each with dark/light variants in
  `JarvisPalette`; `RootView` re-identifies on change. `JarvisCoreMark` now
  animates (counter-rotating rings, breathing glow; faster while JARVIS
  thinks, and it leads the typing indicator) via `TimelineView(.animation)`
  with transform/opacity-only frames in one `drawingGroup` layer and no
  per-frame shadow; it stops off screen, with Reduce Motion, and in Low Power
  Mode.

- **Chat and alert polish (1.85 / build 87)** — The keyboard "Done" bar is
  gone (on iOS 26 it floated over the composer and the send button); dismiss
  by swiping down or tapping the transcript, as in Messages. JARVIS replies
  drop the avatar and use the full width. Swiping between tabs now tracks the
  finger and slides the new tab in (Reduce Motion: instant). The dead-man
  heartbeat no longer alerts: it fired on every restart because it checked
  before the arr monitor's first poll, and Healthchecks.io already pages on a
  real outage.

- **One-tap download retry + links that work away from home (1.84 / build 86)**
  — A stall alert now carries its torrent hash; the runtime matches it to the
  Radarr/Sonarr queue item and stages a `retry_download` repair ("Retry The
  Croods (2013)") on Home → Repairs. Nothing runs until Nick approves (tap,
  or "yes" when JARVIS proposes it in chat via `propose_stack_fix` with a
  title). The retry removes the release from qBittorrent, blocklists it, lets
  the *arr search again with its own settings, and checks ~90 s later whether
  a replacement was grabbed. Stall incident reports gain a "This download"
  section. `/api/links` adds `remote_url` from `LINK_REMOTE_HOSTS`; the app
  probes the home address on each Hub refresh and opens it when it answers,
  otherwise the Tailscale URL (Launch card shows "Home network" / "Via
  Tailscale"). New Local Network usage string for that probe.

- **One-tap stack fixes (1.83 / build 85)** — JARVIS can propose four
  allowlisted fixes after diagnosing a failure (`propose_stack_fix`, see
  Self-repair above). Approval is Nick's alone: "yes"/code in chat through
  `confirm_gate`, or Approve on the new Home → Repairs card
  (`GET /api/repairs`, `POST /api/repairs/{id}/apply|reject`). The model's
  own `apply_repair` is refused for these. Each runs in the background and
  reports a verified outcome as an alert; restarts have no rollback, so a
  failed check says so plainly. Uses only existing permissions.

- **NAS host health (1.81 / build 83)** — Root cron on the NAS
  (`ops/nas/host_health.py`, installed by `ops/nas/install-host-health.sh`)
  writes `host-health.json` every 5 minutes: every container's state, health,
  restart count and start time; `/proc/mdstat`; SMART health, temperature and
  sector counts (every 30 min, never waking a sleeping disk); volume usage;
  CPU temperature; fan readings; recent kernel disk errors; the image
  updater's last run. `stack_status.sh` returns it, so the read-only status
  key gains output, not privilege. JARVIS's new NAS host monitor
  (`integrations/nas_host.py`) raises debounced `nas_host` alerts, and
  incident reports gain a "NAS host" section plus suspects such as "the
  updater applied a new gluetun image 13 h before this alert". See
  ARCHITECTURE.md "Runtime Monitor Settings" for the `NAS_HOST_*` settings.

- **Tap-to-chat incident reports (1.80 / build 82)** — Tapping a row in the
  companion's "Needs attention" card calls `POST /api/alerts/{id}/report`.
  `brain/incident_reports.py` gathers, in parallel under a 25 s deadline:
  `nas_stack.poll_health()`, the read-only NAS status key (recovery record,
  watchdog log tails), a `media_doctor` sweep, warning/error lines from the
  Radarr/Sonarr/Prowlarr/qBittorrent/HA log APIs, and recent changes
  (watchdog actions, recoveries, JARVIS repairs, related bus alerts). It
  ranks suspected causes with fixed rules (no model call), stores everything
  as a `debug_reports` entry with `source="incident"` and evidence
  `sections`, and returns a prompt that lands in Chat's composer for review.
  `inspect_debug_report` reads it; fixes still go through the existing gates
  (media doctor/confirm gate, arr restart, self-repair). A stale alert id
  (server restarted) or older Core falls back to a prompt built from the
  alert text. No NAS permission change. Capability flag
  `chat.incident_reports`.

- **Overnight overhaul: alerts, confirmation gate, notifications, efficiency
  (1.35–1.43 / builds 37–45)** — Arr alerts now need 3 consecutive failing
  polls (10 for zero DHT) and 2 clean ones to recover, and are held while a
  NAS or Jarvis recovery runs: HA's recorder showed every recent phone page
  was a one-poll blip or the NAS watchdog's own container recreation. Each
  Prowlarr indexer alerts on its own. A confirmed arr restart replies at once
  and follows up with a per-service up / still starting / failed report
  instead of "outcome unknown"; malformed confirmations reuse the open token
  and echo what was sent. Phone alerts route through `brain/notifications.py`
  (companion push stubbed, HA fallback is primary; device-token endpoints
  exist). The dashboard hub cache is single-flight, stops pulling full
  Radarr/Sonarr libraries every 15 s, and pings services in parallel;
  Whisper decodes in one deterministic pass. See `OVERNIGHT_HANDOFF.md`.

- **Runtime turn lock split (1.29 / build 31)** — `JarvisRuntime` now has
  two locks. `_turn_lock` serializes conversation turns and session
  switching (including companion streams); `_lock` guards only tasks, events,
  snapshots, and monitor state and is never held across a model call. So
  monitors can `record_event()` and panels can read status while a
  multi-second Claude turn is in flight, and GUI/voice streaming is now
  serialized against companion turns instead of racing them.
- **Governed self-repair and lesson capture (1.28 / build 30)** —
  `brain/self_repair.py` closes the loop opened by the health ledger: JARVIS
  can probe the subsystem behind a degraded tool, stage a bounded repair
  (validated `.env` write with automatic rollback, a pre-approved
  `SELF_REPAIR_COMMANDS` entry by name, or a flag clear), and apply it only
  when Nick approves the proposal id. `ANTHROPIC_MODEL` and
  `SELF_REPAIR_COMMANDS` are now editable settings. The memory extractor also
  turns clear behavioural corrections into standing orders on its own.
- **Model auto-update (1.64 / build 66)** — `brain/model_select.py`:
  `ANTHROPIC_MODEL` empty or `auto` follows the newest model in
  `ANTHROPIC_MODEL_FAMILY` (haiku) via the Models API, cached daily in
  `memory/model_cache.json`; a full id still pins. 5.x+ models get
  `effort` (default low) and, on Haiku, thinking off, sent via `extra_body`;
  a model that rejects them is remembered and sent plain requests. The memory
  extractor and study brief follow the newest Haiku unless their own model
  vars are set. A `refusal` stop reason gets a spoken in-character reply.
- **Self-diagnosis and live model selection (1.27 / build 29)** —
  `brain/tool_health.py` scores every tool call so JARVIS knows when one of
  his own integrations is down: degraded tools ride into the system prompt as
  a "Systems health" section (only while something is failing), and the
  `system_health` / `clear_tool_health` tools let him report on and reset his
  own faults. The Home Assistant live monitor feeds the same ledger.
  `brain/agent.py` now reads `ANTHROPIC_MODEL` and `ANTHROPIC_API_KEY` live
  per request, so `update_setting ANTHROPIC_MODEL` switches models without a
  restart (the import-time read was a roadmap bug).
- **Standing orders and a JARVIS-owned study brief (1.22 / build 24)** —
  `brain/standing_orders.py` gives Nick a durable way to change how JARVIS
  behaves by just telling him: the model records a one-line directive with
  `add_standing_order`, it rides in the dynamic system block on every turn,
  and the persona's hard rules say a correction must be persisted rather than
  obeyed once. `brain/study_brief.py` moves the daily quiz/exam countdown's
  rules server-side as `STUDY_BRIEF_*` settings (hour, warning window,
  keywords, on/off, style note), all editable through `update_setting`, and
  composes the next three mornings' notification text in JARVIS's voice with
  the cheap extraction model (template fallback). `/api/study/brief` serves
  the schedule and text for the phone to register as local notifications;
  `study_brief_preview` lets Nick hear what tomorrow's brief will say and
  iterate on it. Calendar assessment detection now reads the same keyword
  setting. On the phone (1.23 / build 25) the Settings toggle now asks iOS for
  notification permission the moment it is switched on (previously the prompt
  waited for the first morning with an assessment, so the app never appeared
  under iOS Notification settings), the local hour picker is gone in favour of
  the schedule JARVIS reports, and the scheduler registers the server-composed
  bodies as local notifications. A calendar outage or transport failure leaves
  the queued briefs alone; an older Core without the endpoint falls back to the
  phone-side template plan.
  1.26 / build 28 lets the Chat keyboard be dismissed: a hide-keyboard button
  appears at the left of the composer while typing, and tapping the transcript
  or empty state also dismisses it. (1.25 tried the system keyboard toolbar,
  which on iOS 27 floats as a capsule over the send button.) The same release
  adds testlets to the default assessment keywords and a second keyword set
  for assignments due, with its own shorter warning window.
- **Media storage measured through Radarr** — with no local mount, the Hub's
  storage card reads Radarr's (then Sonarr's) `/api/v3/diskspace` report and
  shows the volume holding the root folder, so the NAS library is measured
  without mounting it on the Optiplex.
- **Companion experience overhaul (1.19 / build 21)** — Chat, Hub, and Settings
  are now peer tabs. The Hub has Today, Media, and Home focus views, a compact
  two-metric overview, an actionable setup screen, and a briefing shortcut
  that opens an unsent Chat draft. Chat adds inline Markdown, scrollable code
  blocks with Copy, a visible New Chat button, and streaming follow-scroll that
  pauses while reading earlier messages with a Latest button to return.
  Settings shares the cockpit styling and includes a real connection test,
  study-alert toggle, delivery-hour picker, and iOS permission status.
  Daily assessment notifications are now grouped into one brief per day;
  unchanged requests are retained, and calendar failures or older Core replies
  cannot erase the schedule. Now caches are scoped to connection identity;
  overlapping Hub reads are suppressed and old-connection responses discarded.
  Media/storage polling is slower than transfers, and calendar data is cached
  for five minutes with interactive OAuth disabled in the dashboard. Dictation
  rejects callbacks from stopped sessions and cancels on conversation changes
  or backgrounding. Layouts are bounded on wider screens and support Reduce
  Motion; live timers tick locally between Core refreshes.
- **Companion cockpit revamp + dictation (1.18 / build 20)** — Chat and Hub now
  share a layered navy telemetry field, raised cyan glass panels, clearer
  connection/activity states, refined asymmetric messages, a more purposeful
  composer, and a compact six-metric Hub overview. The Control Room uses
  native cards instead of grouped-list chrome while retaining independent
  endpoint loading, stale-data errors, polling, and guarded quick actions.
  Permission-gated native dictation fills the current draft with live partial
  recognition, preserves existing text, stops when Chat leaves, and never
  auto-sends. Core now exposes authenticated `GET /api/capabilities` with a
  versioned chat/dashboard/action feature contract for rolling upgrades.
  The first Now surface adds a typed `/api/now` snapshot for active/alarming
  timers, open reminders, and the next calendar event. The companion persists
  the last good snapshot locally and labels it stale when Core is unreachable.
  Streamed text from distinct model/tool rounds now receives a
  boundary space when neither round supplies one, preventing persisted replies
  such as `Done.Here is the result` without rewriting ordinary deltas.
  The same release adds quiz/test/exam detection from the existing read-only
  Google Calendar feed, a 60-day Hub countdown, and phone-local daily 9 AM
  reminders during the final two weeks. These use iOS local notifications—no
  push service or new API key. The Hub also reports free/used/total capacity
  for exactly one configured media filesystem through `/api/status/storage`.
  `MEDIA_STORAGE_PATH` measures a filesystem mounted on the JARVIS host; when
  it is blank the Hub asks Radarr (then Sonarr) for its `/api/v3/diskspace`
  report and shows the mount holding the root folder, so a library on the
  NAS is measured without mounting it on the Optiplex. Only when neither
  source exists does the panel report an unconfigured state.
- **Guarded price watching** (`brain/price_watch.py`) — `watch_price`,
  `list_price_watches`, `check_prices`, and `remove_price_watch` persist public
  product URLs and inspect JSON-LD/product metadata without a shopping API.
  The runtime checks them every six hours by default and announces target-price
  or material-drop transitions once. Local/private destinations, credentialed
  URLs, unsafe redirects, non-page responses, and pages above 2 MB are blocked;
  JavaScript-only storefronts may report that no structured price was found.
- **Automatic memory + habits + vault capture** (`brain/memory_extractor.py`,
  `brain/habits.py`, `brain/knowledge_vault.py`) — JARVIS now learns the way
  ChatGPT/Claude memory does: a background worker reviews every exchange
  after the reply and saves durable facts on its own (revising superseded
  ones in place, staging low-confidence guesses as proposals), while a
  no-model habit log turns usage into a "What you've noticed" prompt digest.
  New tools: `capture_note` (journal / titled notes in the vault) and
  `observed_habits`. The extractor also writes a one-line daily `## Log`
  entry to the vault when a turn accomplished something. Settings:
  `MEMORY_AUTO_EXTRACT`, `MEMORY_EXTRACT_MODEL`, `MEMORY_AUTO_CONFIDENCE`,
  `MEMORY_EXTRACT_MIN_CHARS`, `HABITS_MIN_TURNS`, `OBSIDIAN_JOURNAL_FOLDER`,
  `OBSIDIAN_NOTES_FOLDER`. Tests: `tests/test_memory_extractor.py`,
  `tests/test_habits.py`, `tests/test_note_capture.py`.
- **Companion reliability and visual refresh (1.17 / build 19)** — shared
  navy/cyan cockpit styling, an illustrated Core mark, draft-only starter
  prompts, timestamped message cards, amber activity, and consistent Chat/Hub
  surfaces. Multiline paste stays in the composer; draft saves are debounced.
  History sync preserves active/unresolved turns and stable server message IDs.
  Every new companion turn carries a durable request UUID; exact-ID recovery
  cannot confuse repeated text, and completed retries reuse the stored answer
  without repeating tools. Interrupted receipts fail closed. Streams finish on
  one worker thread even after a client disconnects; Stop stops reception, not
  already-running server actions. Session deletion is serialized with turns.
  Offline rename/delete edits persist for retry with visible errors. Paused and
  stopped torrents remain visible and resumable; media rows retain queue IDs.
  Home/media refresh alongside transfers/services while the Hub is visible.
  Keychain updates preserve the old value on failure and report storage errors.
  Regression coverage: `tests/test_companion_reliability.py` and the shared
  Xcode scheme's `JarvisCompanionTests` target.
- **iOS Hub screen** (`ios/JarvisCompanion`) — a tab bar now pairs Chat with a
  Hub dashboard rendering the seven server endpoints: per-service up/latency
  rows, live torrent transfers with progress/speed/ETA and per-torrent
  pause/resume (targeting the `hash` now included in `/api/status/torrents`;
  the buttons hide gracefully against an older Core), Radarr/Sonarr
  queues with collapsible recent additions, allowlisted quick-action toggles
  (optimistic, reconciled from the POST response, reverted with an alert on
  failure), and browser links. Pull-to-refresh everywhere; torrents and
  services, home entities, and media auto-poll every 5s (matching the server TTL) only while the Hub is
  visible and foregrounded. Every section loads and fails independently — a
  dead service shows an inline error while the rest render. Settings now
  fills in port 8000 for plain-http addresses typed without a port and shows
  the effective URL. All Hub networking reuses `JarvisAPIClient`.
- **Companion quick actions** (`app/status_hub.py`, `app/server.py`) — two
  bearer-auth'd write endpoints for the iOS hub. `POST /api/home/control`
  turns an allowlisted entity on or off (entity must be in
  `HOME_DASHBOARD_ENTITIES` — the allowlist is the security boundary, not a
  display filter; non-allowlisted entities and unsupported actions are 400s)
  and returns the resulting state (polling up to two seconds for Home
  Assistant to report the requested state); it delegates to the guarded
  `call_discovered_service` path. `POST /api/torrents/control` pauses or
  resumes one torrent via `manage_torrent`, preserving its exact-hash and
  explicit-confirmation gates; `delete_files` is never passed. Successful
  actions invalidate the matching status cache; refused or failed service
  calls return 200 with an `error` field.
- **Companion dashboard endpoints** (`app/status_hub.py`, `app/server.py`) —
  bearer-auth'd, read-only JSON endpoints so the iOS app can render a
  status hub without ever talking to the NAS services directly:
  `/api/status/torrents` (qBittorrent transfers + global rates),
  `/api/status/media` (Radarr/Sonarr queues + 5 most recent library
  additions each), `/api/status/services` (reachability + latency for Home
  Assistant, Plex, Sonarr, Radarr, qBittorrent), `/api/links` (web-UI links
  derived from the same `.env` base URLs the integrations read; unset
  services are omitted), `/api/status/storage` (capacity of only the configured
  media filesystem), and `/api/home/entities` (state of the
  `HOME_DASHBOARD_ENTITIES` allowlist — read-only, no control). `/api/now`
  returns timers, reminders, the next calendar event, and academic countdowns
  as one typed snapshot. A short
  in-process TTL cache (5s torrents/services/entities, 15s media) absorbs
  app polling, and every endpoint returns 200 with an `error` field and
  empty data when a backing service is down. New `.env` vars: `PLEX_URL`
  and `HOME_DASHBOARD_ENTITIES`; a full `.env.example` now exists at the
  repo root. `integrations/qbittorrent.py` gained a structured
  `list_transfers()` used by the torrents endpoint.
- **Efficiency pass: prompt caching + non-blocking cockpit** — API requests
  now mark the tool schemas and the stable persona system block with
  `cache_control: ephemeral`, so the large stable prefix is cached across
  turns and across every round of a tool loop (the per-turn memory block and
  recent tool context stay in a separate uncached system block). The Now
  Playing panel fetches Spotify state on a worker thread instead of blocking
  the UI thread, and SQLite schema creation/migration runs once per database
  path instead of on every query.
- **Streaming standalone voice mode** (`speech/voice_loop.py`) — headless
  `--voice` now pipelines `respond_streaming` into sentence-level TTS when
  `STREAMING_TTS=true`, while retaining mic interruption and blocking fallback.
  An interrupted reply still drains the model stream so persisted history and
  tool-aware follow-up timing remain correct.
- **Download and media-pipeline awareness** (`integrations/qbittorrent.py`,
  `integrations/servarr_common.py`) — JARVIS can report qBittorrent's live
  speeds, progress, ETA, stalled/error state, and seeding status, then correlate
  operational context through Radarr/Sonarr queues and health warnings. A
  runtime monitor notices new download errors and optionally announces
  completions. Exact-hash pause/resume/recheck/priority/delete operations are
  confirm-gated; downloaded-file deletion requires a separate explicit flag.
- **Home Assistant automation workshop** (`brain/automation_workshop.py`) —
  JARVIS can inventory and inspect editor-managed automations, validate entity
  and service references, draft creates/updates/deletes, show a diff, and wait
  for explicit approval of the exact draft ID. Apply detects concurrent edits,
  verifies HA's saved configuration, preserves a local backup, and attempts a
  verified rollback on failure. Rollbacks are also separately confirm-gated.
  Drafts and change records persist in `memory/automation_workshop.json`.
- **Live + generalized Home Assistant integration**
  (`integrations/home_assistant.py`) — the runtime now subscribes to HA's
  WebSocket `state_changed` feed with automatic reconnect instead of waiting
  for the polling interval. JARVIS can discover the instance's live service
  catalogue and call capabilities beyond on/off. Locks, alarms, covers,
  buttons, notifications, and similarly sensitive actions require explicit
  confirmation; administrative and code-execution services are excluded.
- **Reliable Home Assistant off commands + actions**
  (`integrations/home_assistant.py`) — explicit `turn off` / `switch off` and a
  trailing `off` now win over location language such as "the light on my desk."
  HA configuration is read live and accepts both canonical
  `HOME_ASSISTANT_URL` / `HOME_ASSISTANT_TOKEN` names and the older short
  aliases. JARVIS can also fuzzy-match and activate existing HA scenes,
  scripts, and automations with the `activate_home_action` tool.
- **Full companion chat baseline** — authenticated JARVIS Core HTTP server,
  isolated server session IDs, synchronized server/device history, streamed
  replies with Stop, health/offline status, Keychain token storage, saved
  drafts, history search, rename/delete swipe actions, and message copy/share.
  The client recovers stale sessions and falls back to legacy `/chat` when a
  running server has not deployed `/chat/stream` yet. Tool-heavy streamed turns
  have a three-minute timeout and reconcile against server history, preventing
  completed Radarr/Sonarr operations from appearing failed until app reopen.
  Connection status now reflects reachability instead of changing to
  “Connecting…” for every healthy chat request. Stream rendering is batched and
  the local archive is saved once per completed turn, avoiding per-character
  disk rewrites and transcript stutter. Debug-report sheets present reliably
  from menus, show submission progress, reject empty transcript entries, and
  explain when the connected Core needs updating for report support.
- **Anthropic multi-round serialization compatibility** — assistant content
  returned after a custom tool call is sanitized before being sent back to the
  Messages API. Response-only SDK fields such as `parsed_output` are removed,
  preventing immediate HTTP 400 failures on Radarr and other tool-driven turns.
- **iOS local chat history** (`ios/JarvisCompanion`) — conversations persist as
  a device-local JSON archive with a history picker and manual New Chat action.
  Returning after five minutes inactive automatically opens a clean chat while
  retaining the previous conversation. Existing single-transcript data migrates
  in place; server-side history synchronization remains future work.
- **Persona tightening** (`brain/personality.py`) — replies are terse by
  default now (one or two sentences, lead with the answer, no filler / hedging /
  preamble / sign-offs). Media adds confirm once: the user's match selection
  *is* the confirmation, no redundant "are you sure" round-trip (the
  schema-level `confirmed: true` guard is unchanged). `ELEVENLABS_SPEED` raised
  to 1.155 (was the implicit 1.05 default), still clamped to 0.7–1.2 in
  `speech/tts.py`.
- **Radarr / Sonarr requests** (`integrations/servarr_common.py`, `radarr.py`,
  `sonarr.py`) — "add Deadpool in 4k efficient" → JARVIS looks it up, reports the
  match (title, year, TMDB/TVDB id), and waits for an explicit yes before
  adding. Two tools per service: `find_*` (search only) and `add_*`
  (confirm-gated). Quality phrases fuzzy-match a `.env`-defined
  phrase→profile-name map against the live `/qualityprofile` list — no
  hardcoded IDs. TV defaults to monitoring `future` episodes only. No-match and
  multi-match cases are handled explicitly; unconfigured services degrade to a
  polite line like Home Assistant.
- **Media doctor** (`integrations/media_doctor.py`) — "why hasn't Dune shown
  up?" → `diagnose_media` walks Radarr/Sonarr reachability → library →
  monitored → released → grabbed / indexers (Prowlarr) → qBittorrent torrent
  (stalled, dead metadata, paused, missing) → import (blocked/failed messages)
  → Plex, and stops at the first stuck stage in plain English. No title = sweep
  every queued download. Suggested fixes (re-search, monitor + search, replace
  a dead release with blocklist, Plex rescan, restart arr stack) are proposed
  by `apply_media_fix` and run only after Nick's "yes"/"approve <code>"
  (`brain/confirm_gate.py`; restart uses the arr restart gate). Also
  `GET /api/status/media/diagnose?title=&kind=movie|show` (read-only JSON:
  stage, stalled, summary, steps, fixes, candidates).
- **Companion media requests** (`integrations/media_requests.py`) — chat-free
  search → request → confirm for the planned Media tab:
  `GET /api/media/search`, `POST /api/media/requests` (opens a confirm-gate
  proposal, writes nothing), `POST /api/media/requests/confirm {code}`. Uses
  the same receipts and `add_movie`/`add_series` as chat.
- **Reminders & to-dos** (`brain/reminders.py`) — timed reminders ("remind me at
  3pm") and open to-dos, persisted, spoken aloud when due. Tools: add_reminder /
  list_reminders / complete_reminder / delete_reminder.
- **Google Calendar** (`integrations/google_calendar.py`) — todays_agenda /
  next_event tools, agenda folded into the briefing, and a proactive monitor that
  announces meetings as they approach. Read-only OAuth; see setup below.
- **Proactive announcement queue** — runtime `announce()` + `pop_due_reminders()`
  now speak calendar alerts, timers, and reminders in *both* voice and chat modes.
- **Boot sequence + sound** (`speech/sounds.py`, `ui/hud.py`) — movie-style
  power-up on launch: orb spin-up, staggered telemetry init, arc-reactor hum +
  blips, spoken "all systems nominal" greeting (then the briefing). Toggle with
  BOOT_SEQUENCE.
- **Persistent timers** — timers now serialize to `memory/timers.json` and
  rehydrate on launch. A timer that lapses while JARVIS is closed is in the
  alarming state on next start.
- **Morning briefing** — `brain/briefing.py` composes a spoken greeting + time
  + weather + running timers. Exposed as the `morning_briefing` tool ("brief
  me", "good morning") and auto-spoken once per runtime at launch when
  `BRIEFING_ON_LAUNCH=true`. Text, headless voice, animated GUI boot, and the
  GUI's boot-disabled path all share the runtime gate. GUI composition runs on
  a background thread so weather/calendar latency cannot freeze the cockpit.
- **Multi-monitor vision** — `look_at_screen` now captures the full virtual
  desktop (all monitors) by default; set `VISION_ALL_MONITORS=false` for primary
  only. Vision model is configurable via `VISION_MODEL`.
- **Streaming TTS (opt-in)** — `STREAMING_TTS=true` streams Claude's reply and
  speaks it sentence-by-sentence, cutting perceived latency. Text mode, GUI
  chat, GUI voice, and standalone headless voice all support the streaming path.
- **Scalable memory** — `render_memory_block` caps injected facts at
  `MEMORY_MAX_FACTS` (default 60) so the system prompt doesn't bloat as memory
  grows; older facts stay on disk.
- **Obsidian memory vault baseline** — durable JSON facts are mirrored into a
  human-readable managed Markdown profile. JARVIS locally searches only the
  comma-separated folders authorized by `OBSIDIAN_INDEX_FOLDERS`, retrieves a
  bounded set of relevant excerpts with source paths, and injects those instead
  of uploading the whole vault. A disconnected vault never prevents JSON facts
  from being saved.
- **Auditable memory corrections** — profile facts can be listed with stable
  IDs, filtered by category or text, and corrected in place. Corrections retain
  a bounded 20-entry revision trail in `profile.json`, update timestamps, and
  refresh the managed Obsidian mirror. Facts may also carry normalized semantic
  keys, confidence from 0–1, and a short provenance label. Reusing a key for a
  new value automatically treats it as a contradiction and revises the prior
  fact instead of adding an incompatible duplicate. Existing facts need no
  migration.
- **Governed evolving memory** — inferred facts stage as persistent reviewable
  proposals and require approval of an exact ID. Typed people/preferences/
  projects/routines/goals revise by stable key, legacy unkeyed contradictions
  can be surfaced without mutation, and confidence changes are explicit and
  timestamped. Optional local abstractive session summaries retain the bounded
  extractive digest as a failure fallback.
- **Media history and guarded operations** — Radarr/Sonarr expose recent
  history, missing items, and upcoming calendars. qBittorrent mutations require
  a freshly verified full hash and explicit confirmation.
- **Isolated session summaries** — every persisted turn refreshes a compact,
  dependency-free digest of that session's five most recent exchanges in
  SQLite. JARVIS can deliberately search recent or topic-relevant digests with
  stable session IDs when asked about prior work. Summaries are not injected
  automatically, preserving companion session isolation, and disappear with
  their session on delete/clear. Same-second session ordering is deterministic.
- **Optional local conversational routing** — when Claude is unavailable and
  `LOCAL_MODEL_ENABLED=true`, unknown conversational requests can use a bounded
  OpenAI-compatible `/chat/completions` endpoint with configurable model and
  timeout. Deterministic time/Home Assistant commands always run first, and the
  local model receives no tools, so it cannot claim or initiate world-changing
  actions. Invalid URLs, network failures, HTTP errors, and malformed responses
  fall through to the dependency-free keyword router. Local replies retain the
  current session's bounded conversational context and persist normally. A
  structured semantic classifier can recognize paraphrases for a strict
  read-only allowlist (time, HA connection test, device listings, and filtered
  live-state questions); only an exact JSON intent is accepted, then ordinary
  deterministic Python performs the read. Common state questions also work
  through the dependency-free keyword router. High-confidence semantic on/off
  requests may also target only lights, switches/plugs, or fans. Explicit
  0–100 percent brightness requests can target one uniquely resolved light.
  Named color requests use a fixed ten-color allowlist and require both the
  specific light name and requested color to appear in the user's request.
  Explicit thermostat setpoints are accepted only with a named target, unit,
  and conservative range (45–90°F or 7–32°C); JARVIS reads the thermostat's
  live unit and converts before sending the service call.
  The structured target must overlap the
  user's own words and then pass the existing unique Home Assistant entity
  matcher; ambiguity performs no action. Locks, doors, garages, covers, alarms,
  scenes, scripts, automations, playback, deletion, and arbitrary services are
  excluded. A configurable failure cooldown prevents a down endpoint from
  causing two timeouts per request.
- **Companion debug feedback** — the iOS app can explicitly submit a bounded
  conversation and "what went wrong" description to authenticated
  `/debug-reports`. Reports persist separately from personality memory in
  `memory/debug_reports.json`; JARVIS can list and inspect them as untrusted
  diagnostic evidence. API tokens and server addresses are never included.

## What's Not Done Yet (Roadmap)

### Performance debt (from the September 2026 efficiency audit)

Shipped from that audit: prompt caching, the Now Playing worker thread, and
once-per-path SQLite schema checks (see Recently Shipped). Still open, roughly
in value order:

- **UI panels poll and rebuild from scratch** — `RuntimePanel` (2s) calls
  `status_snapshot()`, which reads and parses `memory/profile.json` from disk
  every tick just to show a fact count; `MemoryPanel` (5s) re-reads the same
  file and destroys/recreates every QLabel even when nothing changed;
  `SessionsPanel` (30s) re-queries and rebuttons; `EventsPanel` likewise.
  Cheap fix: cache facts behind an mtime check and skip rebuilds when the
  data is unchanged. Right fix per ARCHITECTURE.md: panels subscribe to
  runtime change events instead of owning QTimers.
- **Per-turn memory disk reads** — `render_memory_block()` re-reads
  `profile.json` and re-tokenizes every fact each turn. Bounded and fine at
  60 facts; add an in-memory cache invalidated on write before the vault
  grows (the roadmap's indexed store supersedes this).
- **Per-turn SQLite housekeeping** — `append_turn()` still runs the summary
  refresh, an `updated_at` update, and the prune DELETE-with-subquery every
  turn on a fresh connection (connections are dropped, not `.close()`d —
  CPython refcounting saves us, but it's sloppy). Consider a shared
  connection and pruning every N turns.
- **Streaming is still opt-in** (`STREAMING_TTS=false`), so GUI chat waits
  for the full completion before showing anything. The streaming path exists
  in every mode; consider making it the default once trusted.
- **Companion turns reload the whole session** — `switch_session()` reads
  every stored message for the session from SQLite on each `/chat` turn and
  then trims to 40. Negligible today; switch to a `LIMIT` query (keeping the
  current message-count trim semantics) if sessions grow long.
- **No message-level cache breakpoint** — tools and persona are cached, but
  each extra round of a tool loop re-sends the full history uncached. A
  breakpoint on the last message would help multi-tool turns. This is a
  Claude-integration change, so it was flagged, not made (2026-09-22 audit).
- **96 tool schemas on every call** (~9k tokens, cached). Routing to a tool
  subset per intent would cut latency further but changes behaviour.
- **Cache-invalidation footnote** — persona `.env` changes (humor, address)
  and toggling `WEB_SEARCH_ENABLED` invalidate the cached prompt prefix for
  one full-price request each. Harmless, just worth knowing; keep the
  persona block byte-stable when editing `personality.py`.

### Feature roadmap

- **Evolving structured memory** — automatic extraction and habit learning
  are shipped; the remaining step is an indexed (embedding) store once
  keyword retrieval over the vault starts returning noise. Keep the live
  database on the Optiplex and synchronize or back up the vault to the NAS.
  Also worth doing: point `OBSIDIAN_VAULT_PATH` / `OBSIDIAN_INDEX_FOLDERS` at
  Nick's real vault so retrieval covers his own notes, not just JARVIS's.
- **Local model routing phase two** — extend the shipped tool-free
  conversational fallback and bounded home on/off/brightness/color/climate
  router with broader governed tool calling while retaining Claude for complex
  reasoning and planning.
- **Governed self-modification** — allow JARVIS to propose source/configuration
  changes through patches, tests, approval, activation, and automatic rollback;
  never permit unrestricted silent code rewriting.
  Configuration/service self-repair is shipped (`brain/self_repair.py`);
  source-level patches with tests and activation/rollback remain open.
- **Media operations phase two** — add Radarr/Sonarr manual search and retry
  operations; guarded torrent controls and read-only history/missing/upcoming
  views are shipped.
- **Email awareness** — read unread email summaries
- **Ambient music control** — detect silence and auto-dim volume
- **Double-clap wake** — alternative to voice wake word (from reference script)

---

## Running

```bash
# GUI (default, recommended daily driver)
python -m app.main

# Headless voice loop
python -m app.main --voice

# Text REPL (debug / no mic)
python -m app.main --text

# iOS companion API (requires JARVIS_API_TOKEN)
python -m app.main --server

# Create Desktop shortcut (run once)
python create_shortcut.py
```

---

## JARVIS TV

The TCL Google TV in Nick's room ("Nick's Room TV", G10, Android 12,
1920x1080 UI at density 320) runs `tv/launcher` as its HOME app. adb over
Wi-Fi on port 5555, Mac key authorized; its DHCP address drifts (10.0.0.44
until 2026-10, 10.0.0.46 since), so scripts take `TV=host:5555`. Find it
with a port-5555 scan of 10.0.0.0/24 if it moves; a router DHCP reservation
for the TV and the Optiplex would end the drift.

```bash
TV=10.0.0.46:5555 tv/launcher/deploy.sh   # build, install, re-apply TCL permissions, start
TV=10.0.0.46:5555 tv/provision.sh         # Nick, at home: scoped token + Core URL to the TV
```

- Build needs Android build-tools 34 / platform 31 under
  `/opt/homebrew/share/android-commandlinetools` and javac. Signing key and
  Plex token/PIN stay outside git in `~/tcl-tv-cleanup` (`TV_SECRETS`); the
  installed app only upgrades with that same key. d8 34 can't read the
  constructors javac 24 writes for inner (non-static) classes, so nested
  classes in the launcher are static.
- The token is `nicks-room-tv` with `events:read status:read` only; it can't
  chat or control anything. Until provisioned, the footer reads "NOT LINKED
  TO CORE".
- TCL quirks: reinstalling resets `appops APP_AUTO_START` (deploy.sh re-applies
  it); the firmware restores some removed apps every boot (BootCleanup removes
  them again through self-adb). The launcher's own adb key is approved on the
  TV; self-adb is what runs the nightly reboot.
- Sound fix test hooks (DUMP-guarded, adb only):
  `adb shell am broadcast -n com.tvhome/.NightlyRefresh --el in_ms 0 [--ez dry_run true]`
  runs a refresh now (skips the uptime gate, never the screen-off check);
  `in_ms > 0` arms a one-off alarm instead. Outcomes log under
  `TvHomeRefresh` (the TV's log buffer only holds minutes).
- `--ez clear true` to `.ProvisionReceiver` unlinks the TV from Core.
- Verified 2026-10-27 against a mock Core on the Mac: glance renders, the
  SSE banner shows over Plex without stealing focus, offline footer reads
  "CORE UNREACHABLE · LAST SEEN ...". Not yet verified against the real Core
  (needs `tv/provision.sh` and the Optiplex pulling `/api/glance`).

## Nick's Preferences & Context

- Goes by "sir" (default address, Nick can change it mid-conversation)
- Based in Herriman, Utah
- Uses Cursor (VS Code fork) as his IDE
- Has a working Govee smart lighting setup through Home Assistant
- Spotify Premium user
- Wants JARVIS to be proactive — update settings, remember things, and evolve itself without being asked
- Movie-accurate JARVIS aesthetic: cyan (#00d9ff) holographic HUD on near-black,
  amber when thinking, hot-red reserved for alerts. Corner brackets (HUDFrame),
  scanlines, mono caps. (Overhauled June 2026 — supersedes the earlier red theme.)
- Response style: terse by default — one or two sentences, lead with the
  answer, no filler / hedging / preamble / sign-offs, never restate the
  request back. Dry, confident, never apologetic. (Persona rules live in
  `brain/personality.py`; tightened August 2026.)

### NAS recovery operations

`ops/nas/` contains the reviewed UGOS recovery scripts and installer. The single
recovery chain takes the existing watchdog flock, explicitly removes gluetun
before recreating it, waits 20 seconds, explicitly removes the five VPN
dependents before recreating them, then waits 130 seconds for Byparr and
verifies gluetun health plus positive qBit DHT. The dependents are rm -f'd
first (not just force-recreated) because a dead container left behind by a
prior failed/aborted recovery keeps its name and Compose refuses to recreate
it until it's gone — hit live during a manual recovery on 2026-09-20. Every
failed step aborts and records a failed result. DHT's three checks/30-minute
cooldown and indexer's two strikes/one-hour cooldown are preserved. No Docker
socket or root SSH is exposed to Jarvis. See `PROGRESS.md` for deployment state;
a live restart always requires separate confirmation.

NAS SSH setup uses dedicated `jarvis_nas_recovery` and `jarvis_nas_status` keys
on the Optiplex plus a pinned known-hosts file. The status key reads a fixed
JSON summary and bounded watchdog tails without sudo; it cannot restart. The
recovery key validates request IDs, detaches from UGOS SSH timeouts, and uses
one narrow sudo launcher. See `ops/nas/README.md` for the preview/install and
hand-test commands. Installation hardens previously writable path/SSH ACLs.

### Arr stack monitor, alerts, and confirmed restart

- `brain/arr_stack.py`: durable alert cooldowns and session-bound recovery
  proposals; `integrations/nas_stack.py`: plain HTTP checks, restricted SSH,
  HA mobile delivery, and Healthchecks pings.
- `arr_stack_status` reports API reachability, qBit DHT/connection, health
  warnings, and recent NAS watchdog actions. Polling never calls a model.
- Asking directly ("restart the arr stack", "can you restart arr stack
  please") first checks qBittorrent via `qbittorrent.active_downloads()`
  (incomplete torrents in downloading/metaDL/forcedDL/allocating state or with
  download speed > 0; stalled, queued and paused ones don't count). Zero
  active: it restarts immediately ("Nothing downloading, restarting now").
  Otherwise, or if qBittorrent can't be checked, it opens a session-bound
  request with a 4-digit code: a bare "yes"/"confirm"/"go ahead" works only
  as Nick's very next message (any other message closes that window), and
  "confirm <code>" (or the bare code, or the old long phrase with the code)
  works for ten minutes. A bare "no"/"cancel" as the next message, or
  `cancel arr restart` any time, cancels. The request is consumed once, before
  SSH. `restart_arr_stack` (the model's tool) never restarts by itself: it
  only opens the same request. The direct request also works in offline
  text/voice.
- Recovery returns promptly on SSH and continues detached on the NAS. The
  turn replies at once; a background follow-up polls the NAS result (if the
  status key works) and live service probes for up to 12 minutes, then pushes
  a per-service up / still starting / failed report. A lost result never
  auto-retries. A typo'd phrase, wrong code, old 8-character token, or a late
  bare "confirm" re-prompts with the same code and echoes what was sent.
- The runtime confirms a condition after 3 consecutive failing polls (10 for
  zero DHT), recovers after 2 clean ones, and tracks each Prowlarr indexer
  separately. `brain/arr_incidents.py` then decides what reaches the phone:
  a condition pushes only after 4 minutes down (zero DHT/qBit disconnected
  wait 25 minutes for the NAS watchdog); when the NAS watchdog acts, all arr
  pushes are held for 20 minutes and replaced by one "Recovered after
  watchdog: ..., took N min" summary or one "still failing" alert (indexer
  backoff left after a recreate is expected and never counts as failing); one
  incident = one APNs thread/collapse id, so updates replace each other; an
  indexer backed off for 6h moves to one passive daily digest (09:00) that
  suggests re-saving it in the Prowlarr UI. A NAS unreachable for 10 minutes
  breaks through the hold. See ARCHITECTURE.md "Incident push policy". Phone delivery goes through
  `brain/notifications.py` (companion push stubbed; HA fallback is primary). HA
  `notify.mobile_app_nicks_iphone_17_pro` was discovered as Nick's existing
  phone channel; the inbox/voice path also receives each alert. HA unavailability
  is reported locally. This channel depends on HA on the NAS, so an entire NAS
  outage may prevent phone delivery; the external dead-man check separately
  covers Jarvis process/arr-monitor liveness.
- A separate status SSH key reports recent watchdog actions/log tails and
  never runs recovery. Missing status is reported as unknown.
- Environment names/defaults are in `.env.example` and ARCHITECTURE.md's
  "Arr monitoring and dead-man heartbeat" section. Startup is opt-in; leave
  restart disabled until the key restrictions pass hand tests. Never put NAS
  Docker commands into `SELF_REPAIR_COMMANDS` or grant Docker/root SSH access.
- Healthchecks needs a user-supplied secret ping URL and an account notification
  integration. For five-minute pings configure a five-minute period and at
  least five-minute grace. Its success API is documented at
  <https://healthchecks.io/docs/http_api/>. No URL is embedded in source or logs.

See `PROGRESS.md` for what is actually installed/activated and the known live
NAS outage; implemented functionality must not be mistaken for completed live
recovery verification.


The companion server starts configured arr monitoring/heartbeats during server
startup, without waiting for a phone connection. Runtime creation is serialized
to prevent duplicate monitors, and server shutdown stops them.
