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
| TTS | ElevenLabs Flash v2.5 (Piper as fallback); `TTS_PROVIDER=piper` makes a local Piper voice primary |
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
│   ├── companion_voice.py   Live transcript WebSocket, release-to-commit, streamed text/PCM
│   ├── client_tokens.py     Scoped per-client tokens (events:read, status:read, chat, settings:write, wake:write) + CLI (create/grant/list/revoke); hashes in memory/client_tokens.json
│   └── server.py            Authenticated HTTP bridge for the iOS companion (+ /api/clients/events SSE)
│
├── mac/JarvisMac/           JARVIS for Mac: native SwiftUI client (Chat, Home, Alerts, Donna); SwiftPM, build.sh installs ~/Applications/JARVIS.app
│
├── brain/
│   ├── agent.py             JarvisAgent: session management, tool loop, history
│   ├── apps.py              Windows app launcher (open/close/focus via PowerShell)
│   ├── automation_workshop.py Staged HA automation drafts, diffs, apply + rollback
│   ├── clipboard_tool.py    Read/write clipboard via PowerShell Get/Set-Clipboard
│   ├── alert_checks.py      Needs attention Refresh: re-checks each alert live, hides the fixed ones
│   ├── debug_reports.py     Durable user-submitted companion failure reports
│   ├── incident_reports.py  Tap-to-chat incident report: live health, NAS watchdog logs, app log warnings, quoted indexer HTTP/Cloudflare errors, recent changes, rule-based suspected cause
│   ├── history.py           SQLite conversation history with session support
│   ├── intents.py           Keyword intent router (fallback if brain offline)
│   ├── memory.py            JSON long-term memory, correction history, inspection
│   ├── memory_extractor.py  Background post-turn learning: auto-saves facts, journals
│   ├── memory_proposals.py  Reviewable staging for low-confidence inferred facts
│   ├── habits.py            Usage-pattern log + derived "what you've noticed" digest
│   ├── knowledge_vault.py   Authorized Obsidian search, profile mirror, capture + journal
│   ├── local_model.py       Optional OpenAI-compatible conversational fallback
│   ├── maintenance.py       Recurring chores: countdowns + completion log (SQLite)
│   ├── shopping.py          Shopping list over a saved-item catalog with photos (SQLite)
│   ├── donna.py             Donna, the private text-only counsellor (own DB, prompt, tools)
│   ├── usage.py             Ledger of every model call's tokens + estimated $ per agent
│   ├── briefing.py          Spoken situational briefing (time, weather, agenda, timers)
│   ├── personality.py       System prompt builder (humor%, address, tool descriptions)
│   ├── price_watch.py       Guarded public-page price checks + durable targets
│   ├── subagents.py         Background subagents: focused tool loop + one-at-a-time jobs in the task lane
│   ├── email_triage.py      Email subagent: trash noise, file missionary mail, keep the rest, unsubscribe list
│   ├── watches.py           "Let me know when X happens": durable web watches checked by the watcher subagent
│   ├── investing.py         Investing subagents: target weights, daily advisor, crash watch, paper playground
│   ├── trading.py           Real Schwab orders: sizes them, enforces caps/cash-only/stocks+ETFs, waits for Nick's Approve
│   ├── agent_roster.py      Who works for JARVIS: the agents behind GET /api/agents and the companion's Agents map
│   ├── reminders.py         Persistent reminders & to-dos (timed + open list)
│   ├── router.py            Top-level request dispatcher
│   ├── runtime.py           JarvisRuntime — wraps agent + status snapshot
│   ├── settings.py          Whitelist-based .env editor (update_setting tool)
│   ├── standing_orders.py   Durable behavioural directives from Nick, injected every turn
│   ├── lessons.py           JARVIS's lessons from his own mistakes (memory/lessons.json): decay, reinforce, cap, per-turn block
│   ├── reflector.py         Reflection after failed turns: cheap heuristics, then one Haiku call writes a lesson
│   ├── tool_health.py       Self-diagnosis ledger: per-tool failures, degraded flags, prompt warning
│   ├── notifications.py     Phone alert router: companion push (APNs, stubbed) then HA notify fallback
│   ├── event_bus.py         Single alert bus: publish once; phone (APNs→HA) and SSE client streams subscribe; also answer cards (`publish_card`)
│   ├── answer_cards.py      Answer screens: `show_screen` builds a typed card (weather_today, calendar_day, timers), publishes it, returns the spoken facts
│   ├── arr_stack.py         Arr alert hysteresis/per-indexer tracking, restart confirmation gate, async recovery follow-up
│   ├── confirm_gate.py      `act()` runs what Nick asked for at once (a guest voice only gets a proposal); `propose()` is the session-bound "yes"/"approve <code>" gate for fixes JARVIS suggests itself; several proposals at once, "yes" approves all staged that turn, one message can carry several codes
│   ├── arr_incidents.py     Arr push policy: watchdog-recovery hold + summary, minimum downtime, grouped incidents, daily indexer digest
│   ├── stack_fixes.py       Tier-2 allowlist of NAS-stack fixes (HA restart, indexer re-test, indexer mirror switch, Plex rescan, arr recovery, download retry), each with a post-fix check
│   ├── self_repair.py       Governed self-repair: diagnose a subsystem, stage a bounded fix, apply on approval
│   ├── study_brief.py       Daily quiz/exam countdown: settings, planning, JARVIS-composed text
│   ├── timers.py            Timer store: set/cancel/pause/resume/dismiss/alarming state
│   ├── tools.py             All tool schemas + dispatch (45+ tools)
│   ├── vision.py            Screenshot via PyQt6 + Claude vision API call
│   └── weather.py           Open-Meteo weather lookup (+ `get_weather_today`: hourly temps/rain chance, sunrise/sunset, for the weather screen)
│
├── integrations/
│   ├── home_assistant.py    HA REST API: control_device / list_entities
│   ├── google_calendar.py   Read-only Google Calendar: agenda / next event / alerts
│   ├── gmail.py             Gmail (gmail.modify): list, label/file, trash/untrash; own token, --auth CLI
│   ├── qbittorrent.py       Live torrent progress, speed, ETA + completion monitor
│   ├── servarr_common.py    Shared Radarr/Sonarr HTTP client + quality/root-folder
│   │                        resolver + NL quality-phrase fuzzy matching
│   ├── radarr.py            find_movie (lookup) / add_movie (confirm-gated)
│   ├── temp_4k.py           temporary 4K copies via a second Radarr (radarr4k), auto-removed
│   ├── sonarr.py            find_series (lookup) / add_series (confirm-gated)
│   ├── nas_host.py          NAS host-health snapshot (containers, RAID, SMART, volumes, temps): debounced alerts + incident-report lines
│   ├── media_doctor.py      Traces one title request→arr→indexer→qBit→import→Plex; names the stalled stage; fixes via confirm_gate
│   ├── plex.py              Minimal Plex client (title search + library rescan) for the media doctor
│   ├── schwab.py            Charles Schwab: OAuth (7-day login), holdings every 30 s, live prices over the streamer, place_order (only brain/trading.py calls it)
│   └── spotify.py           Spotipy wrapper: play/pause/skip/volume/transfer/now-playing
│
├── speech/
│   ├── audio.py             MicStream rolling buffer, record_until_silence, rms()
│   ├── chime.py             Activation chime playback
│   ├── stt.py               faster-whisper Transcriber
│   ├── tts.py               ElevenLabs TTS with RMS envelope computation; `python -m speech.tts --say "..."` to test a voice by ear
│   ├── piper_voice.py       Local Piper voice (TTS_PROVIDER=piper): model loaded once in-process, PIPER_* knobs, piper-tts 1.2/1.3 APIs
│   ├── remote_voice.py      Per-request PCM for the phone's voice (ElevenLabs or Piper, 22.05 kHz)
│   ├── cloud_http.py        one kept-alive HTTPS session for the speech clouds (STT + TTS), warmed by the wake word
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
│   ├── shopping.db          Saved shopping items; on_list = on the list now
│   ├── donna.db             Donna's sessions, notes, case file, follow-ups, moods, check-ins (private)
│   ├── usage.db             One row per model call: agent, model, token counts, estimated $
│   ├── shopping_photos/     <item id>.jpg product photos from the companion
│   ├── profile.json         Long-term memory facts (auto-created)
│   ├── habits.json          Bounded per-turn usage log for habit learning
│   ├── memory_proposals.json Low-confidence facts awaiting approval
│   └── obsidian/            Default vault: Jarvis Memory/{Profile.md,Journal/,Notes/}
│
├── tv/                    JARVIS TV: the TCL Google TV's home screen (see "JARVIS TV")
│   ├── launcher/            Hand-built Android app com.tvhome (no Gradle): build.sh, deploy.sh
│   │   └── src/com/tvhome/  HomeActivity (glance + Plex/Settings), ReactorView (phone's mark), Jarvis (Core client), AlertStream, AnswerCard (answer screens)
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
- `show_screen(card, location?)` — answer screens: puts a readout on the iPad
  Room tab, the phone and the room TV and returns the same facts to speak.
  `card` is `weather_today` (now, hourly temps and rain chance for the rest of
  the day, high/low, sunrise/sunset, wind), `calendar_day` (the rest of
  today's events) or `timers` (running timers, counting down). The model uses
  it instead of get_weather / todays_agenda / list_timers when Nick asks for
  one of those directly; incidental lookups stay text-only. See "Answer
  screens" below.
- `morning_briefing` — spoken greeting + time + weather + running timers

### Home Assistant
- `control_device(command)` — fuzzy natural-language on/off device control;
  explicit off phrases take precedence over location wording such as "on my desk"
- `activate_home_action(kind, name)` — fuzzy-match and activate an existing HA
  scene, script, or automation
- `list_scenes()` — Nick's saved HA scenes and which one the room is in
  ("what scenes do I have?")
- `save_scene(name)` — capture the room's allowlisted lights, plugs and fan
  (`HOME_DASHBOARD_ENTITIES`) and save them as an HA scene via
  `/api/config/scene/config/<slug>`; saves at once (`confirm_gate.act`; a guest voice only proposes),
  replaces a scene of the same name. Never includes `switch.wii_u`; ring lights
  (`RING_LIGHTS`, the fan light) are always saved off; fans get HA 2026.10's
  required `direction`/`preset_mode`. Govee DIY rainbows can't be read back,
  so a rainbow light is saved at its plain colour
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
- A due reminder is spoken in the room as before and also goes on the alert
  bus (`runtime.raise_alert`, kind `reminder`, severity info, push): it buzzes
  the phone and shows on the TV, without joining Needs attention. One inbox
  line, spoken once (only the room voice / voice loop / GUI that popped it
  speaks). Timers stay in the room.

### Barn Door Protocol *(new)*
- `barn_door_protocol(action?)` — "Barn Door Protocol" / "lockdown": HA
  snapshots the room (`scene.create` → `scene.barn_door_before`), every
  colour light in `BARN_DOOR_LIGHTS` (strips, desk, the fan's LEDs) goes full
  red, `BARN_DOOR_OFF` (the bedroom light, the Avengers tower) goes dark, the shared theme goes
  to the hidden red `barnDoor` palette (`brain/theme.py` remembers the old
  one) and the TV's whole screen becomes the red-alert view (no alert
  banner). `stand_down` restores the snapshot and
  the theme (`brain/protocols.py`), then re-applies each light's effect
  (scene snapshots skip animated modes): the effect HA reported at engage
  time, else `BARN_DOOR_EFFECTS`. Govee app modes (Nick's rainbow) report
  no effect in HA, and the fan's rotating rainbow isn't exposed at all.
  Govee DIY scenes made in the Govee app ("New Rainbow", "Xmas", "Real
  Rainbow") *are* in HA, as `select.<light>_diy_scene` dropdowns from the
  `govee` integration: a `BARN_DOOR_EFFECTS` entry keyed by that select is
  restored with `select.select_option`, and the pick showing at engage wins.

### Voice ID *(new)*
- `voice_id(action)` — Nick's voiceprint for the room mic (`brain/speaker_id.py`):
  `status`, `enroll` (the next `VOICE_ID_SAMPLES` room sentences become
  samples, counted aloud, no wake word between them), `cancel`, `reset`.
  Locked itself, so only Nick (or the phone app) can re-enrol.

### Nick's room TV *(new)*
- `kill_camera()` — lock the room camera (the iPad lens Nick watches from
  his phone) off for five minutes, from anywhere; no tool turns it on.
- `tv_power(state)` — on/off through Home Assistant's Android TV Remote
  entity (`TV_REMOTE_ENTITY`, default `remote.nick_s_room_tv`), which can
  wake the TV from standby (`integrations/tv_remote.py`).
- `play_on_tv(title)` — play a Plex show or movie on the room TV where Nick
  left off: `plex.next_up` picks the show's On Deck episode (else its first
  unwatched one) and `tv_remote.play_plex` sends a `plex://play/?metadataKey=`
  deep link as the remote's turn_on activity; Plex resumes the position.
- `home_assistant.call_service` sends `media_player` turn_on/off for any of
  `TV_MEDIA_PLAYERS` (the TV's Cast/group entries) to that remote instead:
  "turning on" a Cast entity opened Home Assistant's cast screen on the TV.
- Companion remote (top of the Hub tab, `TVRemoteView`): power, D-pad +
  OK, back, home, play/pause, volume, mute → `POST /api/tv/remote {key}`
  (admin) → `remote.send_command` (DPAD_*, BACK, HOME, MEDIA_*, VOLUME_*).

### Charles Schwab
- `portfolio_summary` — live value, today's and yesterday's change, overall
  gain/loss (`total_gain`/`total_gain_pct`: unrealized, live value of what's
  held vs. Schwab's average price paid), cash, biggest movers (`integrations/schwab.py`).
  Real orders go through `place_trade` / `decide_investment` and always wait
  for Nick's Approve (Trading, below).
- `integrations/schwab.py` also keeps each account apart (`accounts_by_number()`,
  by last four digits) and looks up any symbol (`quotes`, `price_history`) for
  the investing agents.
- JARVIS's own Schwab developer app (not the one for Nick's coworker), with
  "Accounts and Trading Production" + "Market Data Production" so trading can
  come later without a new app. Its registered callback is
  `https://127.0.0.1:8182` (changing it on Schwab's site means days of
  re-approval, so Core follows the app, not the other way round).
- **Borrowed login (2026-11-08, the intended setup):** Schwab keeps one live
  login per Schwab user, so JARVIS and Nick's Coopa Trada bot revoked each
  other (Coopa login 11-04 1:47 PM MT → JARVIS refresh refused 1:59 PM). The
  **Schwab hub** (schwab.nickolaspearson.com, `~/Developer/projects/schwab-hub`,
  its own service on the Lightsail box) holds Nick's one login. With
  `SCHWAB_HUB_URL` + `SCHWAB_HUB_SECRET` set, Core keeps no Schwab login:
  `integrations/schwab.py` borrows a 30-min access token from
  `GET https://schwab.nickolaspearson.com/api/token` (only the access token
  crosses, never the refresh token or app secret), re-asking when it has under
  a minute left, or each minute while the hub is logged out. Live streaming is
  unchanged (the streamer logs in with the borrowed token; reconnects get a
  fresh one). Log in = the hub's page (`SCHWAB_LOGIN_PAGE`, default the hub's
  `/`): the app's Log in buttons open it in Safari (`/api/schwab/login`
  returns `external: true`), the alert and the spoken answer point there, and
  nickolaspearson.com's home page links it. The SCHWAB_APP_* login below is
  the fallback when the hub variables are empty. JARVIS sees the accounts Nick
  ticks on Schwab's consent screen when logging in on the hub.
- Login: the companion's Settings > Charles Schwab > Log in (or the Home
  card) gets `POST /api/schwab/login` (admin; returns the page and the
  callback) and opens Schwab in an in-app web view (`SchwabLoginView`). When
  Schwab redirects to the callback the web view cancels it and posts the
  address to `POST /api/schwab/finish` (admin); Core checks the one-time
  `state` (10 min) and trades the code → tokens in
  `memory/schwab_token.json` (0600, gitignored). `/api/schwab/callback`
  (no bearer, same `state` check) is there if the app is ever registered with
  Core's Tailscale HTTPS name instead. Access tokens
  refresh every 30 min; Schwab ends the login after 7 days regardless, so the
  monitor raises `schwab_login` a day before and when it lapses. If Schwab
  refuses the refresh token early ("invalid, expired or revoked"; seen
  2026-11-03 an hour after a fresh login), the token file is marked
  `revoked` and counted as run out, so the apps show Log in and the alert
  says "Schwab ended the login early" instead of retrying every 30 s. Nick chose
  this over JARVIS logging in himself (2026-10-27): no Schwab password or 2FA
  on Core.
- Live: holdings/cash from `/trader/v1/accounts?fields=positions` every
  `SCHWAB_POLL_SECONDS` (30) in market hours (4am-8pm ET weekdays, else 10
  min); stock/ETF prices from the streamer (LEVELONE_EQUITIES fields 0,3)
  as they tick (today = Schwab's own day gain/loss + the price move since the
  last fetch, so the weekly $25 deposit and its auto-buys are never "gain"); yesterday from daily candles once a day. `GET /api/portfolio`
  (status:read) serves the in-memory snapshot; `/api/glance` has a
  `portfolio` summary. Phone Home card polls it every 5 s; the TV's line
  under the weather every 5 s while home is showing.

### Wake-up music *(new)*
- `set_wake_up(time, days?, music?, label?, volume?)` — puts a time on the
  weekly schedule for those days (other days keep their own times), or sets
  the one-off ("in 3 minutes", days once/tomorrow)
  (`brain/wake_alarm.py`, `memory/wake_alarm.json`, old one-alarm files
  migrate on read). At the time the TV in Nick's room turns on and plays
  Spotify at `volume` (default 30) straight away, no fade, with I'm up /
  Snooze 9 min on screen. `music` is a Spotify link/URI, a name (stored as
  `search:<words>`, Spotify plays the top hit), `resume`, or omitted to keep
  the current music.
- One day only: `set_wake_up` with days `today`/`tomorrow`/`YYYY-MM-DD`
  ("8:30 tomorrow") and `skip_wake_up(day?, restore?)` ("skip my alarm
  tomorrow") write an override for that date (`overrides`, "" = skipped);
  it wins even while the schedule is paused, drops off once the date has
  passed, and the weekday is back to normal the week after.
- `get_wake_up` / `cancel_wake_up(days?)` (some weekdays for good, `once`, or everything)
- Companion editor: `GET`/`PUT /api/wake` (admin token) with `times` (7
  entries, Monday first, `HH:MM` or null), `music`, `label`, `volume`,
  `enabled` (pauses the schedule without losing it); the one-off and the
  one-day `overrides` are shown, not edited.
- Runtime wake monitor (every 20 s, fires each alarm once within 10 minutes of
  its time) publishes a `wake_up` bus event with `push=False`; the TV acts on it.
- The event can't reach a TV that dropped off Wi-Fi in deep standby (2026-10-31:
  the stream went quiet at 06:26 and every TV entity in HA read `unavailable`
  until noon), so Core also powers the TV on through HA (`tv_remote.power`).
  If `remote.nick_s_room_tv` still isn't `on` `WAKE_BACKUP_DELAY_S` (90) later
  and nobody pressed I'm up / Snooze, the **backup ring** starts: the room voice
  beeps the Optiplex speaker (at the day volume) until I'm up, Snooze, "stop" /
  "I'm up" / "Hey JARVIS" by voice, or `WAKE_BACKUP_RING_S` (600) runs out.
  Voice dismissal counts as I'm up (lights, TV wake screen closes:
  `wake_alarm.im_up`). The TV coming on, it plays the music and the room stays quiet.
- **The phone (2026-11-01, iOS 5.52):** the wake-up push is time-sensitive with
  `relevance-score` 1 (`notifications.URGENT_CATEGORIES`; breaks through Focus,
  top of Notification Center; the app's entitlement is pending, see
  FUTURE_SESSIONS.md) and keeps its I'm up / Snooze buttons. While an alarm is
  unanswered or snoozed (`wake_alarm.ringing_state()`, in `GET /api/wake` and
  glance `wake.ringing`: `state` ringing/snoozed, `since`, `detail`, `until`)
  the phone and iPad open the full-screen alarm whenever the app is open and
  the state is `ringing` (RootView polls every 15 s, and the push landing in
  the foreground opens it at once); answered anywhere, I'm up or Snooze, it
  closes (iOS 5.66: a snooze used to keep it up on the iPad). Close hides it
  for that alarm only. The iPad's night screen stays down while the alarm
  screen is up and is dropped before it opens (5.66: the two full-screen
  covers used to take turns knocking each other off).
  Ringing state is in memory and lapses after an hour (`RINGING_WINDOW_S`).
- **I'm up stops the music (Core, TV 9.0):** the TV pauses Spotify on
  `wake_dismiss` and on its own I'm up (only within an hour of an alarm or
  while snoozed), and Core also sends `MEDIA_PAUSE` through the HA remote in
  case the TV's event stream is down (2026-11-01: the phone's I'm up closed the
  TV's wake screen but the music played on).

### Price Watching
- `watch_price(url, label?, target_price?)` — monitor structured price metadata
  on an exact public product URL; private/local destinations are rejected
- `list_price_watches` / `check_prices` / `remove_price_watch(id)`

### "Let me know when" watches (background subagent) *(new 2026-11-14)*
"Tell me when the AP rankings are out", "let me know when the PS5 restock
happens", "tell me when Michael 2 gets a release date". A watch
(`brain/watches.py`, stored in `memory/watches.json`) is a search query, a
"done when" condition, a cadence and an expiry. The runtime's watch monitor
looks every `WATCH_POLL_S` (300 s) and, when a watch is due, runs one
`watch_check` job: the `watcher` subagent (cheap model, `WATCH_MODEL`,
default `auto:haiku`, plus the web_search server tool, up to
`WATCH_SEARCHES_PER_CHECK` searches) answers with a JSON verdict. Met: an
info alert with push (`raise_alert`, kind `watch`: phone + TV), spoken in the
room too unless `WATCH_ANNOUNCE=false`, and the watch retires. Expired: a quiet
inbox note. Cadence words: hourly, every N hours/minutes (floor
`WATCH_MIN_INTERVAL_MIN`, 30), twice a day, daily, every morning/evening/night,
every day at 9 pm, weekly; default every 6 h. The first check runs right away
unless the model sets `not_before` (a known drop time). Costs are capped by
`WATCH_MAX_CHECKS_PER_DAY` (12, all watches) and `WATCH_MAX_CHECKS_PER_WATCH`
(4 per watch per day); every call lands in the usage ledger as `watcher`
(Agents map: Watcher).
- `watch_for(query, done_when, label?, cadence?, not_before?, expires_in_days?)`
- `list_watches` — active watches (next check, last finding) + recently finished
- `cancel_watch(watch)` — by id or a word from the name (voice-locked to Nick)

### Email (background subagent) *(new)*
The email sorter is JARVIS's first subagent (`brain/subagents.py`,
`brain/email_triage.py`). Once Gmail is linked it sorts new inbox mail every
`EMAIL_TRIAGE_INTERVAL_S` (15 min): annoying mail to the Trash (30-day,
restorable), missionary mail into that missionary's folder (Gmail labels under
`EMAIL_MISSIONARY_PARENT`, default `Mission Emails/<name>`; filed mail is marked read), everything even
slightly important left in the inbox. Starred mail and `EMAIL_NEVER_TRASH`
senders are never trashed; a sender filed to the same folder twice is filed
without asking the model again. Senders trashed `EMAIL_UNSUB_THRESHOLD` times
in `EMAIL_UNSUB_WINDOW_DAYS` go on the unsubscribe list (vault note
`Jarvis Memory/Email Unsubscribe List.md`). All tools are locked to Nick's voice.
- `sort_inbox(days?)` — start a sorting pass now in the background (announces when done)
- `email_triage_report(hours?)` — what was kept, filed and trashed
- `email_unsubscribe_list` / `email_unsubscribed(sender)` — the list, and striking a sender off it
- `restore_email(query, days?)` — untrash by sender/subject and never trash that sender again
- `background_agents` — running and recent subagent jobs

### Trading (real Schwab orders) *(new 2026-11-10)*
Nick: "enable jarvis trading on my charles schwab" (`brain/trading.py`). The
guardrails agreed 2026-10-27:
- **Every order waits for Nick.** `place_trade(id | symbol, side, dollars)`
  (or approving a buy/sell with `decide_investment`) prices the order live and
  opens a `confirm_gate` proposal with the exact order: "sell 1 share of SPCX
  at $25.77 or more (last $25.90), about $25.90, in account ...1234 for P3".
  Nothing reaches Schwab until Nick taps **Approve** in the companion's chat,
  says "yes" as his next message, or "approve <code>" within 10 minutes.
  Always `propose`, never `act`.
- **Caps:** `TRADING_MAX_ORDER_USD` (100) per order, `TRADING_MAX_DAY_USD`
  (250) per ET day, buys and sells together; checked when staged and again
  (with fresh account numbers) the moment Nick approves.
- **Cash only, stocks/ETFs only.** A buy may spend `trade_cash`
  (`cashAvailableForTrading`, never margin buying power) plus open Schwab sell
  orders; a sell never exceeds what Schwab's API shows held (manual holdings
  don't count). Anything whose quote isn't `EQUITY` is refused.
- **Order shape:** a dollar amount worth at least one share becomes whole
  shares (rounded down) as a DAY limit 0.5% past the last price; under one
  share becomes a fractional market order. **First live order (2026-11-10,
  #1008619430771, sell 0.1914 SPCX, market open, SPCX status Normal): Schwab's
  firm cancelled it at once, "No trades are currently allowed".** Most likely
  the API refuses fractional quantities; every holding in the main account is
  under one share, so no whole-share order has been tried yet. A rejected
  order is reported first ("Schwab rejected it…"), marked `rejected` in the
  journal, doesn't count toward the day cap, and leaves its proposal open.
  Selling ~everything sells the exact quantity.
- Placed orders go in `memory/trades.json`; the proposal becomes `ordered`
  with its `order_id`. `investing_status` shows trading on/off, caps, today's
  orders and approved-but-unsent proposals.
- **Kill switch:** `TRADING_ENABLED=false` (default on). Also stops an order
  already staged if Nick approves after it's switched off.
- Core places orders with the token it borrows from the Schwab hub. The hub
  README planned a hub `/api/orders` with hub-side limits, but a borrowed
  token can already trade, so limits there would add nothing; Core enforces them.

### Approve / Deny buttons in chat *(new 2026-11-10, companion 6.6)*
Anything staged through `brain/confirm_gate.py` (orders, stack fixes,
scenes, media requests) shows under JARVIS's reply as a card with
**Approve** and **Deny**. The app reads `GET /api/approvals?session_id=`
(admin) after each turn and when a chat opens; a tap sends `approve <code>`
or `deny <code>` as Nick's own chat message, so the gate decides it exactly
as if he typed it (no new approval path, recorded in history, no model call).
`deny <code>` (also decline/reject/cancel <code>) drops just that proposal.

### Investing (background subagents) *(new 2026-11-09)*
Nick's plan, designed with him 2026-11-09 (`brain/investing.py`). The agents
propose, record and push; since 2026-11-10 an approved buy/sell becomes a real
order that still waits for Nick's Approve (Trading, above).
- **Main account** = `INVESTING_MAIN_ACCOUNT` (last 4 digits), or the only
  account while there is one. Target weights per symbol (percent of the
  account, `CASH` allowed). New cash goes to whatever is furthest below target
  (`cash_plan`); a holding more than `INVESTING_BAND_PCT` (5) points over
  target gets a trim. Proposals (ids `P1`, `P2`…) wait for Nick and expire
  after 3 days; approving one stages its real order (Trading, above). Approving a target change updates the targets.
- **Suggest weights**: the first time Schwab's accounts are in and there are no
  targets, the agent studies the holdings and suggests starting weights
  (alert "Portfolio weights to approve"); Nick says "approve the weights" or
  gives his own.
- **Portfolio advisor**: each market day after `INVESTING_REVIEW_HOUR_ET`
  (10.5 = 10:30 ET) it reviews the main account (holdings, drift, price
  history, web search for news) and proposes up to 4 trades or target changes,
  tax-aware (steer cash first, sell only past the band or for a reason). New
  companies are allowed as proposals only.
- **Crash watch**: a holding down `INVESTING_DROP_PCT` (8) % today (market
  hours, checked every 5 min, once per symbol per day) gets a focused check.
  Nick's rule: company-specific bad news (not the whole market) lets the agent
  sell up to `INVESTING_EMERGENCY_SELL_PCT` (25) % without asking. With orders
  off, that sale becomes an `emergency` proposal plus a warning push.
- **Playground**: the agent's own account, `PLAYGROUND_MONTHLY` ($5) a month,
  no rules except cash-only. Until Nick opens the real account it trades
  **paper money** at live Schwab prices, once a week (`PLAYGROUND_WEEKDAY`,
  0 = Monday), keeps a journal, and is scored against plain VOO bought with the
  same deposits.
- Model `INVESTING_MODEL` (default `auto:sonnet`), `INVESTING_WEB_SEARCHES`
  per run, `INVESTING_MAX_ROUNDS`. Subagents can now opt into Anthropic's
  `web_search` (`SubagentSpec.web_search_uses`; `pause_turn` resumes).
  State in `memory/investing.json`. All tools are locked to Nick's voice.
- Every agent sees **Nick's notes** (corrections like "I don't own Amazon",
  `investing_run(note=…)`, or `job=note_only` to just record one) and any
  **open Schwab orders** (`schwab.open_orders()`, so pending buys aren't treated
  as idle cash). Bumping `_SUGGEST_VERSION` re-runs the weight suggestion
  while none are approved (v2, 2026-11-09: v1 added Amazon and dropped SpaceX).
- `investing_status` — targets, drift, cash plan, proposals waiting, playground score
- `investing_run(job)` — `suggest` / `review` / `playground` now, in the background
- `set_portfolio_targets(weights | accept_suggested)` — Nick sets or approves the weights
- `decide_investment(id, approve)` — approve or decline a proposal (approving a buy/sell stages its order)
- `place_trade(id | symbol, side, dollars)` — stage a real Schwab order; Nick approves with the button, "yes" or the code
- `record_holding(symbol, value_now, cash_used)` — a holding Nick's app shows but Schwab's API doesn't yet (state `manual`, auto-cleared when the API catches up)

### Agents map (companion) *(new 2026-11-09)*
`GET /api/agents` (`status:read`, `brain/agent_roster.py`): JARVIS as the head
and every agent that works for him (memory keeper, reflector, study coach, email sorter,
portfolio advisor, crash watch, playground trader) with on/off and why, what
it's doing now, what's waiting for Nick, and its last 5 runs (per-job history
in `subagents.jobs_snapshot()`). The companion's **Agents** view
(`Views/AgentMap.swift`) draws them around JARVIS's orb; a working agent's link
carries a moving dot, an off one is dimmed and dashed; tap for details. Opened
from the iPad Room menu (and the iPad Memory tab toolbar, next to Link map) and, on the iPhone, Settings > Agents (the phone has no Memory tab; its Memory is a Settings panel too).
Each agent has a `kind`: **subagent** (`brain/subagents.py`: own instructions
and tools, works in steps: email sorter, portfolio advisor, crash watch,
playground trader) or **helper** (one model call, no tools: memory keeper,
reflector, study coach). The map shows subagents with a solid ring and helpers with a
smaller dashed one, in separate list sections. **The map is fluid**: any
subagent job or spec that runs without an `_AGENTS` entry still appears
(`new:<name>`, sparkles icon, named after its job), and the app draws whatever
Core lists, so no app update is needed. Give new subagents a proper `_AGENTS`
entry in `brain/agent_roster.py` (name, kind, icon, description, schedule);
`tests/test_agent_roster.py` fails until every subagent name in the code has one.

### Shopping list
- `add_to_shopping_list(item, quantity?, note?, store?, new_item?)` — matches
  saved items by name ("face lotion" → the saved CeraVe one); several matches
  → asks which; anything new is saved for next time
- `shopping_list` — what's on it, grouped by store
- `mark_shopping_bought(items[], not_bought?)` — tick off (kept in the catalog)
- Companion API: `GET /api/shopping` (list + catalog), `GET /api/shopping/suggest?q=`,
  `POST/PUT/DELETE /api/shopping/items[/{id}]`, `POST|DELETE …/{id}/list`,
  `POST|DELETE …/{id}/bought` (DELETE = undo), `PUT|GET|DELETE …/{id}/photo` (raw JPEG)

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
- `temporary_4k(action?, title?, hours?)` — "4K Harry Potter for tonight".
  A second Radarr (`radarr4k`) downloads a 4K web release of a film already
  in the main library into `/volume2/data/media/temp-4k-movies`; Plex shows
  it as a second version. `request` (default) fetches or extends, `status`
  lists, `remove` drops one early. The runtime's temp-4K monitor alerts when
  it lands and deletes it `TEMP_4K_HOURS` (48) later; requests with no 4K
  release after `TEMP_4K_GIVE_UP_HOURS` (72) are dropped. Movies only; the
  1080p file is never touched; already-4K films are refused. State:
  `memory/temp_4k.json`. Setup: "Temporary 4K Radarr setup" below.
- `find_series(title)` — Sonarr `/series/lookup`. Same shape, TVDB id.
- `add_series(tvdb_id, quality?, root_folder?, monitor?, season_folder?, confirmed)`
  — Sonarr `/series` POST with `addOptions.searchForMissingEpisodes: true`.
  `monitor` defaults to `future` (new episodes only); accepts `all`,
  `firstSeason`, `pilot`, `latestSeason`, `none`, or natural phrasing.
- "Add <title>" is the confirmation: when the pick is the only result, or the
  top result with no other result of the same title, the add runs in the same
  turn (`media_selections.is_clear_pick`). Otherwise (two "Gold"s, or the
  model picked a lower result) it still adds at once for Nick; only a guest
  voice gets a `confirm_gate` proposal.
- `list_quality_profiles(service?)` — the profile names defined in Radarr /
  Sonarr, for when a quality phrase doesn't match.
- `media_queue(service?)` — live Radarr/Sonarr activity with movie/show/episode,
  progress, size remaining, ETA, tracked status, and error detail
- `media_health(service?)` — current download-client, indexer, disk, and other
  Servarr health warnings
- `media_history(service, limit?)` / `missing_media(service, limit?)` /
  `upcoming_media(service, days?)` — bounded read-only operational views
- **No confirmation round** (2026-11-04: Nick hates "approve 4093"): his
  request is the yes. When several plausible titles compete the agent asks
  which one, once. Selection receipts bind the allowed TMDB/TVDB IDs to the conversation for
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
  separate explicit flag and every action revalidates the live exact hash.
  Pause/resume use qBittorrent 5's `/torrents/stop`/`start` (the NAS runs
  5.2.3; the old `pause`/`resume` paths 404 there) and fall back to the 4.x
  names on a 404.
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
- `web_fetch` — Anthropic server tool: reads a page whose URL is already in the
  conversation (WEB_FETCH_ENABLED). Both use the `_20260209` dynamic-filtering
  versions on Opus/Sonnet 4.6+ and Fable, the basic ones on Haiku
  (`tools._dynamic_web_tools`). A 400 naming one drops it for the process
  (`reject_server_tool`) instead of failing every turn; `pause_turn` resumes.
- `announce_in_room` — speak a broadcast on the room speaker (chime first)
- `browser_open` / `browser_read` / `browser_act` — JARVIS's own browser
  (`brain/browser.py`): numbered links, buttons and fields; click, type,
  type_and_enter, select, scroll, back. Headless Chromium via Playwright when
  installed, else plain HTTP (read + follow links). Public pages only
  (re-checked after every navigation), never types into password/card/account
  fields, and submitting a form that posts data needs `confirmed` (Nick's yes);
  search boxes don't. Page text is marked untrusted. Closes after BROWSER_IDLE_S.

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
- Repeat alerts are condensed (2026-11-03; Nick saw the same error twice
  with different times): `event_bus.condense_key` (kind + title + context,
  else the exact text) gives every push a default `collapse_id` so a repeat
  replaces the earlier notification on the phone, `/api/alerts/recent` shows
  one row per problem with `repeats` (the Hub shows "3×"), and the same
  warning raised again within `ALERT_REPEAT_WINDOW_S` stays visible but does not push again; suppression is persisted
  so a jarvis-api restart does not resend every push. Already-live clients do
  not receive repeated banners for the same active condition; history retains
  occurrences for polling and replay.
- `refresh_alerts` — the Hub's Needs attention Refresh button (↻,
  `POST /api/alerts/refresh`, admin token; replaced the Clear button
  2026-11-03): `brain/alert_checks.py` re-checks each alert against the live
  system and hides the fixed ones (Schwab linked again, the stalled/errored
  torrent finished, moving or gone, the offline device back, the iPad
  charging or above 20%). Arr incidents require healthy live stack checks; NAS
  alerts carry condition keys checked against fresh host telemetry. Unrelated
  recovery notes never clear warnings. Anything it cannot judge stays.
- `clear_alerts` — empty the app's Needs attention list ("things need you")
  outright when Nick says it's all handled (`POST /api/alerts/clear`, admin
  token; voice only now). Cleared alerts drop out of `/api/alerts/recent`
  (still in bus history); new ones show as usual. In memory, like the bus.
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
  JARVIS cannot edit these definitions; command repairs require the chat gate or app approval), or `clear_flag` (transient fault).
- `propose_stack_fix(fix, reason)` — stages one allowlisted NAS-stack fix
  (`brain/stack_fixes.py`): `restart_home_assistant` (then waits until HA
  answers), `retest_indexers` (Prowlarr test-all, then checks none is backed
  off), `refresh_plex`, `arr_recovery` (the existing restricted recovery via
  `arr_stack`, with its own follow-up), `retry_download` (pass `title`),
  `switch_indexer_mirror` (pass `indexer`: tests Prowlarr's mirrors without
  saving, saves the first that passes; `STACK_FIX_MIRROR_TEST_S`, default 90,
  bounds each test). It never runs anything: in chat it
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

### Lessons from failed turns *(new 2026-11-13)*
- `list_lessons` — the one-line lessons JARVIS drew from his own mistakes,
  with ids and how often each was reinforced.
- `forget_lesson(identifier)` — by id, a unique fragment, or `all` (owner-only,
  in `voice_trust.LOCKED_TOOLS`).
- How they're made (`brain/reflector.py`): after every trusted turn the agent
  hands the exchange and its tool results to the reflector. Heuristics in code
  flag a bad turn, and only then one forced-tool call on the extraction model
  (`LESSONS_MODEL`, falling back to `MEMORY_EXTRACT_MODEL`, Haiku) writes at
  most one lesson, on a daemon thread (two at most in flight). Signals:
  **correction** (Nick's next message within 5 minutes says "no, the …",
  "that's wrong", "not that", "I said …", "that didn't work", "still not …",
  "you misheard", "wrong room"; a bare or polite "no", or a "no" answering
  JARVIS's own question, is not one), **repeat** (he says nearly the same
  thing, ≥2 content words and ≥0.7 similarity, within two turns and 5
  minutes), **false_success** (a tool result looked like a failure per
  `tool_health.looks_like_failure` or an "error" prefix, and the reply never
  admitted it). The model sees the standing orders and existing lessons and
  returns an empty lesson for false alarms, explicit "from now on" rules (the
  memory keeper turns those into standing orders) and honest outages.
- The store (`brain/lessons.py`, `memory/lessons.json`): text, tool,
  situation, signal, created, last_reinforced, hits. The same lesson again
  (same tool, wording ≥0.6 similar) reinforces instead of duplicating. Weight
  is hits halved every `LESSONS_HALF_LIFE_DAYS`; under 0.15 it is dropped (a
  one-hit lesson lasts about 5½ weeks at 14 days); capped at `LESSONS_MAX`,
  weakest first. The top `LESSONS_IN_CONTEXT`, ranked by weight times overlap
  with Nick's message, ride in `<turn_context>` as "Lessons from your own past
  mistakes", never the system prompt. Usage is recorded as `lessons`; the
  Agents map shows it as the **Reflector** helper. Lessons are about JARVIS;
  facts about Nick stay in `memory.py`, his explicit rules in standing orders.

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
RADARR_DEFAULT_QUALITY=Standard   # profile for requests with no quality (app quick add, chat, voice)
SONARR_DEFAULT_QUALITY=TV
# future | all | firstSeason | latestSeason | lastSeason | pilot | none
SONARR_MONITOR_STRATEGY=future
SONARR_LANGUAGE_PROFILE_ID=       # Sonarr v3 only; blank = first listed profile (v4 ignores it)
# NL phrase => quality-profile name (exact name from the app); ';'-separated
RADARR_QUALITY_MAP=4k efficient => UHD Bluray + WEB; 1080p => HD-1080p; 4k => Ultra-HD
SONARR_QUALITY_MAP=1080p => HD-1080p; 4k => Ultra-HD

# Temporary 4K (integrations/temp_4k.py): the second Radarr. Monitor starts
# only when both are set (restart Core after adding them).
RADARR4K_URL=http://<same host as RADARR_URL>:7879
RADARR4K_API_KEY=...
RADARR4K_QUALITY=Temp 4K          # profile in the 4K Radarr
RADARR4K_ROOT_FOLDER=             # blank = its only root folder (/temp-4k-movies)
TEMP_4K_HOURS=48                  # kept this long after the file lands
TEMP_4K_GIVE_UP_HOURS=72          # drop a request with no 4K release by then
TEMP_4K_MONITOR_ENABLED=true
TEMP_4K_INTERVAL_S=300

# qBittorrent WebUI (optional, read-only integration)
QBITTORRENT_URL=http://localhost:8080
QBITTORRENT_USERNAME=
QBITTORRENT_PASSWORD=
QBITTORRENT_MONITOR_ENABLED=true
QBITTORRENT_MONITOR_INTERVAL_S=30
QBITTORRENT_ANNOUNCE_COMPLETIONS=true
DOWNLOAD_BATCH_MAX_WAIT_S=1800  # episodes are announced once per show (brain/download_batches.py); a stuck one holds the rest at most this long

# Tunable at runtime via JARVIS voice commands
HUMOR_PERCENT=30           # 0-100
ADDRESS=sir                # how JARVIS addresses Nick
ELEVENLABS_SPEED=1.155     # TTS speed; clamped to 0.7–1.2 in speech/tts.py (code default 1.05)
ELEVENLABS_MODEL=eleven_flash_v2_5  # TTS model (read once at startup; restart after changing)
TTS_PROVIDER=elevenlabs     # piper = the local Piper voice speaks first (room + phone), ElevenLabs is the fallback for any line it can't say; read live
PIPER_VOICE_MODEL=          # .onnx path (its .onnx.json beside it): absolute, relative to the repo, or a bare name in voices/ (e.g. en_GB-alan-medium). Empty: the fallback uses voices/en_US-ryan-high
PIPER_LENGTH_SCALE=         # empty = the model's default; >1 slower, <1 faster (e.g. 0.95)
PIPER_NOISE_SCALE=          # empty = model default (~0.667); lower is flatter, higher more expressive
PIPER_NOISE_W=              # empty = model default (~0.8); phoneme-length variation
PIPER_SPEAKER_ID=           # multi-speaker models only
TTS_STREAM_PLAYBACK=1       # Linux: ElevenLabs audio plays through aplay as it downloads (first word ~0.2 s sooner on short lines, ~0.75 s on long ones); 0 = wait for the whole clip as before
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
WATCH_MONITOR_ENABLED=true      # "let me know when" watches (brain/watches.py)
WATCH_POLL_S=300                # how often the monitor looks for due watches (min 60)
WATCH_MAX_CHECKS_PER_DAY=12     # web-search checks per day across all watches
WATCH_MAX_CHECKS_PER_WATCH=4    # checks per day for any one watch
WATCH_DEFAULT_CADENCE_HOURS=6   # when Nick doesn't say how often
WATCH_MIN_INTERVAL_MIN=30       # fastest allowed cadence
WATCH_DEFAULT_EXPIRY_DAYS=14    # a watch gives up quietly after this
WATCH_SEARCHES_PER_CHECK=3      # web_search max_uses per check
WATCH_MODEL=                    # model id or auto:<family>; default auto:haiku
WATCH_ANNOUNCE=true             # also say a found watch in the room

# Voice tuning
WAKE_THRESHOLD=0.6         # wake score 0-1 (higher = harder to wake; was 0.5, raised to cut false triggers)
WAKE_CONSECUTIVE=2         # frames in a row over the threshold before it wakes (debounce against stray noise)
PREROLL_SECONDS=4.0
FOLLOWUP_TIMEOUT_S=10.0
FOLLOWUP_TASK_S=2.5
INTERRUPT_RMS_THRESHOLD=1500
WHISPER_MODEL=small.en
STT_PROVIDER=local          # elevenlabs (Scribe, uses ELEVENLABS_API_KEY) or openai: the command after the wake word goes to the cloud
STT_MODEL=                  # cloud model override (scribe_v1 / gpt-4o-mini-transcribe)
STT_TIMEOUT_S=6             # slower than this (or any error, or an empty transcript of a 1.5 s+ utterance) falls back to local Whisper
# Default is one deterministic decode (fast, stable latency on CPU); true
# restores faster-whisper's re-decode-on-low-confidence temperature schedule.
WHISPER_TEMPERATURE_FALLBACK=false

# Feature flags / tuning (all optional, sensible defaults)
STARTUP_NETWORK_GRACE_S=120   # boot grace: monitors retry without recording outages
BRIEFING_ON_LAUNCH=false    # speak a briefing at startup
STREAMING_TTS=false         # opt-in sentence-streamed TTS in every interactive mode, the room voice ("Hey JARVIS") included
MEMORY_MAX_FACTS=60         # cap facts injected into the prompt (0 = no cap)
MEMORY_RELEVANT_FACTS=12    # query-matched/recent profile facts per turn
OBSIDIAN_VAULT_PATH=        # optional vault root; defaults to memory/obsidian
OBSIDIAN_INDEX_FOLDERS=Jarvis Memory  # comma-separated folders JARVIS may read
OBSIDIAN_MAX_RESULTS=4      # maximum note excerpts retrieved per turn
OBSIDIAN_MAX_CHARS=4000     # hard cap on retrieved note text per turn
OBSIDIAN_MAX_FILE_BYTES=1000000 # skip unusually large Markdown files
OBSIDIAN_JOURNAL_FOLDER=Jarvis Memory/Journal  # vault-relative daily-note folder
OBSIDIAN_IMPORT_FOLDER=Jarvis Memory/Imported  # where pasted memory imports are saved
TRAFFIC_NAS_HOSTS=          # extra hostnames counted as the NAS on the link map (HA/Plex/arr URLs count already)
TV_HOST=                    # the TV's IP, so direct calls to it show as TV on the link map
OBSIDIAN_NOTES_FOLDER=Jarvis Memory/Notes      # vault-relative folder for titled captures
MEMORY_AUTO_EXTRACT=true    # background post-turn fact extraction (ChatGPT-style memory)
MEMORY_EXTRACT_MODEL=claude-haiku-4-5-20251001 # cheap model used for extraction
MEMORY_CORE_MODEL=          # model that builds the core profile (default auto:sonnet)
MEMORY_CORE_REFRESH_H=24    # rebuild the core profile for newly learned facts at most this often (imports rebuild at once)
MEMORY_AUTO_CONFIDENCE=0.75 # facts at/above this write directly; below -> proposals
MEMORY_EXTRACT_MIN_CHARS=12 # skip extraction for very short user turns ("yes")
LESSONS_ENABLED=true        # reflection after failed turns (brain/reflector.py) + lessons in the turn context
LESSONS_MODEL=              # reflector model; falls back to MEMORY_EXTRACT_MODEL, then auto:haiku
LESSONS_MAX=40              # cap on stored lessons (weakest dropped first)
LESSONS_IN_CONTEXT=4        # lessons injected per turn (0 = store but don't inject)
LESSONS_HALF_LIFE_DAYS=14   # a lesson's weight halves this often unless reinforced
HABITS_MIN_TURNS=10         # turns logged before the habits digest is injected
VISION_MODEL=               # empty/auto follows the newest Sonnet (auto:sonnet); a concrete id pins it
VISION_ALL_MONITORS=true    # capture the whole virtual desktop
WEB_SEARCH_MAX_USES=5       # max web_search calls per turn
WEB_FETCH_ENABLED=true      # Anthropic web_fetch server tool
WEB_FETCH_MAX_USES=3        # pages read per turn
WEB_FETCH_MAX_TOKENS=12000  # cap on one fetched page's tokens
BROWSER_ENGINE=auto         # auto = Playwright Chromium if installed, else http
BROWSER_IDLE_S=300          # close the browser after this long unused
BROWSER_TIMEOUT_S=20        # per page load
BROWSER_TEXT_CHARS=5000     # page text shown to Claude per step
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
WAKE_UP_LIGHTS=1            # I'm up (phone or TV) turns on every light (strip segments skipped); 0 = off
ROOM_VOICE=0                # 1 = "Hey JARVIS" on the Optiplex mic/speakers inside Core (speech/room_voice.py)
AUDIO_OUTPUT_DEVICE=        # ALSA device for aplay; Optiplex: plughw:1,0 (card 0 is the USB mic, no output)
MIC_DEVICE=CMTECK           # sounddevice input name/index. Set it: left empty, Core's room voice opened the onboard jack (hw:1,0). Rates it refuses are converted (48 kHz -> 16 kHz)
VOICE_LIVE_MODEL=tiny.en    # Whisper model for live captions on the iPad; "off" to skip
VOICE_SILENCE_END_MS=1000   # silence that ends what you said (750 cut Nick off mid-sentence)
VOICE_MIN_UTTERANCE_MS=1000 # never end a command sooner than this (a pause after "turn" isn't the end)
VOICE_MIN_RMS=90            # speech is at least this loud...
VOICE_NOISE_RATIO=2.5       # ...and this many times the room's learned background (capped by MIC_RMS_THRESHOLD)
WHISPER_PROMPT=             # words Whisper should expect; empty = the room's scene/device names; off = none
VOICE_START_TIMEOUT_S=4     # wait after the wake word before giving up
VOICE_FOLLOWUP_S=8          # no-wake-word follow-up window after a spoken answer
VOICE_PREROLL_S=2           # audio kept from before the wake word ("game time, Jarvis")
VOICE_SHORTCUT_AFTER=2      # same phrase + same simple action this many times -> instant shortcut
VOICE_BARGE_IN=1            # "Hey JARVIS" while he's talking stops him and listens
VOICE_ID=0                  # 1 = check the room voice against Nick's voiceprint; guests can't use locked tools (brain/voice_trust.py)
VOICE_ID_MODEL=             # speaker model; default models/speaker/nemo_en_titanet_small.onnx (download below)
VOICE_ID_THRESHOLD=0.55     # cosine score that counts as Nick; every verdict is logged with its score to tune this
VOICE_ID_SAMPLES=5          # sentences "learn my voice" asks for
VOICE_LOCKED_TOOLS=         # extra comma-separated tool names only Nick's voice may use
VOICE_TRUSTED_CLIENTS=      # client-token names whose /chat counts as Nick (default: every client token is a guest)
ROOM_ALARM_RING_S=120       # longest a finished timer rings on the room speaker
LIGHT_TWINS=light.desk_lights_2=light.desk_lights,light.nick_strip_lights=light.strip_lights  # cloud=LAN entity of one light; scenes' "off" goes to both
WAKE_BACKUP_DELAY_S=90      # wake-up: how long the TV gets to come on before the room speaker rings instead
WAKE_BACKUP_RING_S=600      # wake-up: longest the backup ring goes on
ROOM_ANNOUNCEMENTS=1        # speak runtime announcements (never in quiet hours)
ROOM_QUIET_HOURS=22-7       # night mode hours; empty turns night mode off
ROOM_NIGHT_VOLUME=4         # 0-10 speaker level in quiet hours (voice at night, or the app's Settings > Night volume, override it)
ROOM_ALARM_VOLUME=8         # 0-10 speaker level for the wake-up backup ring, whatever the hour; the time-of-day level comes back after
ROOM_VOLUME_CONTROL=Master  # ALSA mixer control for "volume 4" / "louder"
ROOM_VOLUME_MAPPED=1        # levels use amixer -M (loudness-matched: 8 = about -6 dB, not -12.75); 0 = raw hardware steps
TV_HDMI_INPUTS=              # HDMI 1..N input ids for the remote's Input menu (blank = the TCL's HW15,HW16,HW17)
LIGHT_RAINBOWS=             # light=select:option (or light=scene.x) pairs for the Rainbow button (defaults to Nick's strip + desk DIY scenes, the lamp's built-in Rainbow scene, and the fan ring's scene.fan_light_rainbow Tap-to-Run)
ALERT_REPEAT_WINDOW_S=1800  # suppress repeat pushes, retain visible alerts even across restarts (memory/alert_repeats.json); 0 = off
RING_LIGHTS=light.nicks_fan # colour-only lights: the apps never send white (the fan's RGB ring)
RESTING_SCENES=scene.normal,scene.peace,scene.goodnight  # running one means "no scene"
NORMAL_LIGHT=light.bedroom_lights  # left on when a scene tile leaves its scene and HA has no Normal scene
WAKE_UP_SCENE=              # optional HA scene./script. to run instead; unset uses scene.good_morning when HA has it
# Companion dashboard (read-only status hub endpoints)
PLEX_URL=                   # Plex base URL for /api/links + /api/status/services
LINK_REMOTE_HOSTS=          # lan=tailscale host pairs; /api/links adds remote_url (e.g. 10.0.0.147=100.97.179.53)
PLEX_TOKEN=                 # X-Plex-Token; enables the media doctor's "is it on Plex" stage and Plex rescan fix
MEDIA_DOCTOR_STALL_MINUTES=30  # no torrent progress for this long counts as stalled (doctor + download monitor)
HOME_DASHBOARD_ENTITIES=    # comma-separated HA entity_ids for /api/home/entities
SCHWAB_APP_KEY=             # JARVIS's own Schwab developer app (developer.schwab.com); empty = Schwab off
SCHWAB_APP_SECRET=
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182   # exactly what the Schwab app has registered
SCHWAB_HUB_URL=             # borrow the Schwab hub's login instead: https://schwab.nickolaspearson.com/api/token
SCHWAB_HUB_SECRET=          # the "jarvis" program secret from the hub page; both set = borrowing
SCHWAB_LOGIN_PAGE=          # optional; default the hub's front page
INVESTING_ENABLED=true      # the investing subagents (brain/investing.py); they propose, Nick approves
TRADING_ENABLED=true        # kill switch for real Schwab orders (brain/trading.py); each still needs Nick's Approve
TRADING_MAX_ORDER_USD=100   # largest single order JARVIS may stage
TRADING_MAX_DAY_USD=250     # most JARVIS's orders may move per ET day, buys + sells
INVESTING_MAIN_ACCOUNT=     # last 4 digits of the main Schwab account (needed once there are several)
INVESTING_MODEL=auto:sonnet # model for the investing agents
INVESTING_REVIEW_HOUR_ET=10.5   # daily review (and weekly playground) after this ET hour, market days
INVESTING_BAND_PCT=5        # trim a holding more than this many points over its target
INVESTING_DROP_PCT=8        # a one-day drop this big triggers the crash check
INVESTING_EMERGENCY_SELL_PCT=25 # most of a position the crash rule may sell without asking
INVESTING_WEB_SEARCHES=     # web searches per run (defaults 3-8 by job)
PLAYGROUND_MONTHLY=5        # the playground's monthly deposit (paper until the real account)
PLAYGROUND_WEEKDAY=0        # playground session day, 0 = Monday
BARN_DOOR_LIGHTS=light.nick_strip_lights,light.strip_lights,light.desk_lights,light.desk_lights_2,light.lamp   # go red (default; the lamp too since 2026-11-03)
BARN_DOOR_OFF=light.bedroom_lights,switch.avengers_tower_socket_1   # can't go red, so go dark (lights or switches)
BARN_DOOR_EFFECTS=light.nick_strip_lights=Rainbow-A,light.desk_lights_2=Rainbow   # effects to return to when HA can't see the running one
#   Govee DIY scenes (Nick's own rainbows) go by their dropdown instead:
#   BARN_DOOR_EFFECTS=select.nick_strip_lights_diy_scene=Real Rainbow,select.desk_lights_diy_scene=New Rainbow   (Nick's picks)
TV_REMOTE_ENTITY=remote.nick_s_room_tv   # HA Android TV Remote entity for Nick's room TV
TV_MEDIA_PLAYERS=media_player.nicks_room_tv,media_player.nicks_room_tv_2,media_player.nick_s_room_tv,media_player.nicks_room_group,media_player.nick_s_room_display   # on/off for these goes to the remote
SCHWAB_POLL_SECONDS=30      # holdings/cash refresh in market hours (prices stream live regardless)
JARVIS_THEME=arc            # arc | hotRod | malibu | crimson | mark85 | spiderMan | mcqueen | experimenting (markIII/graphite/stealth retired): default look until someone picks one on the phone or TV (then memory/theme.json via /api/theme)
CALENDAR_CATEGORIES=        # calendar name=category pairs, ';'-separated (e.g. Oil/Boil Outs=work);
                            # unlisted names with school/family/work in them use that, else personal
GLANCE_HIDDEN_CATEGORIES=work # categories left off the TV's Coming up list
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

# Gmail + email triage subagent (optional; uses GOOGLE_CREDENTIALS_FILE)
GMAIL_TOKEN_FILE=           # defaults to memory/gmail_token.json (separate from the calendar token)
EMAIL_TRIAGE_ENABLED=true   # sort automatically once Gmail is linked
EMAIL_TRIAGE_INTERVAL_S=900 # seconds between passes (min 120)
EMAIL_TRIAGE_DRY_RUN=false  # true: decide and log only, move nothing
EMAIL_TRIAGE_MODEL=         # model id or auto:<family>; default auto:haiku
DONNA_MODEL=                # Donna's conversation model; default auto:sonnet
DONNA_NOTES_MODEL=          # Donna's session notes / case file / import; default auto:haiku
EMAIL_TRIAGE_LOOKBACK_DAYS=3 # how far back an automatic pass looks
EMAIL_TRIAGE_MAX_PER_PASS=60 # messages per automatic pass (sort_inbox(days>7) allows 500)
EMAIL_TRIAGE_BATCH=25       # messages per subagent run
EMAIL_TRIAGE_MAX_ROUNDS=8   # tool-loop rounds per batch
EMAIL_MISSIONARY_PARENT=Mission Emails # sub-labels of this label are missionary folders
EMAIL_MISSIONARY_LABELS=    # extra comma-separated label names that are missionary folders
EMAIL_NEVER_TRASH=          # comma-separated addresses or domains never to trash
EMAIL_UNSUB_THRESHOLD=3     # trashes in the window that flag a sender for unsubscribing
EMAIL_UNSUB_WINDOW_DAYS=30
EMAIL_UNSUB_NOTE=           # vault-relative note path; default Jarvis Memory/Email Unsubscribe List.md
```

## Temporary 4K Radarr setup (one time)

Status 2026-11-13: steps 1–3 done, and step 4 done over the API (root folder,
Temp 4K profile id 7 = WEB 2160p only, qBittorrent category `radarr4k`,
recycle bin off so removals free space, "Radarr 4K" app in Prowlarr with the
same four active indexers as the main Radarr). Custom formats copied from the
main Radarr minus the size ladders; Temp 4K scores only the hard blocks
(-10000: non-English, AV1, Opus, BR-DISK, LQ, upscaled, extras, DV without HDR
fallback, remux, 3D) and HDR +60, min score 0, so within a tier Radarr's
seeder ranking decides (size doesn't matter for a 48-hour copy). Prowlarr's
app profile already enforces 20 minimum seeders. No Plex connection: the main
Radarr has none either, so Plex's own folder watching picks up imports.
Left: the 4K Radarr's UI login (it asks for one on first visit), step 5
(Plex folder) and step 6 (Core `.env`). Needs sudo on the NAS (`ssh nas`); no nano
there, so edit files with a `sudo python3` one-shot or vi.

1. Folders, and the port pre-seeded (both Radarrs share gluetun's network, so
   the second can't use 7878):
   `sudo mkdir -p /volume1/docker/radarr4k/config /volume2/data/media/temp-4k-movies`
   `echo '<Config><Port>7879</Port></Config>' | sudo tee /volume1/docker/radarr4k/config/config.xml`
   `sudo chown -R 1000:1000 /volume1/docker/radarr4k /volume2/data/media/temp-4k-movies`
2. In `/volume1/docker/docker-compose.yaml` (back it up first): add
   `- 7879:7879` under gluetun's `ports`, and a service next to `radarr`:
   ```yaml
     radarr4k:
       image: lscr.io/linuxserver/radarr:latest
       container_name: radarr4k
       network_mode: service:gluetun
       environment:
         - PUID=1000
         - PGID=1000
         - TZ=America/Denver
       volumes:
         - /volume1/docker/radarr4k/config:/config
         - /volume2/data:/data
         - /volume1/docker/downloads:/downloads
         - /volume2/data/media/temp-4k-movies:/temp-4k-movies
       restart: unless-stopped
       depends_on:
         - gluetun
   ```
   Then `sudo /volume1/docker/watchdog/recover_stack.sh manual` (recreates
   gluetun with the new port, then its dependents), and
   `cd /volume1/docker && sudo docker compose --project-name stack up -d --no-deps radarr4k`.
3. Updater and recovery: `ops/nas/stack_updater.sh` and `recover_stack.sh`
   list `radarr4k` with the other gluetun dependents, so a gluetun restart
   recreates it and the weekly update pulls it. Install them only after the
   container exists (`docker rm -f` of a missing name fails the chain). The
   repo's `stack_updater.sh` also carries the stricter error handling
   (installed 2026-11-13).
4. In the 4K Radarr (http://10.0.0.147:7879; Claude can do this over its
   API): root folder `/temp-4k-movies`; profile **Temp 4K** = WEBDL-2160p +
   WEBRip-2160p only, upgrades off, no size cap; qBittorrent download client
   with category `radarr4k`; add it as an app in Prowlarr; a Plex Media
   Server connection so Plex rescans on import and delete.
5. Plex: Movies library → Manage Library → add folder `/data/media/temp-4k-movies`.
6. Core's `.env`: `RADARR4K_URL`, `RADARR4K_API_KEY`, then restart Core.

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

## Gmail setup (one time)

1. In the same Google Cloud project as Calendar, enable the **Gmail API**.
   On the OAuth consent screen add the `gmail.modify` scope (and keep Nick as
   a test user if the app is in testing mode).
2. Link it explicitly (the running process never prompts):
   - Mac: `.venv/bin/python -m integrations.gmail --auth` opens a browser.
   - Optiplex: from the Mac `ssh -L 8765:localhost:8765 jarvis`, then
     `cd ~/jarvis && .venv/bin/python -m integrations.gmail --auth` and open the
     printed URL on the Mac.
3. The token lands in `memory/gmail_token.json`; the monitor starts sorting
   on its next pass, no restart needed (the monitor thread starts at launch
   whenever `GOOGLE_CREDENTIALS_FILE` is set). Create the missionary folders
   as Gmail labels nested under `Mission Emails` first (Nick's are already there).
4. Nervous? `EMAIL_TRIAGE_DRY_RUN=true` for a day and read `email_triage_report`.

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

- **Reliability pass (2026-11-14).** Fixes from a code review, each with a regression test. Barn Door: `stand_down` does nothing unless Barn Door is engaged (theme `barnDoor`), so it no longer replays an old room snapshot or drops the current theme. Monitors: device watch, home poll/websocket snapshot, calendar, Schwab, email triage, weather, downloads and the dead-man ping now catch and log a failed pass instead of silently ending their thread; interval settings read through `_env_float` (a garbled value falls back to the default). Email triage holds its state lock only around load/mutate/save (Gmail and model calls run outside it, decisions are re-checked against fresh state, so `restore_email` and unsubscribes no longer wait minutes); the action log rotates to 90 days / 20k lines atomically. Temporary 4K: a film already in the 4K Radarr before the request is marked `adopted` and expiry only stops tracking it (never deletes its files; entries without the field are treated as JARVIS-added, as before); an expiry delete waits while Plex is playing that film, or while Plex can't be asked, for at most 12 h past expiry (`plex.playing_movies`); one bad entry no longer aborts the pass. Wake alarm: a snooze lives in its own `snooze` key (no longer overwrites a pending one-off; old files read fine) and every read-modify-write holds the lock. Room: the wake-up backup ring plays at `ROOM_ALARM_VOLUME` (8) instead of possibly the night level; `room_volume` saves atomically. Investing: no review/drop check/playground on NYSE holidays (built-in 2026-2027 list in `brain/investing.py`, extend yearly); a job that fails without doing anything is un-stamped and may retry once the same day (a playground session that already traded never re-runs). Memory bounds: reflector recent-turn buffers and the agent's per-session tool context keep the newest 50 sessions; runtime reminder/calendar/proactive dedupe keys are capped. Read-live: ElevenLabs key/voice/model/speed and `WHISPER_MODEL`/`DEVICE`/`COMPUTE_TYPE`/`BEAM_SIZE` are read per use (a changed Whisper model reloads on the next utterance).
- **Local Piper voice behind one setting (2026-11-14).** Core-side wiring for the Bettany voice being trained on Nick's PC: `TTS_PROVIDER=piper` + `PIPER_VOICE_MODEL=<path>.onnx` makes a local Piper voice JARVIS's main voice, on the room speaker and the phone, with ElevenLabs as the fallback; nothing changes while `TTS_PROVIDER` is unset/`elevenlabs`. `speech/piper_voice.py` loads `PiperVoice` once in-process and caches it by path (the old fallback ran `python -m piper` and reloaded the model every line), supports piper-tts 1.2.x (`synthesize_stream_raw`) and 1.3+ (`synthesize` + `SynthesisConfig`), and reads the knobs `PIPER_LENGTH_SCALE`, `PIPER_NOISE_SCALE`, `PIPER_NOISE_W`, `PIPER_SPEAKER_ID` live. Room: `tts._stream_piper` pipes each sentence's PCM into `aplay -t raw` at the model's own rate as soon as it is synthesized (Linux; `TTS_STREAM_PLAYBACK=0` and the GUI orb use a whole-clip WAV instead); barge-in, hush and `stop_playback` stop it like the ElevenLabs stream, `cache=True`/`"auto"` lines get their own cache entries per model and knobs, and audio from the fallback voice is never cached under Piper's key. Phone: `remote_voice.pcm_stream` sends the same voice, resampled to 22.05 kHz if needed. If Piper can't speak a line (piper-tts missing, model or config missing, synthesis error before the first audio) the line goes to ElevenLabs and the reason is logged once (`Piper voice unavailable, using the fallback voice: ...`); a failure mid-sentence is never re-spoken in another voice. Each line logs `4. TTS first audio (Piper stream): N ms` at INFO. Test by ear: `python -m speech.tts --say "Good evening, sir." --provider piper --model voices/en_GB-alan-medium.onnx` (prints ms to first audio; `--wav out.wav` writes a file instead). The ElevenLabs-mode Piper fallback also tries the in-process voice first, then the old CLI. piper-tts is not in `requirements.txt` and not in the Mac venv; install it on the Surface (`.venv/bin/pip install piper-tts`). Not done: the optional light EQ (hook noted in `piper_voice.py`). Tests: `tests/test_piper_voice.py`.
- **"Let me know when X happens" + reminders on the phone (2026-11-14).** Due reminders now also go through `runtime.raise_alert` (kind `reminder`, info, push), so they buzz the phone and show on the TV; still spoken once in the room and one inbox line (`raise_alert` gained `inbox_kind` / `speakable`; announcements are no longer logged twice). New `watch_for` / `list_watches` / `cancel_watch` tools and `brain/watches.py`: durable web watches (query, done-when condition, cadence, optional not-before, expiry) checked by the new `watcher` subagent (cheap model + web_search) from a runtime monitor; met watches alert with the answer and retire, expired ones leave a quiet note; checks capped per day and per watch. Watcher on the Agents map; settings `WATCH_*`. Tests: `tests/test_watches.py`. Not run against the live API yet.
- **Answer screens in the reply panel, bigger (companion 6.26 / build 268, TV 9.6, 2026-11-14).** Nick's notes after the first run: on the iPad's Room tab the card now shows inside JARVIS's reply panel under the reply (the panel stays up while the card does) rather than floating over the screen; the phone and other tabs keep the floating panel. Every size is roughly doubled (`AnswerCardContent(k:)`, 1 on iPad, 0.72 on phone; the current temperature is 110 pt), the TV card is 760 dp wide with text 1.45x, and cards clear after 45 s instead of 60.
- **Goodnight that always runs, weather card by voice (companion 6.25 / build 267, 2026-11-14).** `goodnight.is_goodnight` now also matches "good night" wrapped in filler and mishearings of Jarvis ("Hey Jarvis, good night", "Thank you, Jervis. Good night", "I said good night", "Hey drivers, good night"), the room-log transcripts that used to fall through to the plain Goodnight scene or a model reply; anything else in the sentence still goes to the conversation. The room voice's weather quick answer puts the `weather_today` card up in the background (`quick_answers._put_weather_on_screen`), since that path never reaches the model's `show_screen`.
- **Answer screens (companion, TV 9.5, 2026-11-13).** "What's the weather?" puts a weather readout on the iPad's Room tab, the phone and the room TV, like JARVIS putting it up on the nearest display. Trigger: the model calls the new `show_screen(card)` tool (instead of `get_weather` / `todays_agenda` / `list_timers`) only when Nick asks for something with a visual shape; the tool description carries the rule, so the persona block is untouched. `brain/answer_cards.py` builds the card once (one builder per type), publishes it with `event_bus.publish_card`, and returns the spoken facts plus a note to keep the reply short. Cards are the bus's second event kind: own ids and 20-card history, never pushed to the phone, never on Needs attention, 45 s `ttl_s`. `GET /api/clients/events` (scope `events:read`, so the TV's existing token works) sends them as `event: card` frames with no `id:` line (Last-Event-ID only tracks alerts) and replays unexpired cards on connect; `?only=cards` streams just cards (the companion). Cards so far: `weather_today` (`brain/weather.get_weather_today`: the same Open-Meteo call with `hourly=temperature_2m,precipitation_probability,weather_code,is_day`, sunrise/sunset, gusts; hours from now to midnight, 8-18 of them; times as Unix seconds, conditions as one word: clear/partly/cloudy/fog/drizzle/rain/snow/storm), `calendar_day` (rest of today, `allow_interactive=False`) and `timers` (remaining seconds; clients count down). Companion: `AnswerCardPresenter` follows the card stream while the app is active and `AnswerCardOverlay` (RootView, over every tab, under Donna) shows the newest card in the panel style of the current palette; on the iPad's Room tab the card sits inside JARVIS's reply panel (`RoomDialogue`, under the reply) instead, and the overlay stays away (`AnswerCardPresenter.inlineHost`, set with the voice's inline host); tap or wait 45 s to clear it. TV: `AlertStream` routes `event: card` to `AnswerCard` (right side, over any app, no focus, fade only), screen on only. A new card type is one builder plus one renderer per client (`AnswerCard.swift` payload + view, `AnswerCard.java`).
- **Reflection after failed turns (2026-11-13).** JARVIS learns from turns that went badly, not only explicit corrections: `brain/reflector.py` flags a correction ("no, the bedroom", "that's wrong", "I said…"), a repeat within two turns, or a tool error the reply glossed over, then one Haiku call writes a one-line lesson to `memory/lessons.json` (`brain/lessons.py`). Repeats reinforce, unreinforced lessons fade (half-life 14 days), the top four relevant ones ride in `<turn_context>`. Tools `list_lessons` / `forget_lesson`; Reflector on the Agents map; settings `LESSONS_*`. Tests: `tests/test_lessons.py`. See "Lessons from failed turns".
- **iPad TV remote + movie-mode inputs (companion 6.23 / build 265, 2026-11-13).** On iPad the remote is a page-sized sheet (iOS 18 `presentationSizing(.page)`) laid out as a console: power, a 300 pt d-pad and Back/Home/Play on the left; Inputs (one tile per port, PS5 / Switch / Wii U), Volume and Apps on the right. The old form sheet was too short and pushed the d-pad into the title. Phone keeps the single column, now scrollable. Movie mode's bottom row has an Input menu beside TV remote. Port names live in `TVInputs` (TVRemoteView.swift).
- **TV input switcher, to-do sheet, Donna end (companion 6.21-6.22 / builds 263-264, 2026-11-13).** The TV remote (phone and iPad) has an Input menu: PlayStation 5, Nintendo Switch, Wii U (HDMI 1-3, the TV's own port names). The `TV_INPUT*` remote keys do nothing on the TCL (its stock home screen handles them and JARVIS TV replaced it), so `hdmi_N` sends HA `remote.turn_on` with the deep link `content://android.media.tv/passthrough/<input id>`, which the TV's own TV app (`com.tcl.tv`) plays. Input ids come from `TV_HDMI_INPUTS` (default `com.tcl.tvinput/.TvPassThroughService/HW15,HW16,HW17`; find them with `adb shell dumpsys tv_input`). To-do sheet: the + is a real button (adds what's typed, else focuses the field), the field grows to four lines with an Add button, and rows show one line with "…" and expand on tap. Donna's End session now closes Donna once she has saved her note.
- **Room volume on a loudness-matched scale (companion 6.20 / build 262, 2026-11-13).** `speech/room_volume.py` runs `amixer -M`, so "volume 8" is about -6 dB instead of -12.75 dB (raw steps made 8 sound like a quarter of 10 on the Surface). Stored levels keep their numbers, so they get louder: the night level (7 on the Surface) is now about -9 dB; say "night volume 5" for roughly the old loudness. `ROOM_VOLUME_MAPPED=0` restores raw steps.
- **Temporary 4K live (2026-11-13).** NAS side set up (see "Temporary 4K Radarr setup"); first end-to-end run: Michael (2026), asked by voice, 24.3 GB iTunes DV/HDR WEB-DL in about 25 minutes, ready alert, Plex chose the 4K version.
- **Temporary 4K movies (2026-11-12).** "Get a 4K file of Harry Potter for tonight": `temporary_4k` tool + temp-4K monitor (`integrations/temp_4k.py`) over a second Radarr whose folder is `temp-4k-movies`; alert when ready, auto-removed 2 days after it lands; the 1080p copy is never touched. NAS side done 2026-11-13 except the Plex folder and Core `.env`: "Temporary 4K Radarr setup".
- **JARVIS for Mac (companion 6.16 / build 258, 2026-11-10).** A dedicated native macOS app, not the iPad app ported over (`mac/JarvisMac/`, SwiftUI, Swift Package, no Xcode project). `mac/JarvisMac/build.sh` builds it and installs an ad-hoc-signed `~/Applications/JARVIS.app` (`--open` also launches it; it quits a running copy first). It is a thin client of Core's existing API with the companion token, so no server change. Mac-style layout: a sidebar with **Chat**, **Home**, **Alerts** and, under Private, **Donna**; ⌘1–⌘4 switch sections, ⌘N starts a new chat, ⌘R refreshes, ⌘, opens Settings.
  - **Chat:** history list (`GET /sessions`, shared with the phone; right-click to delete) beside the transcript. Replies stream (`POST /chat/stream` with `X-Jarvis-Stream-Segments: 1`, so each `ROUND_BREAK` starts a new bubble) and render inline markdown. Like the phone (6.17), the reader hands text to the UI on a 40 ms timer rather than when the next byte arrives; Core often receives a round from Claude in one burst, and the byte-driven check showed a word or two and then held the rest until the stream closed.
  - **Home:** scene tiles (`/api/home/scenes`, the current scene outlined; click runs it, or leaves it if it is current) and device tiles grouped by domain from `/api/home/entities` with on/off switches (`/api/home/control`) and a brightness slider for dimmable lights (`/api/home/light`, sent when the drag ends). Refreshes every 15 s while open.
  - **Alerts:** `/api/alerts/recent` (24 h), with Re-check (`/api/alerts/refresh`) and Clear (with a confirmation). Warnings and criticals badge the sidebar, polled every 60 s.
  - **Donna:** her rose space inside the window, sharing the one open session with the phone (reloads when the app comes to the front), End Session (with a confirmation) and What Donna knows (read-only). Importing old chats, erasing her memory and check-in notifications stay on the phone (the Mac doing check-ins too would notify twice).
  - **Composer:** Return sends; Shift- or Option-Return adds a line (a `TextEditor` whose plain Return is intercepted; a vertical `TextField` dropped newlines added in code). If a send fails, the text goes back in the box.
  - **Settings:** Core's address (a bare host gets `http://` and port 8000, as on the phone) and `JARVIS_API_TOKEN`. The token is kept in `~/Library/Application Support/JARVIS/token` (0600), not the Keychain: an ad-hoc-signed app gets a new signature each rebuild, and the Keychain would ask for the login password every time.
  - Verified against a local fake Core with the same response shapes (streamed chat with round breaks, multi-line Donna messages, home, alerts); not yet run against the real Core.
- **6.13 / build 255 (2026-11-10): the room voice streams long replies.** With `STREAMING_TTS=true`, "Hey JARVIS" starts speaking as soon as Claude has written the first sentence instead of waiting for the whole reply (`RoomVoice._converse_streamed`; one worker thread runs the turn because the runtime's turn lock is re-entrant and voice trust is a context variable). A one-sentence reply, or a bare "Done." after a tool, still takes the old path (jingle, TTS cache). Barge-in and an iPad hush stop it between sentences as well as mid-clip, and the reply is always written to history in full. `runtime.respond_streaming(spoken=True)` now switches to the room session the way `respond()` does. Room voice previously ignored `STREAMING_TTS`. Also fixed docs: the TTS model variable is `ELEVENLABS_MODEL`, not `ELEVENLABS_MODEL_ID`. `tests/__init__.py` pins `STREAMING_TTS=false` so the dev `.env` can't flip tests onto the streamed path.
- **5.98 / build 241 (2026-11-09): speed pass (measured on the Optiplex, then fixed).** A full audit of the live system: tests, imports, every read endpoint, the log's 432 model rounds, per-thread CPU, the voice path and the apps' request mix. What it found, with the numbers:
  - **Already fast, left alone.** Every read endpoint answers in 2–9 ms warm (worst cold: `/api/status/media` 407 ms, then cached). Prompt caching works: 214 of 274 first rounds read the ~28k-token prefix from cache; the 60 cold ones were all idle gaps over the 1 h TTL or deploys that changed the tool list. Model first token: median 1.22 s on round 1, 0.89 s on later rounds (the API, not us). Per-turn context building is under 10 ms (vault recall 6 ms). The "hot" thread (15% of a core) is the wake word: 3.3 ms per 80 ms frame at full clock, 10 ms because the CPU idles at 800 MHz between frames (`powersave` governor); inherent to always-on listening.
  - **Speech starts sooner.** `speak()` waited for the whole ElevenLabs clip before starting aplay (median 0.58 s, 0.9–1.1 s for long lines). On Linux it now streams the PCM into `aplay -t raw` as it arrives (`_stream_elevenlabs`; `TTS_STREAM_PLAYBACK=0` reverts). Measured from the Optiplex with Nick's voice: first audio at 172 ms instead of 409 ms for "The fan is off, sir.", 187 ms instead of 945 ms for a 12 s alert. A hush, barge-in or `stop_playback` still stops it at once and is never re-spoken; a failed request or a dead aplay before any sound falls back to the whole-clip path (and Piper). Cached lines still replay from `memory/tts_cache/`; a streamed line is cached only when it played in full. The GUI's audio-reactive orb (`on_envelope`) keeps the whole-clip path.
  - **No handshake per call.** STT and TTS each opened a new HTTPS connection (about 90 ms of DNS + TCP + TLS to api.elevenlabs.io). Both now share one kept-alive session (`speech/cloud_http.py`; a stale pooled connection is retried once). The Anthropic SDK dropped idle connections after 5 s, so every turn re-handshook; the agent's client now keeps them 120 s (`brain/agent.py` `_kept_alive`; measured 252 ms → 166 ms for a request after 8 s idle). The wake word warms all three connections in the background while Nick is still talking (`RoomVoice._warm_connections` → `stt.warm_cloud`, `tts.warm_cloud`, `runtime.warm()`).
  - **Voice ID in parallel.** The speaker check (41–82 ms of CPU) ran after the transcript came back; it now runs while the cloud transcribes. If it crashes the speaker is a guest, never Nick.
  - **The iPad stopped hammering Core.** One iPad made 1,433 requests in 20 minutes: `/api/camera/status` every 1.5 s (725) and the 12 KB `/api/study/brief` with every 5 s `now` tick (229). Now `/api/home/stream` events carry `camera` (is the lens wanted), the iPad follows that (`HubViewModel.cameraWanted` → `RoomCameraController.follow`) and polls status every 20 s behind it while the lens is off (still 1.5 s while capturing, with the stream down, or against an older Core), so the lens also starts within a beat instead of up to 1.5 s later. The brief is fetched when the countdown list changes, on a full refresh, after a failure, or every 10 minutes. Expected: about 1,430 → 500 requests per 20 minutes from the wall iPad.
  - Net for a spoken model turn (after Nick stops talking): roughly 0.5 s sooner to first word on a short reply, about 1 s on a long one. `VOICE_SILENCE_END_MS` (1 s, the largest fixed wait) was left alone: 750 cut Nick off.
  - Validation: Python suite 1051 tests (18 new: `tests/test_voice_speed.py`, `StreamedPlaybackTests`); iOS 40 tests in the simulator. The streamed request and both keep-alives were timed against the real services from the Optiplex, and the new `speak` path ran there against ALSA's silent `null` device (real aplay, a bad device falls back), but nothing was played on the speaker and nothing is deployed: see FUTURE_SESSIONS.md "Speed pass".
- **5.97 / build 240 (2026-11-09): Agents on the iPhone.** The phone has no Memory tab (Memory is a Settings panel there), so the Agents map gets its own Settings panel, "Who works for JARVIS". Installed on iPhone and iPad.
- **5.96 / build 239 (2026-11-09): Agents map labels and auto-discovery.** Each agent is a `subagent` (own tools, works in steps) or a `helper` (one AI call): solid vs. dashed ring, separate list sections, explained in the detail card. Unknown subagent jobs/specs appear on the map by themselves the first time they run, and a test fails until every subagent in the code has a proper roster entry. Not deployed.
- **5.95 / build 238 (2026-11-09): holdings Schwab's API misses.** Nick's app showed SPCX $51.87 and $0.26 cash in account 1058 while the Trader API still reported $50.25 cash, no SPCX and no order or trade in 10 days. New tool `record_holding(symbol, value_now, cash_used)` (voice-locked): `investing.main_account()` adds the holding at live prices and takes its cost out of cash, and the entry drops out by itself once the API lists the symbol. Not deployed.
- **5.94 / build 237 (2026-11-09): JARVIS stops agreeing with Schwab-contradicting claims.** Nick said his $50 cash was already in SpaceX and JARVIS agreed ("the $50.25 is sitting in SPCX"), but Schwab showed one account (...1058), $50.25 cash available, no SPCX, no orders or trades in 10 days. `investing_status` now says how many accounts Schwab shares and whether any orders are open, and its tool description tells the model to report what Schwab shows and the likely reasons instead of agreeing. Not deployed.
- **5.93 / build 236 (2026-11-09): investing fixes from the first live run.** The first weight suggestion added Amazon (Nick doesn't own it) and dropped SpaceX as "not tradeable" (it is; Nick bought $50 on 11-08). Agents now get Nick's notes and open Schwab orders, the prompt carries his facts, and the suggestion re-runs once (v2). Schwab streamer: the retry delay only resets after real quotes arrive, so a refused login backs off to 5 min instead of retrying every 16 s (seen after the 12:40 restart). Not deployed.
- **5.92 / build 235 (2026-11-09): investing agents (suggestions only) + Agents map.** `brain/investing.py`: target weights for Nick's main Schwab account, a once-off "suggest starting weights" run, a daily portfolio advisor that proposes trades for his OK, a crash watch for 8% one-day drops (his emergency rule; proposes, since orders are off) and a weekly paper-money playground scored against VOO. Subagents can use web search. `integrations/schwab.py` gains per-account holdings, `quotes`, `price_history`. New tools `investing_status`, `investing_run`, `set_portfolio_targets`, `decide_investment` (voice-locked, with `portfolio_summary`). `GET /api/agents` + the companion's Agents view (iPad Room menu, phone Memory tab): JARVIS in the middle, his agents around him. Tests in `tests/test_investing.py`, `tests/test_agent_roster.py`. Not deployed.
- **5.91 / build 234 (2026-11-08): one Schwab login (the Schwab hub).** Schwab keeps one live login per Schwab user, so JARVIS's and Nick's Coopa Trada logins revoked each other (Coopa 11-04 1:47 PM MT → JARVIS refused 1:59 PM). The new Schwab hub (schwab.nickolaspearson.com, `schwab-hub` project on the Lightsail box) holds the one login; with `SCHWAB_HUB_URL`/`SCHWAB_HUB_SECRET` set, `integrations/schwab.py` borrows 30-minute access tokens from it (live streaming unchanged). The companion's Log in buttons open the hub in Safari (`/api/schwab/login` → `external: true`); alerts and the spoken answer point there. Tests in `tests/test_schwab.py` (`BorrowedLoginTests`). Deployed 2026-11-08.
- **5.90 / build 233 (2026-11-06): JARVIS knows when voice ID recognised Nick.** Nick enrolled his voiceprint (5 samples); his next "who's talking to you right now?" scored 0.78 but got a stale "Voice ID is armed for enrolment" answer, because only guest turns told the model who was speaking. `voice_trust.context_note()` now adds a one-line "Voice ID recognised this voice as Nick (match 0.78)" to the turn context when a voiceprint matched; typed chat, the phone app and voice ID off add nothing, as before. Regression test in `tests/test_voice_trust.py`. Not deployed.

- **5.89 / build 232 (2026-11-06): subagent backbone + email triage.** `brain/subagents.py` runs focused background tool loops (own prompt, own small tool set, own model, own context) as one-at-a-time jobs that show in the task lane and announce when a person asked. The first subagent, `brain/email_triage.py` over the new `integrations/gmail.py`, trashes annoying mail, files missionary letters into each missionary's label, keeps anything even slightly important, keeps a per-sender unsubscribe list in the vault, and can restore what it trashed (protecting that sender). Handlers validate every model choice (id in batch, folder exists, sender not protected); email text is fenced as untrusted data. New tools `sort_inbox`, `email_triage_report`, `email_unsubscribe_list`, `email_unsubscribed`, `restore_email`, `background_agents` (email tools voice-locked). Tests in `tests/test_subagents.py`, `tests/test_email_triage.py`. Gmail not linked yet; never run against a real inbox. Not deployed.

- **5.88 / build 231 (2026-11-06): one download announcement per show, not per episode.** The download monitor used to say "Silo, season 1, episode 3 has finished downloading, sir" for every episode. Episodes now wait in `brain/download_batches.py` and are announced together once none of that show's torrents is still downloading ("4 episodes of Silo have finished downloading, sir."; a lone episode keeps its full name), or after `DOWNLOAD_BATCH_MAX_WAIT_S` (30 min) so a stuck one can't hold the rest. Movies and season packs are announced at once as before; `QBITTORRENT_ANNOUNCE_COMPLETIONS=false` still silences all of it. Server-only; tests in `tests/test_download_batches.py`. Not yet deployed.

- **5.87 / build 230 (2026-11-06): tap the iPad to hush the room voice.** With people talking in the room, every overheard sentence kept the follow-up window open and JARVIS kept answering. Now a tap on the conversation (the Room tab's dialogue panel or the overlay on any other tab) while he's listening, thinking or speaking calls `POST /api/voice/hush` (`chat` scope; `{"hushed": false}` when he was idle): Core's room voice (`speech/room_voice.py` `hush()`) stops playback, closes the capture or follow-up window, never speaks a reply still being written (the turn still lands in history), skips the barge-in listen and waits for "Hey JARVIS" again. A tap while idle is ignored, so it can't eat the next wake. Light haptic on the tap. Validation: full Python suite plus new `HushTests`/`HushEndpointTests`; iOS builds for device. Not yet deployed or tried on hardware.

- **5.77 / build 220 (2026-11-03): phone hold-to-talk, live transcript and streamed voice.** Hold the Home orb for 0.3 s to capture; partial transcripts from the phone’s native speech recognizer reach HQ over authenticated `WS /api/voice/live`. Release finalizes the last word and commits exactly one request through the current Chat session and runtime receipt path; sliding away, changing conversations/tabs during capture, backgrounding or interruption cancels without sending. The phone shows HQ’s transcript acknowledgement, streamed answer text, and starts playing 22.05 kHz mono PCM as it arrives. Core’s `speech/remote_voice.py` uses the existing ElevenLabs voice via HTTP streaming, with isolated temporary-file Piper fallback; it never plays on the room speaker. Existing Chat drafts survive, history/retries share request IDs, and disconnect after commit stops delivery while Core finishes persistence. Triple-tap Barn Door remains. Phone speech/microphone permissions are required; first-time permission prompts may require holding again. Core runs on the Optiplex, not the NAS. Validation: 961 Python tests (12 skipped), 38 iOS simulator tests, and 74 scoped tests on the Optiplex passed; live Core smoke verified transcript acknowledgement, cancel-without-history, PCM speech, persisted history and same-ID recovery. Core is deployed and restarted. The live smoke received text at 1.61 s and audio at 2.64 s; the temporary test session was deleted. Signed 5.77 (220) installed on Nick’s iPhone 17 Pro; the on-device hold/microphone interaction still needs his hands-on check.

- **5.76 / build 219 (2026-11-03): quieter Chat chrome and orb taps.** The phone Home orb no longer starts a briefing on one tap; triple-tap still toggles Barn Door. Chat uses a transparent navigation bar and composer surroundings, and only shows the connection row when it needs attention or a reply is in progress. The phone tab controller keeps its full height during keyboard presentation and hides the tab bar while typing, leaving keyboard avoidance to the selected page’s composer. Dismissing the keyboard restores the tab row. All 35 iOS simulator tests passed, including real keyboard focus and tab restoration; Chat renders inspected. Signed 5.76 (219) installed and reopened on Nick’s iPhone and iPad.

- **5.75 / build 218 (2026-11-03): orb sizing and consistency.** Home’s hero orb grows from 170 to 190 pt (the accessibility layout retains 130 pt). The shared classic reactor caps arc weight and tick depth at the 190 pt phone reference; larger Room orbs keep fine rings and use an ultra-light central waveform instead of gaining heavier lines as the panel expands. Existing visual snapshot test passed and phone navigation render inspected; signed 5.75 (218) installed on Nick’s iPhone 17 Pro and iPad Air.

- **5.74 / build 217 (2026-11-03): phone swipe and alert review fixes.** Phone tabs now use persistent native hosting controllers and an interactive UIKit transition: adjacent pages move together at finger speed, release velocity decides completion, and cancellation restores the original page. The native tab bar stays in place; left-edge back gestures, horizontal controls, and Reduce Motion retain their own behavior. iPad/Mac keep their tab shell; idle timers no longer restart on every drag sample. Repeat alerts remain visible while duplicate pushes are suppressed, including after restart; resolved recurrences and severity escalations can push immediately. Refresh checks live arr health and keyed NAS conditions, never unrelated recovery notes. Unknown or missing telemetry keeps the warning. Simulator regression tests cover swipe completion, cancellation, page identity, and Reduce Motion. Validation: 951 Python tests (12 skipped), 34 iOS simulator tests passed; navigation snapshot inspected. Signed build installed on Nick’s iPhone 17 Pro; Core changes were subsequently deployed with 5.77.

- **Scenes force their lights off (2026-11-01, Core)** — Goodnight left the
  desk lights on: HA's `scene.turn_on` skips a light whose state already
  matches, and the Govee cloud entity (`light.desk_lights_2`, which the scenes
  use) can read "off" while the light is lit. After any scene runs,
  `status_hub._force_scene_offs` (1.5 s later, in the background) reads the
  scene config and sends one plain `light.turn_off` to every light it sets
  off plus its twin under the other integration (`LIGHT_TWINS`, default
  `light.desk_lights_2=light.desk_lights,light.nick_strip_lights=light.strip_lights`:
  Govee cloud = Govee LAN).

- **Spoken download names (2026-11-01, iOS 5.61 / build 204)** — the
  download-complete announcement says "Iron Man has finished downloading,
  sir." instead of the release name: `integrations/qbittorrent.friendly_name`
  drops the extension, [tags], quality/codec/source tokens and the release
  year (the last year before the tags, so "Blade Runner 2049" and "1917"
  survive), and reads S02E03 as "season 2, episode 3" and anime "- 12" as
  "episode 12". The event log keeps the raw name.

- **Tappable Link map (2026-11-01, iOS 5.60 / build 203)** — tap any
  endpoint on the Link map to read its last 60 signals (kept in
  `TrafficFeed.history`, 400 max): direction, a plain-English description
  (`TrafficWords`: "Home Assistant: light turn on", "Asking Claude", "Reply:
  camera frame"), the raw label and how long ago; tap again or ✕ to go back
  to the live "Just now" list. Core's outbound labels now carry the path too
  (`traffic.host_node`: "home assistant /api/services/light/turn_on"), never
  the query string, with any path segment of 24+ characters masked (APNs
  device tokens, bot tokens).

- **Bigger core, smaller nodes, deeper pool (2026-11-01, iOS 5.58 / build
  201, TV 9.4)** — `NeuralCore` node boxes shrink (`size / 125`) while the
  wires keep their weight. The Room's Wake-up row moved into the Menu (its
  label shows the next alarm) and the core may spill 18 pt into the card's
  side padding, so it fills the left card. The dark pool is a touch darker
  (0.86/0.58) and bigger (radius ×2.15, cut ×1.8), and its shape slowly
  morphs over a minute; the TV's `PlasmaView` matches.

- **Dark pool, heavier core lines, Room menu, memory import (2026-11-01, iOS
  5.56 / build 199, TV 9.3)** — the pool under the core is now a soft shade
  of the deep background, not red (Nick's pick), on the iPad/phone and TV.
  `NeuralCore` lines are a hair heavier (`size / 185`, nodes `/ 92`). Room:
  the Room settings button became a **Menu** (Room settings, Wake-up, Memory
  map, Link map, Import memory); full screen ignores the safe area so the
  cards reach the bottom edge. Link map: Core is drawn as JARVIS's own orb
  ("JARVIS", "Core · Optiplex"); APNs and HA-fallback pushes now count as
  core -> device signals (`notifications._traffic`). Memory map: tapping a
  folder (Journal) lists its notes to open; vaults of ≤60 nodes label every
  dot. Vault search scores each heading's section separately (up to three
  per note, `_SECTIONS_PER_NOTE`), so one long note answers from the right
  part. New `POST /api/knowledge/import` (admin token, `{title, text}` up to
  200k chars) saves pasted text to `Jarvis Memory/Imported/<title>.md`
  (`OBSIDIAN_IMPORT_FOLDER`); the Memory tab and the Room menu have an
  Import memory sheet. **Core profile (2026-11-05):** an import is only searched,
  and profile facts go stale one sentence at a time, so
  `memory_extractor.refresh_core_profile` (background, `MEMORY_CORE_MODEL`,
  default newest Sonnet) merges every note under Imported with the dated
  profile facts, newest winning, into ≤4,000 chars of what a close assistant
  just knows (people, birthdays, school, work, home, car, projects, likes),
  saved as `Jarvis Memory/Core Profile.md` (cut at a line if it runs long).
  `memory.render_memory_block` puts it at the top of the memory section every
  turn; it's left out of vault search. It rebuilds right after an import, and
  after facts learned in conversation at most every `MEMORY_CORE_REFRESH_H`
  hours (24; ~2-3¢ a rebuild), started by the next turn that finds it due. It's
  a plain note: an edit stands until the next rebuild.

- **Calmer plasma, red under JARVIS, Memory map, Link map (2026-11-01, iOS
  5.55 / build 198, TV 9.2)** — `PlasmaBackdrop`'s speed bursts are half
  what they were (`Gust(swing: 0.5)`, slower base loops) and a blob only
  stretches while it's actually moving, so a slow one never swings round in
  a blink (that read as a notification in the corner of Nick's eye). The
  Room's core reports where it sits (`PlasmaAnchor`): the backdrop keeps a
  slow pool of the accent red under it and thins the passing blobs there
  (`destinationOut` on their layer), so full red + full blue can't stack
  behind JARVIS and wash him out, without a dark patch. TV 9.2 does the same
  under the header core (`PlasmaView.setAnchor`). iPad Room landscape: left
  card wider (≤400 pt), the core fills its free space, and the lights card
  stretches so the right column ends flush at the bottom.
  New `brain/traffic.py`: a 400-entry in-memory log of every signal between
  `core`, `nas`, `tv`, `iphone`, `ipad`, `mac` and `internet`. Fed by an ASGI
  tap in `app/server.py` (each authorized request = client -> core, and
  since 5.59 its successful reply = core -> client once the body is sent; the
  companion sends `X-Jarvis-Device`, client tokens are named by their token
  name), the voice/home/alert streams as they push (core -> client),
  `status_hub.home_changed` (HA websocket = nas -> core), and a wrapper on
  `requests` / `httpx` installed at server import (core -> NAS or internet,
  classified by `HOME_ASSISTANT_URL`/`PLEX_URL`/arr URLs, `TRAFFIC_NAS_HOSTS`,
  private/Tailscale addresses, `TV_HOST`). `GET /api/traffic/stream`
  (status:read, SSE `traffic` batches every 100 ms, no replay). The iPad's
  Room holds the stream open (`TrafficFeed`) and each signal fires one
  neural-core thought from that device's own neuron (outward from the core
  when Core sends, inward from the outer shell when Core receives); idle
  thoughts slow while it's wired. Memory tab toolbar: **Memory map**
  (`GET /api/knowledge/graph`: root, folders, notes, Profile `##` topics
  weighted by fact count, resolved `[[wikilinks]]`; titles only; a live
  force layout that blooms and settles, pinch/drag, tap for links, Open) and
  **Link map** (`Views/LinkMap.swift`: every endpoint, packets running along
  the links as real signals cross, last six listed).

- **Vivid glass backdrop, still TV plasma, Barn Door fly-in (2026-11-01, iOS
  5.53 / build 196, TV 9.1)** — with Liquid glass on, `PlasmaBackdrop` drops
  the material and the dark wash entirely and its blobs go vivid (opacity
  ×1.4, a bright centre held to 30% then a quicker fade). The TV's
  `PlasmaView` no longer moves: it renders once at full resolution (the
  phone's Reduce Motion frame, t = 40 s, dithered) and just blits it. Barn
  Door on the TV: the big `NeuralView` gets a bloom (wide faint strokes
  under wires, spikes and impulses, thicker strokes, stronger halo), since
  the TV draws at 1080p and the panel stretches it to 4K. Going in, the old
  home (a snapshot taken in `recreate()`) fades off the red one, the core
  flies from the header to the middle growing and bristling, then the
  lockdown screen rises behind it; standing down runs it backwards.
  Timings ignore the TV's 0.5 animator scale (`real()`).

- **Wake-up fixes, Memory view, theme in Room settings (2026-11-01, iOS 5.52 /
  build 195, TV 9.0)** — I'm up (phone, TV, voice) now stops the TV's music;
  the wake-up push is time-sensitive and the alarm screen opens whenever the
  app is open while an alarm rings (see "Wake-up music"). New read-only
  `GET /api/knowledge` (list) and `GET /api/knowledge/note?path=` (one note,
  frontmatter and JARVIS's hidden fact IDs/metadata stripped), admin token,
  only the `OBSIDIAN_INDEX_FOLDERS` notes (`knowledge_vault.list_notes` /
  `read_note`). iPad gets a **Memory** tab (`Views/KnowledgeView.swift`:
  profile memory as "What I know about you", journal, notes; split view with
  light Markdown); the phone opens it from Settings › Memory. Room settings
  has the theme picker (with the Experimenting switches) so the iPad needn't
  leave full screen. Liquid glass: the plasma backdrop drops its material
  frost entirely (cards stay glass), TV wash 60 → 22.

- **Experimenting theme (2026-11-01, iOS 5.45–5.48 / build 188–191, TV 8.5–8.6)** — a
  pickable "visual lab" palette (Spider-Man's colours underneath) whose
  experiments each have a switch under the theme list in Settings
  (`Views/Experiments.swift`, `jarvis.experiment.<name>`). Everything is
  drawn live in Swift (`Canvas` in a `TimelineView`), no video or web views.
  - **Flowing plasma** (default on; `PlasmaBackdrop`): seven red/blue/ice
    radial-gradient blobs on wandering paths, stretched along their travel,
    under a thin material; replaces `JarvisBackdrop` everywhere. Each axis
    is driven by a `Gust`: speed = 1 + three cosines with unrelated 6–31 s
    periods (amplitudes sum to 0.88), integrated analytically, so blobs
    speed up and ease off at random-looking times but never jump or reverse.
  - **Neural core** (default on; `NeuralCore` + `NeuralNetwork`, rebuilt in
    5.47): the reactor becomes a round, layered mind: three Fibonacci shells
    of box-shaped data nodes (16/40/72 at radii 0.15/0.29/0.42, each wired to
    its 3 nearest, axons both ways between shells, each shell swaying out of
    step with the others) inside two tilted orbit rings of 30 nodes, faint
    glass outlines per shell and light in the core. Thinking is driven by
    the house's real traffic (5.62, calmed in 5.63): a `TrafficFeed` signal
    fires a single-branch thread through the shells (outward from Core,
    inward from a device), but each link fires at most once per 5 s, so the
    iPad's steady polling (camera status ~1/s) reads as background. An
    `activity` level (each fired thought +0.12, easing back over ~10 s) sets
    the pace: turn and ring speed (0.3× quiet up to 1×), stray sparks
    (0.2/s quiet up to 1.4/s) and ring-packet speed. Quiet,
    it only thinks on its own every 7–14 s; while `active` (JARVIS working)
    it's busy regardless (a thought every 0.5–1.1 s, 5 sparks/s). Three
    packets circle each ring. Gold impulses (Animal Logic's Age of Ultron
    JARVIS: orange, angular, "made out of code or data"), structure in the
    palette's reactor colour. **Moods** (`NeuralMood`) morph the same nodes,
    core first and rings last (~1–2 s): `watching` (room camera on) is a
    green eye (open pupil, no dark fill since 5.51), the shells becoming pupil rim, inner and outer iris, the
    axons iris fibres, ring 0 the almond eyelids and ring 1 a circle around
    it, thoughts running iris → pupil, the pupil glancing about every
    0.8–3.4 s; `alert` (Barn Door) bristles red: a third of the outer nodes
    stretch into spikes, the mesh fades, the rings become jagged crowns, it
    spins faster and fires along the spikes. During Barn Door the palette is
    `barnDoor`, so `ThemeSync` keeps the replaced pick in
    `jarvis.palette.beforeAlert` and the core stays neural if that was
    Experimenting. `NeuralNetwork.shared` lives outside the views because a
    palette change rebuilds them (`RootView` `.id(palette)`). Marks under
    60 pt keep the classic reactor (and the camera/red-alert orbs).
  - **Orbit orb** (default off; `OrbitOrb`): lit glass sphere with three
    `rotation3DEffect` rings; Neural core wins when both are on.
  Reduce Motion / Low Power get the still frame. Core accepts
  `experimenting` as a shared theme (the TV shows it as Spider-Man), and the
  switches are shared too: `PUT /api/theme {"experiments": {"neural": true}}`,
  returned in `GET /api/theme` and applied by `ThemeSync.pull`. Theme names
  are validated only against `brain/theme.THEMES` (the API used to keep its
  own regex, which refused "experimenting": that was the sync bug). The
  home stream's events carry `look` (`theme.current()["revision"]`, the
  later of the theme pick and the last switch flip), so a switch flipped on
  one device reaches the other at once: the apps skip the 10 s theme poll
  while the stream is up, which is why switches used to lag. New
  experiments go in the `Experiment` enum; Core needs no change.
  - **5.48 / TV 8.6**: any theme change (Barn Door engaging or standing
    down) crossfades the whole app over 1.4 s (`RootView` `shownPalette`,
    `.transition(.opacity)`) instead of cutting. `theme.current()` and the
    glance carry `core` (`"neural"` when Experimenting is the pick, or what
    Barn Door replaced, and the `neural` switch isn't off), and the TV draws
    the same neural core (`NeuralNet`/`NeuralView`, a Java port), spiky
    full-screen for Barn Door; see tv/README.md.
  - **5.49 / TV 8.7**: **Liquid glass** experiment (`glass`, default on):
    every card and panel goes through `View.jarvisSurface(in:)`
    (Experiments.swift), which is the panel gradient normally and frosted
    glass under Experimenting: since 5.50 on iOS 26+ that's only
    `glassEffect(.regular)`, so it follows the phone's Liquid Glass setting
    (Nick's is Clear) and Reduce Transparency; older iOS gets a material
    stand-in. 5.49 crashed at launch whenever the switch was off or the
    theme wasn't Experimenting: a find-and-replace turned the fallback into
    `content.jarvisSurface(in:)`, which recursed forever. The plasma backdrop's own frost thins
    (material 0.35 → 0.15) while it's on. Theme crossfade no longer dims:
    a `JarvisBackdrop` sits under the two fading screens while it runs.
    Core's theme/glance add `lab`, the switches actually showing with
    defaults filled in (`brain/theme._LAB_DEFAULTS` mirrors
    `Experiment.defaultOn`); the TV draws `PlasmaView` (rendered at 1/8 size
    and stretched: full-size gradients ran the TCL at ~7 fps) and frosted
    `glassCard`s from it. TV Barn Door now fades its overlay in over the
    home screen and out on stand-down (no dip to black), and every neural
    core on screen asks for the same mood (`HomeActivity.mood()`); the
    header's thinking core used to tug the shared mind back from spiky.

- **Voice ID backbone (2026-11-01, untested on the Optiplex)** — who's
  talking decides what a voice may do. `brain/voice_trust.py` holds the
  turn's speaker (a `contextvars` value, copied into the agent's parallel
  tool workers); outside a voice turn it's Nick, so the phone and desktop
  are unchanged. The room voice (`speech/room_voice.py`) asks
  `brain/speaker_id.py` (sherpa-onnx + NeMo TitaNet-small, ~20 ms per
  command) whether the clip matches Nick's enrolled voiceprint
  (`memory/voiceprint.json`, vectors only, never audio); client-token chats
  (the TV remote's mic) are guests unless in `VOICE_TRUSTED_CLIENTS`. A
  guest keeps lights, scenes (Peace etc.), timers, music and questions, but
  `execute_tool` refuses `voice_trust.LOCKED_TOOLS` (memory and profile
  writes, standing orders, `update_setting`, voice shortcuts, Barn Door,
  self-repair and automation applies, `voice_id`), the memory extractor
  skips the turn, staged fixes can't be approved, and the turn context tells
  the model not to take persona/address instructions from them. Off by
  default (`VOICE_ID=0`); with nothing enrolled everyone is Nick; once
  enrolled, a missing model or a clip under 0.8 s fails closed. Setup on
  the Optiplex: `.venv/bin/pip install sherpa-onnx`, then
  `mkdir -p models/speaker && curl -L -o models/speaker/nemo_en_titanet_small.onnx
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx`
  (the tag really is spelled "recongition"), set `VOICE_ID=1`, restart, then type
  "learn my voice" in the phone app and say five sentences to the room mic.
  On Mac test clips, TitaNet scored the same voice 0.90+ and a different one
  under 0.25; CAM++ and ResNet34 separated far worse.

- **Room camera (2026-10-31, iOS 5.38 / build 181)** — the iPad is the lens
  (front camera), Nick's phone the only viewer. A "Room camera" button on
  the phone's Hub tab opens a live sentry view; while it's open the phone
  heartbeats `POST /api/camera/view` and pulls `GET /api/camera/frame`
  (~8 fps), and the iPad, polling `GET /api/camera/status`, runs the camera
  and uploads downscaled JPEGs. Close the view and the lens goes off within
  ~6 s. Deliberately visible: the iPad shows a green JARVIS orb
  (`CameraWatchOrb`, the red-alert shape in green) whenever it's capturing,
  and iOS's green dot is on. The phone view has a Barn Door toggle and a
  Kill button; "Jarvis, kill the camera" (quick command) and the
  `kill_camera` tool lock the lens off for 5 minutes no matter who or where
  (`POST /api/camera/kill`). Frames live in memory, never on disk. No audio.
  (`brain/camera.py`, `Views/RoomCamera*.swift`.)

- **Fewer false wake-ups (2026-10-31)** — the room voice fired the instant
  one audio frame crossed the wake score, so TV chatter or a passing phrase
  could wake JARVIS with nobody saying "Jarvis". It now needs the score over
  the threshold on `WAKE_CONSECUTIVE` frames in a row (default 2) and logs
  the firing score; the default `WAKE_THRESHOLD` is raised 0.5 -> 0.6. Both
  are live-tunable in `.env`. (`speech/room_voice.py`, `speech/wake.py`.)

- **Weather beside the date on the iPad (2026-11-02, iOS 5.67 / build
  210)** — the Room tab's date line shows the weather icon and temperature
  from `GET /api/glance` (the Mac Desk's snapshot), refreshed every 10 min
  while the app is open; hidden while unknown. A full "weather today" screen
  is on hold in FUTURE_SESSIONS.md (rich answer screens).

- **TV power: iPad square and instant voice (2026-10-31, iOS 5.36 / build
  179)** — a square power button beside "TV remote" on the iPad's Room tab
  (`TVPowerButton`, amber while the TV is on; state from the new `GET
  /api/tv/power`, every 15 s and 4 s after a press). "TV off" / "turn on
  the TV" / "turn the television off" to the room voice now go straight to
  `tv_remote.power` as a quick command (jingle, no model turn); typed or
  phone requests still use the `tv_power` tool.

- **Less polling, TV stays reachable (2026-10-31, iOS 5.33 / build 176, TV
  8.4)** — measured 1,300 app requests to Core in 10 minutes (portfolio,
  entities and theme each about every 3 s across the devices). Now the
  apps skip entity and theme polls while `/api/home/stream` is connected
  (each pulse carries the theme; changes land in about 0.3 s instead of the
  next 2-10 s poll), the iPad's Room tab no longer drives the Hub-only
  sections (torrents, services, repairs, alerts, portfolio, media), and the
  portfolio refreshes every 5 s only in US market hours (once a minute
  otherwise). Core: a flapping TV entity (`media_player`/`remote`; never
  lights, switches or fans) is muted from the inbox for 30 minutes after 5 changes in 10 minutes; the wake-up alarm reads only the
  TV's entity. The goodnight routine passes its own `now` to the alarm
  calls (the tests had started failing once their date passed). TV 8.4:
  `StayReachable` holds a wake lock and a Wi-Fi lock so the TV stays on the
  network in standby (see `tv/README.md`, TCL quirks).

- **iPad big level panel for everything; Barn Door orb and TV (2026-10-31,
  iOS 5.32 / build 175)** — on the Room tab a plain up/down drag on a light
  or the fan (landscape; portrait still holds first, the panel scrolls)
  opens the big level panel and sets brightness/speed; hold and let go
  opens the same panel to use (`LevelPanel`: drag its bar, power, Rainbow,
  whites, colours, any colour; the fan's Fresh/Nature), replacing the
  light sheet and `FanSheet` there (the phone keeps `LightSheet`). During
  Barn Door the orb everywhere in the app is `RedAlertMark`, a SwiftUI port
  of the TV's `RedAlertView`. Barn Door now turns the TV back off at
  stand-down if it was off when engaged (`brain/protocols.py`,
  `memory/barn_door_tv.json`); a TV that was on stays on.

- **iPad Room: big level bar, no stray tab swipes (2026-10-31, iOS 5.31 /
  build 174)** — holding a light or fan tile now opens a tall level bar over
  the panel (`LevelOverlay`, 4 pt per percent) while the same finger drags.
  The volume slider and tile drags hold `SwipeLock`, so they no longer
  swipe to the next tab; with Room's **Full screen** on, the iPad stays on
  Room (no swiping to other tabs; turn Full screen off in Room settings to leave).

- **Wake-up backup ring; iPad volume slider, hold-and-drag dimming, fan speed
  (2026-10-31, iOS 5.30 / build 173)** — the alarm fired at 09:00 but the TV
  was off the network (see "Wake-up music"): Core now wakes the TV through
  HA and rings the room speaker if it stays off. iPad Room tab: a **JARVIS
  volume** slider in the clock panel (room speaker 0-10, `GET`/`POST
  /api/room/volume`, a chime on release, sets the night level in quiet
  hours); **hold a light or the fan tile and drag up/down without lifting**
  to set brightness/speed (the tile fills to the level, a tick every 10%,
  steps sent 4×/s, 0 = off; `Views/LevelDrag.swift`); hold and let go still
  opens the light sheet, and the fan now has its own `FanSheet` (power,
  speed, Fresh/Nature). Core: `/api/home/entities` gives fans a `fan` block
  (percentage, step, preset_modes, preset_mode) and `POST /api/home/fan
  {entity_id, percentage?, preset_mode?}` (`status_hub.set_fan`).

- **Profile memory cached (2026-10-29, iOS 5.16 / build 159)** —
  `brain/memory.py:_load()` reuses the parsed `profile.json` until its
  path, mtime or size changes, handing each caller a deep copy (they mutate
  then `_save`). Hand edits and other processes' writes still show up.
  Measured on the Mac's 14-fact profile: 0.03 → 0.01 ms per load (small;
  grows with the profile). Tests: `tests/test_memory_cache.py`.

- **"What scenes do I have?" and "save this as a scene" (iOS 5.15 / build
  158)** — `list_scenes` and confirm-gated `save_scene` tools
  (`app/status_hub.py`: `capture_scene`, `describe_capture`, `save_scene`).
  The proposal reads like "save the room as a new scene 'Reading' (3 lights
  on, 2 off, fan at 35%)"; the room captured at proposal time is what gets
  written. Not run against live HA yet. Also: `tests/__init__.py` points
  `tool_health._FILE` at a temp dir, so the suite (`python -m unittest`)
  no longer writes the real `memory/tool_health.json` (12 modules did).

- **Indexer incidents quote the real error; JARVIS can switch mirrors (iOS
  4.9 / build 131)** — Found 2026-10-28: 1337x.to returned `403 Forbidden,
  error code: 1006` (Cloudflare banning the VPN exit IP) and JARVIS called it
  a "429 rate limit". Incident reports now read Prowlarr's log, match each
  backed-off indexer's newest HTTP error by host, and quote the status and
  Cloudflare code with what it means (`nas_stack.explain_http_error`:
  1006-1008 IP ban, 1009 country, 1010 browser signature, 1015 rate limit,
  1020 firewall), in the suspected cause, an "Indexer errors" section and the
  chat prompt. New stack fix `switch_indexer_mirror` (pass `indexer`): tests
  each of Prowlarr's offered mirrors with `/api/v1/indexer/test` (saves
  nothing), saves the first that passes, and checks the backoff cleared; if
  none pass it says the ban is on the VPN exit. Confirm-gated like every
  stack fix. 1337x was moved to `https://1337x.st/` by hand the same day.
- **TV 7.5: Barn Door takes the screen** — while the protocol is engaged the
  TV home is just a giant reactor, "BARN DOOR PROTOCOL" and the time.
- **Barn Door Protocol; McQueen redone (TV 7.4, iOS 3.5 / build 117)** —
  `barn_door_protocol` turns the room red (lights snapshotted and restored on
  stand-down) and the phone and TV to a hidden red-alert theme. McQueen now
  sits on asphalt charcoal (lightning-yellow accent, red reactor) with
  "Ka-chow, sir." and a ⚡ on the date on phone and TV.
- **TV 7.2: the bottom row is all wordmarks** — Wake-up, Continue and
  Settings use Plex and Spotify's bold lower-case wordmark with one accent
  character, every wordmark and label on one line, and their small print
  only shows on the highlighted tile.
- **Themes trimmed and two added; tiles read like wordmarks (TV 7.1, iOS 3.2
  / build 114)** — Nick kept Arc, Hot Rod, Malibu, Crimson and Mark 85 (his
  favourite) and asked for Spider-Man and Lightning McQueen; Mark III,
  Graphite and Stealth are gone (`brain/theme.py` maps a stored one to Hot
  Rod / Arc). TV bottom-row tiles put the headline over the label like the
  wordmarks.
- **TV 7.0: one bottom row, no Plex PIN robot** — every bottom-row tile
  (Plex, Spotify, Wake-up, Continue, Settings) shares one look and width,
  spread evenly; Continue's list opens on the newest and lands on Plex's
  Resume (tested on a movie and an episode). Plex keeps Nick signed in, so
  the PIN auto-entry (`PlexAutoLogin.run`) is removed; `PlexWatch` keeps
  pressing Watch for "play ..." voice requests.
- **JARVIS tunes and teaches itself (room voice)** — (1) the room voice
  reads `WAKE_THRESHOLD` and `MIC_RMS_THRESHOLD` live (`speech/wake.py
  wake_threshold()`, `speech/audio.py rms_threshold()`), and
  `VOICE_SILENCE_END_MS`, `VOICE_FOLLOWUP_S`, `VOICE_START_TIMEOUT_S`,
  `VOICE_SHORTCUT_AFTER` joined the `update_setting` schema, so "you never
  hear me" / "you cut me off" changes apply to the next "Hey JARVIS"; the
  persona tells Claude how to map complaints to those knobs. (2)
  `brain/voice_shortcuts.py`: a room-voice phrase Claude answers with the
  same single home/music action (`LEARNABLE`) twice becomes a shortcut the
  room voice runs itself (no model); a failing one is dropped; the
  `voice_shortcuts` tool lists/forgets them; the runtime's inbox notes each
  one learned. `JarvisAgent.turn_tools` / `runtime.last_turn_tools` expose
  the finished turn's tools. (3) `tts.speak(cache="auto")` keeps a reply's
  audio from the second time JARVIS says it (`memory/tts_cache/said.json`
  counts, newest 500).
- **"Jarvis, goodnight" (room voice)** — `speech/goodnight.py`, no model
  involved: "Goodnight, sir. Your alarm's set for 7:00. Is that fine?" →
  yes / silence keeps it; a time ("6:30", "make it eight", "half past six")
  moves the coming morning only (`wake_alarm.set_alarm(hhmm, date)`); "no
  alarm" / "skip it" / "sleep in" skips that morning; a bare "no" asks
  "What time, sir?" once. With no alarm set it asks "Want one?". Then HA's
  Goodnight scene runs (in the background) as he says "Sleep well, sir."
  After midnight "the morning" is today. The answer is heard without the wake
  word (the mic queue is drained first so JARVIS doesn't hear itself).
  Routine lines use `tts.speak(..., cache=True)`: audio kept in
  `memory/tts_cache/` (per voice/model/speed, newest 200), so from the second
  night they start instantly.
- **Faster home calls (2026-10-28 cleanup)** — HA's service catalogue
  (`get_services`, ~60 KB, ~45 ms) is cached for 5 minutes and re-read once
  when a service isn't in it; `status_hub.home_control(..., settle=False)`
  lets the room voice skip the up-to-2 s wait for HA to report the new state
  (the apps still wait, for their switches); scene activation validates
  against the cached list. Wake-word stripping lives in `speech/wake_words.py`.
- **Room voice: "Hey JARVIS" on the Optiplex, shown on the iPad (iOS 5.0 /
  build 135)** — `speech/room_voice.py` runs inside Core when `ROOM_VOICE=1`
  (started from the server lifespan, same runtime as the apps). openWakeWord
  `hey_jarvis` → soft "listen" sound → record to ~0.55 s of silence while a
  tiny Whisper (`VOICE_LIVE_MODEL`) captions live → final transcript
  (`WHISPER_MODEL`, base.en recommended on the i5-6500T) →
  `speech/quick_commands.py` runs plain scenes / on-off / "X to 40%" / "fan to 30" (fan speed, `status_hub.set_fan`; a bare "fan" never means the fan light) straight
  through `status_hub` with a jingle (`speech/room_sounds.py`, no words);
  anything else is `runtime.respond(text, spoken=True)`, which adds a
  "spoken reply: one short sentence, 'Done.' after a home action" hint to the
  turn context; a bare "Done." after tools becomes the jingle. A spoken
  answer opens a short follow-up window without the wake word. Wake word at
  the end works ("game time, Jarvis") via a 2 s preroll. State goes to
  `brain/voice_presence.py` (not the event bus: captions would flood its
  history) and out on `GET /api/voice/stream` (SSE) / `GET /api/voice/state`
  (`status:read`). On the iPad's Room tab (5.62) the conversation shows in
  place: the side reactor grows (1.12×) with a steady glow and the lights &
  scenes column becomes `RoomDialogue` — what Nick said, smaller, then the
  reply in big type; it lingers 2.5–8 s after (longer replies longer), tap
  to put away. Anywhere else (another tab, a sheet, movie mode) `VoicePresence`
  (`VoiceOverlay.swift`) shows its window above everything — core mark,
  "Listening…", the live transcript, then the reply or a tick. Either way it
  wakes the night screen. Speaking straight from `idle` is an announcement
  (a download finished, a timer, a reminder): `voice_presence` clears last
  turn's transcript so only the announcement's words show. Checked in the iPad
  mini simulator against a local Core cycling fake states; the Optiplex mic
  and speaker path is untested.
- **Smart-speaker room voice (2026-10-28, untested on hardware)** — before
  scenes and the model, `speech/quick_answers.py` answers instantly: the
  time, the date, the weather right now (`status_hub.weather_status`;
  "tomorrow"/"forecast"/rain questions still go to the model), timers ("set
  a pasta timer for 10 minutes", "an hour and a half timer", "how long's
  left", "cancel the pasta timer"), volume ("volume 4", "70 percent",
  "louder", "quieter", "max volume", "what's the volume"), Spotify ("pause",
  "resume", "next", "previous", "what's playing", "play Back in Black"; not
  "play ... on the TV" or episodes/movies), every light and lamp on or off
  ("turn off all the lights", "lights out"; on never includes the fan light,
  and switches like the Wii U are never touched), today's calendar ("what's
  on my calendar today"), "stop" and "say that again". "Good morning" (`speech/good_morning.py`) speaks the time,
  weather and today's first calendar item, and does what I'm up does
  (snooze cleared, TV wake screen closed, good-morning lights). The room
  voice now owns `runtime.pop_due_reminders()` in server mode: a finished
  timer beeps (`alarm`) and says "Sir, your pasta timer is done", ringing
  every 3.5 s until "Hey JARVIS"/"stop", the app's dismiss, or
  `ROOM_ALARM_RING_S`; reminders and broadcasts get a `chime`; other
  announcements are skipped in quiet hours (`ROOM_ANNOUNCEMENTS=0` mutes
  them). Barge-in: the wake word while JARVIS talks stops him and listens
  (`VOICE_BARGE_IN`). `speech/room_volume.py` is volume by `amixer` on the
  `AUDIO_OUTPUT_DEVICE` card plus night mode (`ROOM_QUIET_HOURS` 22-7 at
  `ROOM_NIGHT_VOLUME` 4; the day level is remembered and restored). The
  companion's Settings has a Night volume stepper (`POST /api/room/volume/night`,
  `GET /api/room/volume` reports `night_level`) that edits the night level at
  any hour; it applies at once only inside quiet hours. On the iPad it's also
  in the Room's Menu › Room settings, so it works without leaving full screen.
  Settings › Laptop (under Connection) shows the battery of the laptop Core
  runs on (`GET /api/host/battery`, `app/host_battery.py`, read from
  `/sys/class/power_supply`: percent, charging/discharging/"not charging" =
  held at the 50% charge limit, plugged in, host, uptime); the row shows the
  percent and the page refreshes every 10 s while open.
  Broadcasts: `POST /api/voice/announce {"text"}` (`chat` scope; 409 when the
  room voice is off) and the `announce_in_room` tool ("tell my room dinner's
  ready" from the phone).
  Hush: `POST /api/voice/hush` (`chat` scope) ends the current exchange; the
  iPad calls it when the conversation is tapped (iOS 5.87).
- **Nick's room scenes in HA, Peace on the iPad (iOS 4.12 / build 134)** —
  made 2026-10-28 through HA's scene config API, to Nick's spec: Good
  Morning, Study, Movie, Game Time, Goodnight, Peace (plus Rainbow). Rules:
  never `switch.wii_u` (its own overnight schedule; an always-powered Wii
  burned out before); the fan light `light.nicks_fan` is only ever turned
  off. Rainbows are the DIY dropdowns. The fan's own RGB rainbow isn't
  exposed by the Tuya integration, so no scene sets it. I'm up runs
  `scene.good_morning` when it exists. The iPad Room tab has a Peace
  button (shown when `scene.peace` exists) for heading out.
- **Scenes you can leave, fan ring, movie mode, cloud STT (iOS 5.14 / build 157, TV 8.3)** —
  `/api/home/scenes` adds `current`: the last scene unless it was a resting
  one (`RESTING_SCENES`), so after Normal/Peace/Goodnight nothing is lit.
  `POST /api/home/scenes/activate {"toggle": true}` (every app tile): the
  current scene, untouched since (`status_hub._scene_matches`: on/off and
  dropdown options vs HA's saved scene config), leaves to `scene.normal`
  (bedroom light on, the rest off; added by `ops/optiplex/room_fixups.py`)
  and answers `left: true`; changed since, it runs again. iPad Room shows a
  quiet "Current scene: X" beside Scenes when X isn't a tile, and Movie turns
  the panel into a dark movie remote (clock, playback, volume, bedroom
  light, TV remote) with "Back to panel". `RING_LIGHTS` (the fan light):
  the apps only ever turn it on in colour (its RGB ring), never white; the
  light sheet hides white. Put it on the iPad in place of the Wii U via
  `HOME_DASHBOARD_ENTITIES`. TV remote: a 2x2 quick launch (Plex, YouTube,
  Spotify, Settings: `app_*` keys, HA `remote.turn_on` with `activity`).
  Barn Door: theme and TV wake go first, lights in parallel; the home
  stream's events carry `theme` and the TV follows them (`ThemeStream`), so
  the red screen no longer waits on its 5 s theme poll. Cloud STT:
  `STT_PROVIDER` (speech/stt.py). `ios/install.sh` builds Release once and
  installs on every connected iPhone/iPad.
- **Live room sync, iPad polish, scene fix (iOS 5.13 / build 156)** —
  `GET /api/home/stream` (SSE, `status:read`) sends a `home` event whenever
  a `HOME_DASHBOARD_ENTITIES` entity or any scene changes in HA
  (`status_hub.home_changed`, called from the runtime's HA live feed). The
  phone and iPad (`HubViewModel.startHomeStream`, while the Hub/Room is
  showing) refetch lights and scenes ~0.1 s later instead of waiting on the
  5 s poll; the Room tab's own poll is now a 5 s fallback. The TV shows no
  light state, so it needs nothing new. iPad Room: battery above the clock
  (top left), the time stays on one line, a 200 pt reactor, a bigger Peace
  square, scene icons scale to fit their circle, and the scenes row is just
  the three favourites (others are voice-only). The panel goes dark 3 s
  after every light goes out from anywhere (phone, voice, wall), not only
  after Peace/Goodnight run on the iPad. `ops/optiplex/room_fixups.py` (run
  once on the Optiplex) adds `direction`/`preset_mode` to the HA scenes that
  run the fan (HA 2026.10 otherwise calls `fan.set_direction` with None:
  "string value is None at 'direction'") and renames the room's entities
  (Strip Lights, Desk Lights, Bedroom Lights, Nick's Fan, Fan Light, Lamp,
  Avengers Tower, Scentsy, Wii U; entity ids unchanged). The lamp is a Govee
  light, `light.lamp` (2026-11-03; it replaced the `switch.lamp` Tuya plug),
  so the apps give it the same light sheet as the others.
  `ops/optiplex/swap_lamp.py` made the swap: it reloads the Govee
  integrations, renames the new lamp `light.lamp` (its LAN twin
  `light.lamp_lan`, added to `LIGHT_TWINS`), disables the old plug as
  `switch.old_lamp_plug`, and repoints every scene, automation and `.env`
  entry from `switch.lamp`. The fan's light is
  one Tuya light with modes white/colour/scene/music: colour mode
  (`light.nicks_fan` with `rgb_color`/`hs_color`) is likely the RGB ring;
  scene mode (its rainbow) isn't reachable through HA's Tuya integration.
- **iPad panel and phone glance redone (iOS 5.12 / build 155)** — iPad Room
  fits one landscape screen: clock + battery (full screen hides the status
  bar), the reactor, wake-up / next up / timers, TV remote and a **Room
  settings** sheet (full screen, keep awake, night screen, tap-to-wake
  light, back to Room, which scenes get tiles); lights with a small Peace
  square (waving hand) in the top-right; scenes are three favourites
  (`room.favoriteScenes`, default Game Time, Movie, Study) plus a menu with
  the rest that shows the current one. Phone Home doesn't scroll: a 170 pt
  reactor, next up / to-dos, a Focus-style scene picker (`SceneFocusMenu`)
  and a one-line portfolio strip. Three quick taps on the reactor (iPad
  Room, phone Home) engage Barn Door Protocol, three more stand it down
  (`POST /api/protocols/barn-door`); the phone’s one-tap briefing was removed in 5.76.
  `JarvisCoreMark` now computes its angles from a `TimelineView` clock (the
  `repeatForever` animations stacked and sped up or jumped). A long press
  that opens the light sheet no longer also toggles the light
  (`HoldSuppressor`). Wall switches show up within ~2 s: HA's live feed
  clears Core's entity cache (`runtime._invalidate_app_home_cache`, only for
  `HOME_DASHBOARD_ENTITIES`) and Room/Hub/Desk poll entities every 1.5-2 s.
  Quick add has a quality-profile picker (`GET /api/media/profiles`,
  default Standard for movies, TV for shows). Barn Door now turns the TV on
  (`BARN_DOOR_TV`) and turns the fan light off instead of red.
- **Agent usage and cost (iOS 6.4 / build 246, branch `phone-reorg`)** —
  every Messages API response is logged to `memory/usage.db` by
  `brain/usage.py` (`record` / `record_response`): JARVIS's rounds
  (`agent._log_round`), vision, the memory keeper and core profile, the study
  coach, every subagent (by spec name) and Donna. Costs are estimates from
  list prices in `usage._PRICES` (cache reads/writes, Haiku 5.5's >100K tier,
  $10 per 1,000 web searches); update that table when prices change.
  `GET /api/agents/usage?days=N` (0 = all) → `agent_roster.usage_report`,
  mapping ledger names to agents by id, then job prefixes ("Other" for the
  rest). The Agents page's $ button opens Usage: Today / Week / 30 / 90 days /
  All, the total, and each agent's cost, calls, tokens and models. Donna
  joined the roster. Tests isolate the ledger (`tests/__init__.py`).
- **Donna mode (iOS 6.2 / build 244, branch `phone-reorg`)** — a private,
  text-only counsellor walled off from JARVIS (`brain/donna.py`,
  `memory/donna.db`; JARVIS's memory, history, extractor, dashboards and TV
  never see it). Entry is hidden: tap twice, then press and hold the JARVIS
  logo on the Chat tab (title or the empty-chat orb; a plain hold no longer
  opens her, since 6.9); the app fades to rose, the rings unwind
  into one line, and the tab bar is covered (`DonnaPresenter`, overlay in
  `RootView`). Hold the line or tap the chevron to leave. Memory works like a
  therapist's notes: each request sends her prompt, a per-session context
  block (case file, last 3 session notes, open follow-ups, recent moods) and
  this session's messages only, all prompt-cached. Ending a session (menu, or
  30 min idle, written before the next one starts) has Haiku write a note and
  refresh the case file. Tools: recall_notes, update_case_file, add/resolve
  followup, log_mood, schedule_checkin (the phone schedules them as local
  notifications), and web_search/web_fetch limited to `donna.ALLOWED_DOMAINS`.
  Menu: End session, What Donna knows, Bring old chats over (paste → case
  file via `POST /api/donna/import`). Erasing her memory is deliberately
  buried (6.3): a small link at the very bottom of What Donna knows, with a
  confirmation. API: `GET /api/donna`,
  `POST /api/donna/message|end|import`, `GET|DELETE /api/donna/memory`.
  Replies are not streamed: one `POST /api/donna/message` returns the whole
  answer. 6.17: when that request dies (app backgrounded, network handoff,
  timeout) Core still finishes and saves her reply, so the phone and Mac
  poll `GET /api/donna` (up to ~1 min) for the answer after his message
  before calling it a failure, and the phone re-reads the session whenever
  it returns to the foreground.
  Conversation on Sonnet (`DONNA_MODEL`), notes on Haiku (`DONNA_NOTES_MODEL`).
  On the Agents map since 6.4 (cost only, never content).
  6.9: every message of Nick's reaches her with a `[time · gap]` tag
  (`_timed_history`; derived from stored times, so the cache holds and the
  stored text stays clean), so she can tell back-to-back replies from a
  4-hour absence and late night from morning; she now offers check-ins
  when something hard is coming up (none had ever been scheduled, because
  she only scheduled when asked and didn't know the clock). The composer
  rebuilds itself whenever it empties, fixing sent text reappearing and a
  long draft's height not collapsing.
- **Phone reorganization + Lists (iOS 6.0–6.1 / builds 242–243, branch `phone-reorg`)** —
  phone tabs are Media · Control · Home · Chat · Settings (Home centred and
  default; "Hub" renamed Control on every device). 6.1: Home fits one screen
  (160 pt orb; scene picker and a Charles Schwab half-tile share the last row);
  Settings is glass cards. Home: reactor, a **Lists**
  row of three separate tiles (To do = open reminders only; Shopping; 
  Maintenance = due count), a one-line next-up strip (next assessment, else
  next event → Control), scenes, portfolio. Lists push in Home's stack
  (`ListRoute`, `MaintenanceRoute`); long-press a tile to jump into adding.
  Shopping (`ShoppingViews.swift`): type to see saved items with photos and
  tap to list one, Return lists an exact match or saves a new item, the
  editor takes a photo (camera or library, shrunk to 1200 px JPEG), note,
  store and quantity; ticking off removes it with an Undo; Saved items is a
  searchable photo grid. Control: needs-attention, repairs, camera + TV
  remote side by side, switches, then Now (reminders dropped from Now; they
  live in To do). Maintenance list is sectioned Due / Coming up / Log.
  Settings groups (Core row → Connection; Appearance;
  Routine: wake-up, study brief, night volume; JARVIS: agents, memory;
  Accounts: Schwab; About) with each old panel on its own page.
- **Desk on the Mac (iOS 5.6 / build 149)** — on the Mac, Room and Home are
  replaced by **Desk** (the default tab): a header with the reactor (tap
  for a briefing), greeting, live clock, the system/storage/attention chips
  and an "Ask JARVIS anything…" bar that sends straight to Chat; then
  Today (weather and the 20-item agenda from `GET /api/glance`, next
  assessment and to-dos, Now, maintenance), House (alerts, repairs, scenes,
  switches, TV remote, portfolio) and Machines (request, drive, transfers,
  media, services, links) columns, two columns under 1180 pt. Glance is
  fetched only while Desk exists (`HubViewModel.wantsGlance`, every 30 s).
- **JARVIS on the MacBook (iOS 5.5 / build 148)** — the companion app now
  also builds for Mac Catalyst: the iPad layout in a window (min 900×640),
  Room tab first, wall-panel features off (`RootView.isMac`), a Go menu
  (⌘1–⌘6) and ⌘, for Settings. `JarvisCompanion-Mac.entitlements` adds the
  sandbox, network client and mic; push uses the same bundle ID. Install
  steps in `ios/JarvisCompanion/README.md` ("Running on the Mac").
- **iPad tap-to-wake light, timers, back to Room (iOS 5.4 / build 147)** —
  a tap on the night screen also turns on the Room tab's "Tap the dark
  screen to turn on" light (default `light.bedroom_lights`; never the fan
  light; "Just the screen" for none). Goodnight or Peace run anywhere
  (voice, phone, HA) sleeps the iPad too, via the scenes' `last`. Running
  timers count down on the clock panel; one going off wakes the screen.
  "Back to Room after 3 min on another tab" (on by default; Chat is
  exempt) slides the iPad home after three minutes without a touch. Core:
  `MicStream` falls back to the device's own rate/channels and converts to
  16 kHz mono when a raw device (the CMTECK `hw:0,0`) refuses 16 kHz.
- **iPad as a room panel (iOS 5.3 / build 146)** — the Room tab's Peace
  button moved to the top of the right-hand column; the left column gains
  the JARVIS reactor (`JarvisCoreMark`, glow lifts while the room voice is
  active) and a **Full screen** toggle (hides the tab bar, status bar and
  home indicator; swipe still changes tabs). Running Peace or Goodnight
  from the iPad brings the night screen up 3 s after the lights read off,
  day or night and without Keep screen on (still needs "Dark at night");
  a tap or any light coming on cancels it. Core's room voice now logs
  `Mic open: <device>`; set `MIC_DEVICE=CMTECK` on the Optiplex (left
  empty it opened the onboard jack, so "Hey JARVIS" never heard anything).
  A missing mic retries every 30 s, and each retry first rescans audio
  devices (`speech/audio.py:rescan_devices`; PortAudio only lists them at
  startup), so a mic plugged in after Core starts is picked up without a
  restart (2026-11-10).
- **Room rainbows: lamp in the desk scenes, the fan ring follows the strip
  (2026-11-04, iOS 5.78 / build 221)** — `ops/optiplex/room_rainbows.py`
  (run on the Optiplex) gives every HA scene that turns the desk lights on
  the lamp at 100% in its rotating Rainbow (`select.lamp_scene`), drops
  Movie's "fan light off", renames a Smart Life Tap-to-Run that puts the fan
  ring in its multi-colour scene mode to `scene.fan_light_rainbow` (HA's
  Tuya integration can't set that mode itself; Nick makes the Tap-to-Run),
  and adds HA automation `automation.fan_light_follows_strip` (strip off→on
  runs that scene, strip off turns the fan light off). `LIGHT_RAINBOWS`
  now takes `light=scene.x`; the fan light's Rainbow button appears once
  the scene exists. Barn Door turns the ring red with the strip (it's in
  `BARN_DOOR_LIGHTS`' default now, no longer `BARN_DOOR_OFF`), pauses the
  automation while engaged, and on stand-down the snapshot puts the ring
  back; if it was on with the strip, its rainbow runs again
  (`memory/barn_door_fan.json`) and the automation is re-enabled. Nick lifted the "never turn the fan light
  on" rule for this; white mode is still never sent. iPad Room fix: the
  Hub tab stops the shared polling when the app leaves the foreground but
  only restarts it if Hub is showing, so the Room came back with no scene
  or stream refresh; Room now restarts it on becoming active.
- **Rainbow in the light sheet, on phone and iPad (iOS 4.11 / build 133)** —
  lights in `LIGHT_RAINBOWS` (default: Nick Strip Lights → its DIY "Real
  Rainbow", Desk Lights → "New Rainbow", via `select.<light>_diy_scene`;
  the Govee lamp → its built-in rotating "Rainbow" via `select.lamp_scene`)
  report `light.rainbow: true`; `POST /api/home/light {"rainbow": true}`
  turns the light on and picks that DIY scene. The sheet shows a Rainbow
  button; the phone's light tiles (Hub/Home quick actions) now open the same
  `LightSheet` on long-press. HA also has `scene.rainbow` (made 2026-10-28
  through HA's scene config API) doing both lights at once.
- **Light brightness/colour and the iPad night screen (iOS 4.8 / build 130)**
  — `/api/home/entities` now gives each light a `light` block (dimmable,
  color, temperature, brightness_pct, rgb, kelvin, min/max kelvin) and the
  payload carries `sun` (`sun.sun`'s state). `POST /api/home/light
  {entity_id, brightness_pct?, rgb?, kelvin?}` sets one allowlisted light
  (`status_hub.set_light`). On the iPad, long-press a light tile for
  `LightSheet`: brightness (sent when the finger lifts), whites within the
  bulb's kelvin range, eight colours and a colour picker. Night screen
  (Room tab, "Dark at night when the lights are off", needs Keep screen on):
  after sunset with every light and lamp off for 45 s, a black full-screen
  cover at brightness 0; a light coming on (seen within the 5 s poll),
  sunrise, or a tap restores the saved brightness. iPads give apps no
  ambient-light reading, so the room's lights stand in for one.
- **iPad Room tab (iOS 4.7 / build 129)** — the same universal app; on an
  iPad (`UIDevice` idiom `.pad`) a **Room** tab comes first and is the
  default: big clock and date, next wake-up (opens the editor) and next
  calendar item, TV remote, a "Keep screen on" toggle (idle timer off while
  Room shows), big tiles for the `HOME_DASHBOARD_ENTITIES` lights and
  switches, and the HA scenes. Side by side in landscape, stacked in
  portrait (iPad mini). `Views/RoomView.swift`; no new Core endpoints.
  Checked on the iPad mini simulator against the live Core (2026-10-28).
- **Scene picker on the Home tab (iOS 4.6 / build 128)** — `GET
  /api/home/scenes` lists every *saved* Home Assistant scene (those with an
  `id` attribute; `scene.create` snapshots like `barn_door_before` stay
  hidden) plus `last`, the one run most recently (a scene's state is its
  last-run time). `POST /api/home/scenes/activate {"entity_id"}` runs one,
  only if it's in that list (`status_hub.activate_scene`, via
  `call_discovered_service`). The app shows them like Focus modes: a grid of
  tiles, the last-run one lit; SF Symbols guessed from the mdi icon/name.
  New scenes made in HA appear within ~15 s. Scenes live in HA, not JARVIS:
  JARVIS protocols (Barn Door) are for things that also touch the TV, theme
  or music.
- **Good-morning lights (Core, TV 8.2)** — I'm up (phone button, phone alarm
  screen, or the TV's I'm up / Back) calls `POST /api/wake/awake`, which now
  also runs `wake_alarm.good_morning()` on a background thread: the
  `WAKE_UP_SCENE` scene/script if set, else `light.turn_on` on every light
  except `_segment_` entities. `WAKE_UP_LIGHTS=0` turns it off. The endpoint
  takes the admin token or a client token with the new `wake:write` scope.
- **Tap the wake-up push for the alarm screen; Spotify fallback (iOS 4.4 /
  build 126, TV 8.1)** — tapping the WAKE_UP notification itself (not a
  button) opens the app on `WakeRingingView`, full screen, with big Snooze
  9 min / I'm up buttons (same `/api/wake/snooze` / `/api/wake/awake`). TV:
  if Spotify's leftover session ignores `playFromUri` and stays paused (seen
  2026-10-28: paused on last night's track), `Wake.run` opens the link in
  Spotify itself (`am start VIEW`, `spotify:search:` for searches) and
  presses play again.
- **Five new themes, phone snooze, Continue Watching (TV 6.9, iOS 3.0 / build
  112)** — Graphite, Malibu, Crimson, Stealth, Mark 85 on phone, TV and Core
  (`JarvisPalette`, `Theme.java`, `brain/theme.py`). The wake-up push now
  rings the phone (category WAKE_UP) with I'm up / Snooze 9 min →
  `/api/wake/awake` / `/api/wake/snooze` (Core rings the snooze again, the TV
  pauses and closes its wake screen via `wake_snooze`/`wake_dismiss` bus
  events). TV: a Continue tile from Plex's Continue Watching. Home tidied;
  the TV remote moved to the top of Hub.
- **One-day alarm changes, TV remote, weather icons (TV 6.7, iOS 2.9 / build
  111)** — "skip my alarm tomorrow" / "8:30 tomorrow" change one date only
  (`skip_wake_up`, wake `overrides`). `tv_power` and a remote in the phone app
  go through HA's Android TV Remote entity; Cast "turn on" no longer opens
  Home Assistant on the TV. TV: portfolio in dollars and cents, a weather icon
  (sun/moon, cloud, rain, snow, storm, fog) before the temperature, and the
  shared theme followed every 5 s while home is showing.
- **Charles Schwab, read-only and live (TV 6.6, iOS 2.5 / build 107)** —
  `integrations/schwab.py`, the runtime's Schwab monitor and streamer,
  `/api/portfolio`, `/api/schwab/login` + `/callback`, `portfolio_summary`.
  Phone: Home portfolio card (value, today, yesterday, cash, "Live" when the
  streamer is up) and Settings > Charles Schwab (link status, Log in). TV: a
  live line under the weather. iOS 2.6 / build 108: login moved into an
  in-app web view that catches Schwab's redirect to the app's registered
  `https://127.0.0.1:8182` and hands it to `/api/schwab/finish` (Safari +
  Tailscale callback was refused: the app's callback didn't match).
  iOS 2.7 / build 109: the Home card adds overall gain/loss. Trading is the next step (see
  FUTURE_SESSIONS.md).
- **Wake-up schedule, no fade, snooze; one theme everywhere (TV 6.5, iOS 2.4 /
  build 106)** — the wake-up is a weekly schedule with its own time per day
  (`brain/wake_alarm.py`; `set_wake_up` merges days, `cancel_wake_up` takes
  days) plus a one-off, edited in the phone's Settings > Wake-up
  (`WakeScheduleView`, `GET`/`PUT /api/wake`). The TV no longer fades the
  volume up: it plays at the alarm volume and shows `WakeActivity` with I'm
  up / Snooze 9 min. The colour theme lives on Core (`brain/theme.py`,
  `GET /api/theme` status:read, `PUT` admin or new client scope
  `settings:write`); the phone pulls it on launch/foreground and pushes on
  pick (`ThemeSync`), the TV pushes from its Settings page and follows
  `/api/glance`. The TV needs `tv/provision.sh` re-run for the new scope.
- **Wake-up by name, no Spotify on screen, Back in Black (TV 6.4, iOS 2.3 /
  build 105)** — `set_wake_up` takes relative times ("in 3 minutes" makes a
  one-off that disables itself after firing; days "once"/"tomorrow" too) and
  music by name: anything that isn't a Spotify link is stored as
  `search:<words>` and the TV plays Spotify's top hit through its media
  session (Spotify supports PLAY_FROM_SEARCH / PLAY_FROM_URI), so the app
  never opens on screen. Fixed: a TV restart within 10 minutes of an alarm
  replayed it and woke Nick again; the handled alarm is now persisted.
  Easter egg: the reactor dances to "Back in Black" (Visualizer on the
  output mix).

- **TV 6.2 (iOS 2.2 / build 104)** — Right from School or Systems now goes to
  the music player (it used to drop to the Wake-up tile). `tv/README.md`
  holds the TV's working notes and `deploy.sh` copies it to
  `/sdcard/JARVIS_TV.md` on the TV.

- **TV 6.1: Back hides the highlight (iOS 2.1 / build 103)** — On the TV home
  screen, Back (with no detail open) parks focus on an invisible view so
  nothing is lit; the next arrow/OK only brings the highlight back where it
  was. Home still lands on Plex.

- **Fast restarts (iOS 2.0 / build 102)** — `jarvis-api` restarts took 90 s:
  uvicorn waited for the TV's never-ending SSE stream to close until systemd
  killed it. `run_server` now passes `timeout_graceful_shutdown=5`; the TV
  reconnects and replays recent alerts by itself.

- **JARVIS TV 6.0: cards, settings, wake-up music (iOS 1.99 / build 101)** —
  Reactor centred in the header between greeting and clock. Cards: Next up
  (four non-school calendar items; family red, personal yellow, school
  purple), School (next assignment, "in 2 days · Thursday", how many more in
  two weeks), Systems ("All systems running." or the worst alert, one
  download, to-dos and chores in small print; "CORE ONLINE" without the time)
  and the music player. Every card opens a detail list; Back closes it.
  Bottom row: Plex, Spotify, Wake-up (next alarm), Settings small in the far
  corner. Settings is JARVIS's own page in front of Android's: colour theme
  (TV-local for now), read replies aloud (off), show downloads, nightly sound
  refresh, wake-up info, Android settings. Voice replies are no longer read
  aloud by default, and the launcher steps Back out of any Google app the
  Assistant opens for a "Jarvis, ..." request. Wake-up: `set_wake_up` on Core
  (ask JARVIS on the phone), the TV wakes on the `wake_up` event, opens the
  Spotify link, presses play until it plays, fades the volume in and lands on
  the home screen (verified with the adb test hook; the standby wake-from-
  network path still needs one real morning to prove).

- **JARVIS TV 5.0: calm home, music, "Jarvis, ..." (iOS 1.97 / build 99)** —
  The TV home screen is two panels now: Coming up (the next two weeks of
  calendar from `/api/glance`'s new `events`, each with a category dot and
  legend: school, personal, family; work is hidden by
  `GLANCE_HIDDEN_CATEGORIES`, categories come from `CALENDAR_CATEGORIES` or
  the calendar's name) and Now playing (Spotify on the TV via its media
  session: art, title, artist, previous / play-pause / next on the D-pad;
  play with nothing loaded opens Spotify). To-dos, alerts, downloads and due
  chores shrink into one line of small print. The remote's mic talks to
  JARVIS: say "Jarvis, ..." and the launcher catches the Assistant's
  transcript, closes its panel, sends the rest to `POST /chat` (new client
  scope `chat`; the token is re-made by `tv/provision.sh`), shows the answer
  as a banner and reads it aloud with the TV's TTS (British voice; the
  ElevenLabs voice is a later step). Spotify for Android TV installed.

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
  a large reactor (one-tap briefing removed in 5.76) with a time-of-day greeting ("Good
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
  `SELF_REPAIR_COMMANDS` were initially editable settings; command definitions are now admin-only (see Review corrections). The memory extractor also
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
  by the diagnosis and run when Nick asks (`apply_media_fix` → `confirm_gate.act`,
  at once; restart goes through `prepare_restart(direct=True)`). Also
  `GET /api/status/media/diagnose?title=&kind=movie|show` (read-only JSON:
  stage, stalled, summary, steps, fixes, candidates).
- **Companion media requests** (`integrations/media_requests.py`) — chat-free
  search → request → confirm for the planned Media tab:
  `GET /api/media/search`, `POST /api/media/requests` (opens a confirm-gate
  proposal, writes nothing), `POST /api/media/requests/confirm {code}`. Uses
  the same receipts and `add_movie`/`add_series` as chat. Shows added this
  way always monitor and search **season 1 only** (`SHOW_MONITOR =
  "firstSeason"`), ignoring `SONARR_MONITOR_STRATEGY`, whose `future`
  default monitors only unaired episodes and so downloads nothing.
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

### Performance debt (September 2026 audit, re-measured 2026-11-09)

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
- **Per-turn memory tokenizing** — `brain/memory.py:_load()` now keeps the
  parsed `profile.json` and re-reads only when its mtime/size changes
  (2026-10-29), which also covers the GUI panels' fact reads. Every fact is
  still re-tokenized each turn; fine at 60 facts (the roadmap's indexed store
  supersedes this).
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
- **134 tool schemas on every call** (measured 2026-11-09: the stable prefix,
  tools plus persona, is ~28k tokens, read from cache on 78% of turns).
  Cached reads are cheap and first-token time is already ~1.2 s, so this is
  a cost-of-a-cold-start issue (a 1 h cache write of 28k tokens after every
  idle hour or deploy), not a latency one. Routing to a tool subset per
  intent would shrink it but changes behaviour.
- **Per-turn context is ~4.5k tokens** (2026-11-09): the core profile
  (~1.1k), profile facts (~1.1k), recalled notes (0–0.7k) and the last tool
  results (up to 6,000 characters) are written to the cache fresh every
  turn. The core profile changes at most daily and could move into the
  cached system block. Worth cents a week today; revisit if usage grows.
- **The room voice waits for the whole reply** (`runtime.respond`, not the
  streaming path) before speaking. Replies are one sentence, so streaming
  model text into speech would gain only ~0.1–0.3 s and complicates the
  "Done." jingle check; the larger fixed wait is the 1 s end-of-speech
  silence (`VOICE_SILENCE_END_MS`; 750 cut Nick off). Smarter endpointing
  (end early when the live caption is a complete command) is the next real
  win for voice latency.
- **Server stream loops poll in-memory state** (`/api/voice/stream` every
  80 ms, `/api/home/stream` 150 ms, `/api/traffic/stream` 100 ms per
  client): about 3% of one core at 800 MHz with three screens attached.
  Fine; event-driven wakeups would only matter with many more clients.
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
- **Email awareness** — triage subagent shipped (5.89); still open: important-mail
  summary in the morning briefing, voice reading/drafting replies (send behind the confirm gate)
- **Ambient music control** — detect silence and auto-dim volume
- **Double-clap wake** — alternative to voice wake word (from reference script)

---

### Phone live voice transport

`app/companion_voice.py` implements a single hold/turn per authenticated
WebSocket (`/api/voice/live`, companion admin token in the Authorization header,
never in the URL). The `chat.voice_live` capability advertises it. Start with an
existing `session_id` and unique `request_id`; `partial` messages carry the
current transcript and receive ephemeral `transcript` acknowledgements. Only
`commit` with the final text enters the runtime, using `spoken=True` in that
session, the same confirmation gates, and request receipts. Empty, oversized,
unknown-session and malformed captures fail without tools. Capture is capped
at 120 s; two voice workers and bounded outgoing/sentence queues protect Core.

Replies interleave JSON `text` events and binary PCM (signed 16-bit little
endian, mono, 22050 Hz); `audio_error` preserves the text reply, and `done` ends
reception. The phone waits for scheduled audio to drain before showing Done.
No raw microphone audio or recording file is uploaded in this version. Keys
for ElevenLabs remain on Core; the existing `ELEVENLABS_*` settings and local
Piper voice are reused. `websockets` is an explicit server dependency. The
speech stream strips tool-status/round markers and pipelines sentences while
model generation continues. A committed turn survives transport cancellation;
check Chat/history or retry its original request ID instead of executing it
again with a fresh ID.

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

# JARVIS for Mac (builds + installs ~/Applications/JARVIS.app)
mac/JarvisMac/build.sh --open
```

---

## JARVIS TV

Full working notes: `tv/README.md` (also on the TV at `/sdcard/JARVIS_TV.md`).

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
- The token is `nicks-room-tv` with `events:read status:read chat
  settings:write wake:write`: it can read, talk to JARVIS (his confirmation rules
  apply), set the shared theme and press I'm up (good-morning lights), nothing
  else. Add a scope without re-provisioning:
  `.venv/bin/python -m app.client_tokens grant nicks-room-tv wake:write`. Until provisioned, the footer reads "NOT LINKED
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
- Music: `MediaListener` is an empty NotificationListenerService that deploy.sh
  enables (`cmd notification allow_listener`) so the launcher can read and
  control Spotify's media session; no Spotify API keys involved.
- Wake-up test hook (DUMP-guarded): `adb shell am broadcast -n
  com.tvhome/.WakeTestReceiver [--es music spotify:playlist:...] [--ei volume 20]`.
- Voice: `Voice.request` matches "jarvis" and common mishearings (jervis,
  travis, charvis); one Core chat session per day so follow-ups have context.
  The Assistant panel is dismissed only if it holds focus (never Back out of Plex).
- Verified 2026-10-27 against a mock Core on the Mac: glance renders, the
  SSE banner shows over Plex without stealing focus, offline footer reads
  "CORE UNREACHABLE · LAST SEEN ...". Provisioned against the real Core the
  same evening (token `nicks-room-tv`): live calendar, assessments, weather,
  alerts and downloads render. The TV replays the stream from 0 on every
  connect and dedupes in memory, because bus ids restart with Core.
- Run `tv/provision.sh` from a normal shell; in zsh a leading `! ` negates
  the command, so `! cd … && tv/provision.sh` silently skips the script.

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
  download speed > 0; stalled, queued and paused ones don't count). From Nick
  (phone, desktop, his voiceprint) it restarts immediately, downloads or not
  ("Restarting now; N download(s) pause and pick back up after"). A guest
  voice instead gets a session-bound request with a 4-digit code: a bare "yes"/"confirm"/"go ahead" works only
  as Nick's very next message (any other message closes that window), and
  "confirm <code>" (or the bare code, or the old long phrase with the code)
  works for ten minutes. A bare "no"/"cancel" as the next message, or
  `cancel arr restart` any time, cancels. The request is consumed once, before
  SSH. `restart_arr_stack` (the model's tool) uses the same direct path, so
  "restart it" in chat runs at once too. The direct request also works in
  offline text/voice.
- `update_arr_stack` updates the arr stack at once when Nick asks: the NAS's
  compose updater (`ops/nas/stack_updater.sh`, the weekly root-cron job) runs
  at once through the recovery key (`update <uuid>`), pulling images and
  recreating only changed containers, never through the UGOS GUI. Arr alerts
  are held for `ARR_STACK_UPDATE_HOLD_S` (900) meanwhile. Servarr's "New
  update is available" health message is no longer an alert at all.
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

### Review corrections (2026-10-29)

- Repair command definitions are administrative `.env` configuration only, never editable through `update_setting`. Command repairs require Nick’s session-bound chat confirmation or app approval, and are bound to the exact command digest at diagnosis; changed/legacy commands require a fresh proposal.
- Room voice selects its own runtime session atomically for every spoken turn. Phone/TV session switches cannot move room transcripts or consume another session’s pending confirmation.
- NAS host monitoring waits out startup grace before advancing persisted alert state, so disk/RAID faults present at boot are delivered after grace instead of swallowed.
- Profile memory serializes complete read–modify–write operations with a reentrant lock across parallel tools and background extraction; atomic replacement alone is not the concurrency boundary.

- Updater correction (2026-10-29): failed pulls, image inspections or recreations abort with a nonzero exit and a `FAILED` phase. `APPLIED` follows successful recreation only; final `SUCCESS` requires all stages. Gluetun dependents are explicitly removed and recreated with `--no-deps`; old images are retained for manual rollback.
- Wake scheduling excludes already-fired occurrences before choosing a due candidate, so a nine-minute snooze rings on time alongside the weekly schedule.

- Watchdog correction (2026-10-29): indexer recovery attempts start cooldown before execution, including failures. DHT login/state requests have 3-second connect and 10-second total limits while holding `stack.lock`; credentials are form-encoded.

- Chromium egress uses `brain/browser_proxy.py`: every HTTP request and HTTPS tunnel resolves and pins a public destination before connecting, including redirected requests and subresources. Implicit Chromium loopback bypass and non-proxied WebRTC UDP are disabled; service workers are blocked. The proxy closes with the browser.
- Schwab quotes carry receipt timestamps. Only quotes at least as recent as the account snapshot and no older than 60 seconds override account values; stale quotes fall back to the REST holdings.
- History connections now close explicitly after commit/rollback. An index on `(session_id, id)` supports conversation loading, summaries and pruning as the archive grows.
- Phone alert delivery uses a bounded FIFO worker (200 pending pushes), leaving monitor publishing and SSE delivery unblocked by APNs/HA timeouts. Queue saturation logs the missed push; the alert remains in the bus replay history.

- Companion history sync uses the server’s `last_message_id` revision to skip unchanged transcripts while still updating titles. Revisions are cached in the local archive; older servers without revisions still fetch normally. Live turns are skipped before fetching, message matching is indexed, and date formatters are reused.
