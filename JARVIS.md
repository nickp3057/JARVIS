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
│   └── server.py            Authenticated HTTP bridge for the iOS companion
│
├── brain/
│   ├── agent.py             JarvisAgent: session management, tool loop, history
│   ├── apps.py              Windows app launcher (open/close/focus via PowerShell)
│   ├── automation_workshop.py Staged HA automation drafts, diffs, apply + rollback
│   ├── clipboard_tool.py    Read/write clipboard via PowerShell Get/Set-Clipboard
│   ├── debug_reports.py     Durable user-submitted companion failure reports
│   ├── history.py           SQLite conversation history with session support
│   ├── intents.py           Keyword intent router (fallback if brain offline)
│   ├── memory.py            JSON long-term memory, correction history, inspection
│   ├── knowledge_vault.py   Authorized Obsidian search + managed profile mirror
│   ├── local_model.py       Optional OpenAI-compatible conversational fallback
│   ├── briefing.py          Spoken situational briefing (time, weather, agenda, timers)
│   ├── personality.py       System prompt builder (humor%, address, tool descriptions)
│   ├── reminders.py         Persistent reminders & to-dos (timed + open list)
│   ├── router.py            Top-level request dispatcher
│   ├── runtime.py           JarvisRuntime — wraps agent + status snapshot
│   ├── settings.py          Whitelist-based .env editor (update_setting tool)
│   ├── timers.py            Timer store: set/cancel/pause/resume/dismiss/alarming state
│   ├── tools.py             All tool schemas + dispatch (35+ tools)
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
│   └── profile.json         Long-term memory facts (auto-created)
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

## Tools (35+)

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
- `update_setting(key, value)` — live-edits `.env` (HUMOR_PERCENT, ADDRESS, ELEVENLABS_SPEED, etc.)
- `get_setting(key)` / `list_settings`
- `web_search` — Anthropic server tool (togglable via WEB_SEARCH_ENABLED)

---

## Key Design Patterns

**Session-based history**: Every launch creates a new session UUID. `append_turn(user, reply, session_id=...)` persists turns to SQLite. The Sessions panel shows all past sessions as clickable cards — clicking one loads that session into the agent context and repopulates the transcript.

**Non-blocking input**: Input is re-enabled as soon as the agent replies, before TTS completes. Mute button skips TTS entirely and immediately emits `speak_finished` so the orb resets.

**Live settings**: `.env` is written directly by `update_setting`. All settings are read fresh at call time (`os.getenv()` inside functions, not at import time). JARVIS can change its own behavior mid-conversation.

**RMS envelope**: `tts.py` computes a per-frame RMS envelope from the ElevenLabs WAV before playback. `voice_runner.py` drives the orb's `rms_level` using a monotonic clock thread so audio and visual stay in sync.

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
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
WEB_SEARCH_ENABLED=true

# Voice tuning
WAKE_THRESHOLD=0.5
PREROLL_SECONDS=4.0
FOLLOWUP_TIMEOUT_S=10.0
FOLLOWUP_TASK_S=2.5
INTERRUPT_RMS_THRESHOLD=1500
WHISPER_MODEL=small.en

# Feature flags / tuning (all optional, sensible defaults)
BRIEFING_ON_LAUNCH=false    # speak a briefing at startup
STREAMING_TTS=false         # opt-in sentence-streamed TTS in every interactive mode
MEMORY_MAX_FACTS=60         # cap facts injected into the prompt (0 = no cap)
MEMORY_RELEVANT_FACTS=12    # query-matched/recent profile facts per turn
OBSIDIAN_VAULT_PATH=        # optional vault root; defaults to memory/obsidian
OBSIDIAN_INDEX_FOLDERS=Jarvis Memory  # comma-separated folders JARVIS may read
OBSIDIAN_MAX_RESULTS=4      # maximum note excerpts retrieved per turn
OBSIDIAN_MAX_CHARS=4000     # hard cap on retrieved note text per turn
OBSIDIAN_MAX_FILE_BYTES=1000000 # skip unusually large Markdown files
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
HOME_DASHBOARD_ENTITIES=    # comma-separated HA entity_ids for /api/home/entities

# Google Calendar (optional, read-only)
GOOGLE_CREDENTIALS_FILE=    # path to OAuth client-secret JSON (Desktop app)
GOOGLE_TOKEN_FILE=          # token cache; defaults to memory/google_token.json
GOOGLE_CALENDAR_ID=primary
CALENDAR_MONITOR_ENABLED=true
CALENDAR_MONITOR_INTERVAL_S=120
CALENDAR_ALERT_LEAD_MIN=10  # warn this many minutes before a meeting
```

## Google Calendar setup (one time)

1. Google Cloud Console -> create a project -> enable the Google Calendar API.
2. Create OAuth credentials of type **Desktop app**; download the JSON.
3. Put its path in `GOOGLE_CREDENTIALS_FILE` in `.env`.
4. Launch JARVIS and ask "what's on my calendar" — a browser opens once for
   consent, then the token is cached to `memory/google_token.json` and refreshes
   silently after that. Access is read-only.

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

- **iOS Hub screen** (`ios/JarvisCompanion`) — a tab bar now pairs Chat with a
  Hub dashboard rendering the seven server endpoints: per-service up/latency
  rows, live torrent transfers with progress/speed/ETA and per-torrent
  pause/resume (targeting the `hash` now included in `/api/status/torrents`;
  the buttons hide gracefully against an older Core), Radarr/Sonarr
  queues with collapsible recent additions, allowlisted quick-action toggles
  (optimistic, reconciled from the POST response, reverted with an alert on
  failure), and browser links. Pull-to-refresh everywhere; torrents and
  services auto-poll every 5s (matching the server TTL) only while the Hub is
  visible and foregrounded. Every section loads and fails independently — a
  dead service shows an inline error while the rest render. Settings now
  fills in port 8000 for plain-http addresses typed without a port and shows
  the effective URL. All Hub networking reuses `JarvisAPIClient`.
- **Companion quick actions** (`app/status_hub.py`, `app/server.py`) — two
  bearer-auth'd write endpoints for the iOS hub. `POST /api/home/control`
  turns an allowlisted entity on or off (entity must be in
  `HOME_DASHBOARD_ENTITIES` — the allowlist is the security boundary, not a
  display filter; non-allowlisted entities and unsupported actions are 400s)
  and returns the resulting state; it delegates to the guarded
  `call_discovered_service` path. `POST /api/torrents/control` pauses or
  resumes one torrent via `manage_torrent`, preserving its exact-hash and
  explicit-confirmation gates; `delete_files` is never passed. Successful
  actions invalidate the matching status cache; refused or failed service
  calls return 200 with an `error` field.
- **Companion dashboard endpoints** (`app/status_hub.py`, `app/server.py`) —
  five bearer-auth'd, read-only JSON endpoints so the iOS app can render a
  status hub without ever talking to the NAS services directly:
  `/api/status/torrents` (qBittorrent transfers + global rates),
  `/api/status/media` (Radarr/Sonarr queues + 5 most recent library
  additions each), `/api/status/services` (reachability + latency for Home
  Assistant, Plex, Sonarr, Radarr, qBittorrent), `/api/links` (web-UI links
  derived from the same `.env` base URLs the integrations read; unset
  services are omitted), and `/api/home/entities` (state of the
  `HOME_DASHBOARD_ENTITIES` allowlist — read-only, no control). A short
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
- **Runtime lock held across the whole Claude turn** —
  `JarvisRuntime.respond()` holds `_lock` for the entire multi-second API
  call, so monitor `record_event()` calls and task/event UI reads block until
  the turn ends. Locking is also inconsistent: `respond_streaming()` takes no
  lock while `respond_streaming_in_session()` holds it for the entire stream
  (serializing the runtime during companion streams). The agent already has
  its own conversation lock; the runtime lock should cover only session
  switching and shared-state mutation.
- **`ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` read at import time** in
  `brain/agent.py` — breaks the repo's "settings are read live" rule, so
  changing the model via `update_setting` silently does nothing until
  restart. Read them inside the request path (or at least re-read the model
  per call).
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
- **Cache-invalidation footnote** — persona `.env` changes (humor, address)
  and toggling `WEB_SEARCH_ENABLED` invalidate the cached prompt prefix for
  one full-price request each. Harmless, just worth knowing; keep the
  persona block byte-stable when editing `personality.py`.

### Feature roadmap

- **Evolving structured memory** — expand the shipped typed/proposal/
  contradiction/confidence/summary baseline into an indexed store
  suitable for very large vaults. Keep the live database on the Optiplex and
  synchronize or back up the vault to the NAS.
- **Local model routing phase two** — extend the shipped tool-free
  conversational fallback and bounded home on/off/brightness/color/climate
  router with broader governed tool calling while retaining Claude for complex
  reasoning and planning.
- **Governed self-modification** — allow JARVIS to propose source/configuration
  changes through patches, tests, approval, activation, and automatic rollback;
  never permit unrestricted silent code rewriting.
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
