# Future sessions: what Nick wants and what would move JARVIS forward

Written 2026-09-16 at the end of a budget-limited session. This is the
backlog for the *self-everything* direction: an assistant that learns, adjusts,
heals, and grows on its own while staying governed. Read it alongside
`ROADMAP.md` (milestones), `SELF_HEALING_HANDOFF.md` (what just shipped and
needs review), and `ARCHITECTURE.md` (the layered design and runtime rule).

## Overnight overhaul (2026-09-22) — code done, awaiting review/deploy

Nick's unattended brief, recorded so a later session can resume. Hard
constraint everywhere: do **not** change the NAS SSH permission model
(restricted forced-command keys only, no Docker socket, no root). Priority is
not breaking working behaviour. Small commits, one per logical change.
Progress, evidence, decisions and review notes live in `OVERNIGHT_HANDOFF.md` — read it first to resume.

1. **Confirmation gate (arr restart).** Malformed confirmations (bare token,
   typo, wrong token) must reuse the pending token while unexpired, and the
   mismatch reply must echo exactly what Nick sent. Check the media-add gate
   (`integrations/media_selections.py`) for the same problem.
2. **Post-restart health.** Replace "outcome is unknown ... request <uuid>"
   with a real up / still starting / failed status for Sonarr, Radarr,
   Prowlarr and gluetun, polled within the SSH constraint; follow up
   asynchronously instead of dead-ending on a UUID.
3. **HA alert false positives.** Find why NAS-power-loss / device-offline
   alerts over-fire; fix root cause; add N-consecutive-failure debounce and a
   confirmed "back up" before declaring recovery.
4. **Per-indexer alerts.** Alert on each individual indexer going down (not
   only aggregate Prowlarr health), with the same debounce.
5. **Push via the JARVIS iOS app.** Alerts go to JarvisCompanion as push;
   HA notify becomes fallback-only. If APNs infra does not exist, stub it
   cleanly, document Apple Developer / APNs key requirements, keep HA as the
   working primary.
6. **Performance pass.** Audit API slow paths (Optiplex i5-6500T, 4 GB),
   Faster-Whisper latency, redundant polling / N+1 calls / missing caching on
   status hub endpoints. Don't change Claude model choice; flag waste.
7. **Setup TODO audit.** List everything Nick must do himself.

## Run the NAS stack (2026-10-27) — Phase 1 shipped, rest planned

Goal: a failure on the NAS shows up in JarvisCompanion; Nick taps it, Chat
opens with a detailed pre-filled report (logs, recent changes, suspected
cause), they talk it through, JARVIS proposes or runs the fix. Same hard
constraint as above: the NAS SSH permission model does not widen without
Nick saying so.

Nick's decisions (2026-10-27):
- JARVIS **never** restarts a container or runs the arr recovery without
  asking. Every restart is a one-tap proposal. The NAS's own root-cron
  DHT/indexer watchdogs keep auto-acting as today.
- Notifications keep the current `arr_incidents` policy (debounce, watchdog
  hold, one push per incident); the push should open the report.
- Approved for after Phase 1: a NAS-side `host-health.json` read addition.

Tiers:
1. **Read, always allowed.** Today: Radarr/Sonarr/Prowlarr/qBit/HA HTTP
   health and log APIs, the read-only status key (`stack_status.sh`), media
   doctor. **Phase 2 (approved):** root cron already on the NAS writes
   `/volume1/docker/watchdog/host-health.json` every 5 min (`docker ps`
   state/health/restart counts for every container incl. Plex, pihole,
   immich, tautulli; `updater_status.txt`; `/proc/mdstat`; SMART health and
   temperatures; fan readings from `hwmon.log`; recent `kern.log` disk
   errors), and `stack_status.sh` includes it. The key gains output, not
   privilege. Write the scripts locally under `ops/nas/`, hand Nick the
   install commands; then add those sections to incident reports and new
   monitors (disk, container restart loop, pihole) via `raise_alert`.
2. **Safe fixes, allowlisted, always proposed (never automatic).** Within
   today's permissions: arr API actions (re-search, replace release,
   re-test indexers), qBit resume, Plex library refresh, HA restart via HA's
   API, the full arr recovery via the existing recovery key. Each as a
   `brain/self_repair.py` action kind with a post-fix `poll_health()`
   verify. Per-container restart needs a new `recover_stack.sh` verb, i.e.
   a permission change: ask first.
3. **Everything else.** JARVIS stages a `self_repair` proposal; the app shows
   it with an Approve button (`POST /api/repairs/{id}/apply` →
   `self_repair.apply(confirmed=True)`). Do not add a fourth gate; the
   existing three are self-repair ids, `confirm_gate` codes and the arr
   restart token, and stack fixes should converge on self-repair.

Phase 2 (1.81 / build 83; installed on the NAS and live 2026-10-27, 18 containers, 3 arrays, 3 disks):
`ops/nas/host_health.py` + `install-host-health.sh`, `stack_status.sh`
includes `host_health`, `integrations/nas_host.py` monitor and incident
sections.

Download retry (built 2026-10-27, 1.84 / build 86): stall alerts carry
`context.torrent_hash`; the runtime stages a `retry_download` repair for the
owning Radarr/Sonarr queue item. **Coordination with Nick's separate "revamp
automatic search" session:** the retry deliberately owns nothing about search
quality. It only does remove + blocklist + `skipRedownload=false`, so the
*arr's own profiles, indexers and custom formats (whatever that session sets
up) choose the replacement. If that session adds its own stalled-download
handler (e.g. Cleanuparr/Decluttarr, or Radarr's own failed-download
handling), keep JARVIS proposal-only (it already is) and consider raising
`MEDIA_DOCTOR_STALL_MINUTES` so the two don't both act on the same torrent.

Tier 2 + 3 approval (built 2026-10-27, 1.83 / build 85): `brain/stack_fixes.py`
allowlist, `stack_fix` self-repair kind that only runs on Nick's chat
"yes"/code or the app's Approve tap, Home → Repairs card. Next: per-container
restart (needs a new `recover_stack.sh` verb, i.e. a permission change; ask
Nick), alert id in the APNs payload so tapping a push opens the report, and
`download_stalled` reports calling `media_doctor.diagnose(title)`.

Phase 1 (shipped, 1.80 / build 82): `brain/incident_reports.py`,
`POST /api/alerts/{id}/report`, tappable Needs attention rows. Next small
steps: put the bus alert id in the APNs payload so tapping the push opens
the same report; incident reports for `download_stalled` could also call
`media_doctor.diagnose(title)` for that title.

Survey findings (2026-10-27, read-only):
- All seven services up; DHT ~350; status key works from the Optiplex.
- 2026-10-25 18:00–19:00: VPN DNS timeouts inside gluetun; two DHT-watchdog
  recoveries failed at verify, then it self-healed ~19:00. The
  `stack_updater` cron had auto-updated gluetun + tailscale (`:latest`) at
  05:00 that day — exactly the "recent change" reports can't see yet.
- Every NAS pool is a single disk (`md1/2/3 [1/1]`): no redundancy. Docker
  configs live on volume1, a 2 TB Seagate ST2000DM001.
- `hwmon.log` warns every 10 min (`f1=660,f2=-1`); probably a missing
  second-fan sensor.
- `watchdog/dht_watchdog.sh`, `stack_status.sh` and `qbit_creds.env` show
  mode `rwxrwxrwx+`, but on `/volume1` UGOS's `ugacl` overrides mode bits
  (admin-group allow, Jarvis read-only; see `ops/nas/README.md` "Share ACL"),
  so this is not a write hole for non-admins. Retracted as a security issue.
- Optiplex logs Google Calendar `invalid_grant` every 2 min: re-auth with
  `python -m integrations.google_calendar --auth`.

## Nick's wishlist (ever-growing — add to it, tick things off)

Nick's running to-do list for the project. When one ships, move it to
`JARVIS.md` "Recently Shipped" and delete it here. Newest asks at the bottom
of each group.

### Loose ends (Nick)

- [ ] Reserve the Optiplex's IP on the home router (it drifted 10.0.0.80 →
  10.0.0.81 on 2026-10-27). Ssh now uses Tailscale, so this is insurance.
- [ ] Check the iPhone app's server address in Settings; if it's 10.0.0.80,
  switch it to the Tailscale address `100.69.8.11`.

### Next session starts here (left 2026-10-28, from school)

- **Morning report 2026-10-28:** the TV turned on at 8:00 (proven), but no
  music. HA history: at 08:00:03 Spotify's session was *paused* on "Highway
  to Hell" (last night's track), so `playFromUri` was ignored; the TV went
  off at 08:00:52. TV 8.2 (built, not installed) falls back to opening the
  link in Spotify. **Install at home:** `TV=10.0.0.46:5555 tv/launcher/deploy.sh`,
  then test with the WakeTestReceiver broadcast after the TV has slept.
  Ask Nick whether he turned the TV off at 08:00:52 or it shut itself off.
- **Good-morning lights:** Nick turned the TV off himself at 08:00:52.
  I'm up now turns on every light via Core (live after `git pull` + restart).
  For the TV's I'm up: install TV 8.2 and run
  `.venv/bin/python -m app.client_tokens grant nicks-room-tv wake:write` on
  the Optiplex. Later: build a real `scene.good_morning` in HA (colours,
  brightness, maybe blinds) and set `WAKE_UP_SCENE`.
- Schwab shows Live after the open (confirmed 2026-10-28).
- **Nick's rainbows (picked 2026-10-28, in the Govee app):** Nick Strip
  Lights = DIY **Real Rainbow**; Desk Lights (`light.desk_lights_2`) = DIY
  **New Rainbow**; both 100% brightness. HA can't see a DIY pick made in the
  Govee app (the dropdown stays "None", the light still reports the built-in
  Rainbow-A / Rainbow), so set them by name. At home, put this in the
  Optiplex `.env` and restart:
  `BARN_DOOR_EFFECTS=select.nick_strip_lights_diy_scene=Real Rainbow,select.desk_lights_diy_scene=New Rainbow`
  then engage + stand down to check. In HA scenes, add the two dropdowns by
  hand in YAML (the scene editor would capture "None"):
  ```yaml
  entities:
    light.nick_strip_lights: {state: "on", brightness: 255}
    light.desk_lights_2: {state: "on", brightness: 255}
    select.nick_strip_lights_diy_scene: Real Rainbow
    select.desk_lights_diy_scene: New Rainbow
  ```
- **iPad (iOS 4.8 / build 130, not installed):** Room tab, long-press
  light → brightness/colour, night screen. Untested on hardware: check the
  night screen tonight (sun down, all lights off, wait 45 s; turn a light on
  from the phone and it should wake within ~5 s) and that brightness comes
  back after leaving the app. Needs the Optiplex pull for light details.
- **Scenes decision (2026-10-28):** scenes live in Home Assistant; JARVIS
  runs them (app picker, voice, `WAKE_UP_SCENE`). JARVIS protocols only for
  things that also touch TV/theme/music. Next: a confirm-gated "save this as
  a scene" tool (HA `/api/config/scene/config/<id>` from current light
  states), and an iOS widget / Control Center control for scenes.
- **iOS 4.6 / build 128 (not installed):** tap the wake-up push → full-screen
  Snooze / I'm up. Install from Xcode when at the Mac.
- **1337x fixed (2026-10-28, afternoon):** it was a Cloudflare 1006 IP ban
  on the VPN exit, not a rate limit. Nick moved it to `https://1337x.st/`
  (test passed, saved; Prowlarr backoff cleared). Radarr/Sonarr's
  "unavailable for more than 6 hours" warning clears after their next RSS
  sync. Shipped in iOS 4.9 / build 131: incident reports quote the real HTTP
  status + Cloudflare code from Prowlarr's log, and a confirm-gated
  `switch_indexer_mirror` stack fix. **Not live-tested yet:** after deploy,
  open an indexer incident report and check the "Indexer errors" section.
  If 1337x.st gets banned too, every mirror will likely fail: the fix is a
  new gluetun server (Nick at the NAS; drops downloads briefly).
- **Knaben and Torrent9 are disabled by hand**, not by Prowlarr (no
  failures logged). Torrent9 has a mirror override to `torrent9.la` and is
  French-language; Knaben is a meta-search that duplicates 1337x/TPB. Ask
  Nick whether either should come back as a 1337x backup.
- **NAS powers today:** read-only host health (cron JSON), arr recovery
  script, Prowlarr indexer retest, HA restart, Plex refresh, download retry.
  No arbitrary docker, no image updates, no config edits, by design
  (restricted SSH keys). Nick asked for "update and do everything with my NAS
  and docker": next step is a confirm-gated, allow-listed `nas_docker` action
  (pull + recreate one named container from compose) on the NAS side.

### Earlier: left 2026-10-27, late

- **Morning checks Nick will report:** did the 8:00 wake-up turn the TV on
  from a night off (the unproven part)? Did the LED under the TV still
  flash overnight (the voice-mic LED was turned off via
  `settings put global tcl_sys_lights_farfiled_voice_led_enabled 0`)? Does
  the Schwab card show Live after the open, and does "yesterday" fill in?
- **If the LED still flashed:** suspect the SSE keep-alive (every 15 s)
  waking the TV from network standby. Fix: AlertStream closes the stream on
  screen-off and reconnects on screen-on; Core's wake monitor wakes the TV
  with `tv_remote.power(True)` (HA Android TV Remote) before publishing
  `wake_up`, which the TV replays on reconnect. Only after the alarm test
  works, so the wake path isn't changed blind.
- **Next big step Nick wants:** the always-listening voice assistant on the
  Optiplex (mic + speakers, maybe ElevenLabs already there), to replace his
  Google Home Hub. He's wary because the Hub shows what's happening; plan a
  visible feedback surface (the TV/iPad showing what JARVIS heard and is
  doing) from the start.

### Integrations

- [x] **Charles Schwab, read-only and live** (shipped 2026-10-27, iOS 2.5 /
  TV 6.6; see JARVIS.md "Charles Schwab"). Built against fakes; first real
  run once Schwab approves JARVIS's app. Check then: streamer field numbers
  (3 last), cash fields for Nick's account type, the in-app login
  catching the `https://127.0.0.1:8182` redirect. The `tailscale serve`
  route for `/api/schwab/callback` set up on 2026-10-27 is unused now.
- [ ] **Schwab trading** (Nick wants it, 2026-10-27; parked until the rest
  feels solid: "too janky to let it take my money"): "if NVIDIA hits X,
  sell; at 170, buy". Place real limit/stop orders at Schwab (they run even
  if Core is down), not price-watching in JARVIS. Agreed guardrails: every
  order confirmed on the phone with the exact order and dollar amount;
  per-order and daily caps Nick sets; stocks/ETFs only, no options or
  margin; "buy tons" becomes a number before anything is placed. Same app
  (it already has Accounts and Trading).
- [ ] **Headless browser for JARVIS** (asked 2026-10-27): Playwright on the
  Optiplex for searching, reading and filling forms. Not for logging into
  banks or brokerages (Nick agreed: no Schwab password on Core).
- [ ] **Letterboxd** (asked 2026-10-27): pull Nick's diary/ratings (RSS feed
  is the easy path, no official API) so JARVIS knows what he's watched and
  liked, and can tie it into Radarr requests.
- [ ] **Nick's top 5s** (asked 2026-10-27): his top fives in general (films,
  albums, etc.) as standing knowledge. Probably durable profile facts or a
  small dedicated store JARVIS can read and Nick can edit.
- [ ] **Browser abilities** (asked 2026-10-27): general web search/lookup
  ("google this") and price watching (track a product URL, alert through
  `runtime.raise_alert` when it drops below a target). Price watching is a
  monitor, not a UI poll.

- [ ] **Room scenes** (Nick, 2026-10-27; "not yet"): one phrase sets the
  whole room. "Game time": strips and desk strips on, bedroom light off,
  Avengers tower on, fan on. "Bedtime": everything off, fan to 40%. Barn
  Door Protocol becomes one of them. Shape: named scenes in a small store
  (entity → state/colour/effect/fan %) that JARVIS runs by name, editable in
  the app; expose them to Google Home through HA scripts so "Hey Google,
  game time" works too. Nick's idea for one: a "love" scene that reuses
  Barn Door's takeover (soft pinks on the lights, a warm screen instead of
  the red alert); not now (2026-10-27). Remember Govee app modes (rainbows) aren't visible to
  HA; use the effect names HA lists.
- [ ] **Room sentry on the work iPad** (Nick, 2026-10-27; waiting on the
  iPad). The iPad, propped up running JARVIS, watches the room with Vision's
  on-device person detection (frames never leave it, nothing recorded).
  "Not Nick" is the hard part: start with "armed" (Nick says "guard my room",
  or his phone's HA device_tracker leaves home) + a person in frame;
  face recognition later only if he wants it (opt-in, his face enrolled on
  the iPad). Detection → Core → `barn_door_protocol` + one line spoken in
  unison on TV (Android TTS / ElevenLabs), iPad and the Optiplex speakers
  ("Barn Door Protocol activated. Get out of Nick's room."); nobody in frame
  for ~30 s → stand down. Tell the household it exists. Nick's plan: the
  iPad wall-mounted by his door, with a big ARM button he taps on the way
  out (and DISARM / Face ID on the way in), so "armed" is explicit.
- [ ] **Whole-house Barn Door** (Nick, 2026-10-27, "far off"): glass-break /
  door sensors in Home Assistant; a break-in at night puts every room into
  Barn Door Protocol and plays Back in Black through the house. Builds on
  the room sentry and scenes.
- [ ] **"Hey Google" replacement on the Optiplex** (Nick, 2026-10-27): the
  Optiplex has a mic and speakers (and maybe ElevenLabs set up already, to
  verify), so `--voice` mode could run there as the house's always-listening
  JARVIS once Nick wants it.

### Companion apps

- [ ] **Speak replies in JARVIS's voice on the phone**: the app's mic is
  dictation only today; replies are text. Core would synthesize with
  ElevenLabs and the app plays the audio (opt-in, per reply or always).
- [ ] **JARVIS TV: JARVIS is the TV's home screen** (asked 2026-10-27).
  Nick's TCL TV has had Google TV stripped back to Plex and YouTube. Instead
  of a JARVIS app on it, JARVIS *is* what the TV boots into: an Android TV
  launcher (registered as the HOME app), with Plex and YouTube launched from
  it. Same JARVIS as the phone: one Core, one look, one voice. Planned in
  its own session; this is the agreed checklist to start from.

  **v1 shipped 2026-10-27** (`tv/`, see JARVIS.md "JARVIS TV"): native
  launcher (the existing `com.tvhome`, decision 1), TCL G10 / Android 12
  (decision 4), glance + app row + live alert banners + the no-sound fix.
  Provisioned and live since 2026-10-27. Next: media (continue watching /
  recently added), then v2.

  **Pinned by Nick 2026-10-27 (next TV session)**
  - [x] (6.0) A JARVIS settings page in front of Android Settings (the Settings
    tile opens it; "Android settings" stays reachable from inside it). Lets
    Nick change what I set: colour theme (Arc / Mark III / Hot Rod, saved on
    Core so the phone can follow), which calendar categories show, what the
    small-print line includes, the nightly refresh, the voice. Needs a write
    path on Core for those settings (a new client scope, not the admin token).
  - [x] (6.0) Drop the time after "CORE ONLINE" in the footer (keep "LAST SEEN
    <time>" when Core is unreachable).
  - [x] Hotschedules shifts stay on the calendar (Nick, 2026-10-27).
  - [x] (6.5) Theme choice saved on Core (`settings:write` scope) so the phone
    and TV follow one pick.
  - [x] (6.5) Wake-up schedule with its own time per day, edited in the phone
    app; no fade-in; I'm up / Snooze 9 min on the TV.
  - [ ] Prove the wake-up from deep standby on a real morning (the Core
    event should wake the TV over the network; TCL blocks app alarms).
    Nick is testing 2026-10-28.
  - [x] (2026-10-27) One-day alarm changes by voice: "skip my alarm
    tomorrow", "8:30 tomorrow"; back to normal the week after.
  - [x] (6.7 / iOS 2.9) Phone TV remote, `tv_power`, weather icon, cents,
    5-second theme follow.
  - [x] (6.8 / iOS 3.0) I'm up / Snooze from the phone: the wake-up push
    (category WAKE_UP) has both buttons; `/api/wake/snooze` and `/awake`.
  - [ ] Wake-up options Nick may want once he's lived with it ("idk what i
    want yet"): snooze length, stop music on I'm up, a gentler start.

  **Foundation**
  - [x] Boots straight into JARVIS; the remote's Home button always returns
    to it. Plex and YouTube open from it and Back comes home.
  - [x] Talks only to Core, with its own scoped token
    (`python -m app.client_tokens create tv events:read status:read`, plus
    any write scopes it truly needs; never `JARVIS_API_TOKEN`), over LAN
    with Tailscale fallback, and reads the live SSE feed
    (`/api/clients/events`) instead of polling.
  - [x] Works offline: last-known snapshot with a quiet "Core unreachable"
    note, never a blank screen. Plex/YouTube tiles always work.
  - [x] Escape hatch: a hidden way back to stock Android settings (the
    Settings tile; `tv/restore.sh` brings back the stock launcher).
  - [ ] Updates itself from Core (or a one-command sideload) so it never
    needs a cable or the Play Store.

  **One JARVIS (cohesion with the phone)**
  - [x] Same themes (Arc / Mark III / Hot Rod), same reactor mark, same
    fonts and wording; the theme choice lives on Core so phone and TV match
    (6.5: `/api/theme`, picked on either one).
  - [ ] Same persona: greetings, alerts and briefings in JARVIS's voice
    (ElevenLabs through Core), same phrasing as the phone.
  - [ ] Same data, same rules: to-dos, assessments (next 14 days),
    maintenance and alerts come from the same Core endpoints the phone uses,
    so checking something off anywhere clears it everywhere.

  **Home screen (glance from the couch)**
  - [x] Big clock, date, greeting, weather.
  - [ ] Today: work shift, next assessment(s), birthdays, calendar events.
    (Next event, assessments within 14 days, timers and maintenance done;
    work shift and birthdays wait on decision 2.)
  - [x] Money: live portfolio line under the weather (6.6); value and today.
  - [x] To-dos count and list; "things need you" alerts (plus live banners
    over any app from the SSE stream).
  - [x] Media: Plex Continue Watching tile (6.9; the TV asks Plex itself).
    Nick doesn't want recently added (2026-10-27). Downloads are in Systems.
  - [x] App row: Plex, Spotify, Wake-up, Settings in the corner (6.0). Nick
    dropped YouTube and the inputs (the remote has buttons for them). House
    is v3.

  **Mornings**
  - [x] Wake-up: the TV turns on, music fades in at alarm time (6.0; the
    spoken briefing waits for the ElevenLabs voice).
  - [x] Alarm time set by asking JARVIS (set_wake_up); days of the week.
    Skipping specific days off is not built.

  **Music**
  - [x] Simple player: now playing, play/pause/skip (volume is the remote's).
    Spotify on the TV via its media session; JARVIS starting music by voice
    still needs Core's Spotify keys (SPOTIFY_CLIENT_ID/SECRET aren't set).
  - [ ] Remote's play/pause works anywhere in the launcher.

  **Remote and voice**
  - [x] Everything reachable with the D-pad; clear focus highlight; Back
    always steps out; no dead ends.
  - [x] Remote mic talks to JARVIS like Chat ("Jarvis, ..."; TTS voice for
    now, ElevenLabs later).

  **House**
  - [ ] Light/switch tiles like the phone's Hub; timers and reminders pop
    up as toasts and go off audibly.

  **Evenings and idle**
  - [ ] Ambient mode: after idle, dim to clock + reactor + next thing.
  - [ ] Sleep timer; screen off at night, alerts still allowed if critical.

  **Decide at the start of the session**
  1. Native Android TV launcher (Kotlin + Compose for TV, recommended:
     can be the HOME app, handle the remote properly, run CEC) vs. a web
     page in a kiosk launcher (faster to build, weaker remote and wake-up).
  2. Where the work schedule and birthdays come from (Google Calendar?
     work app?).
  3. Music source: Plex music, Spotify or YouTube Music.
  4. TV model and Android version (launcher and CEC behaviour vary).
  5. What ships first: suggested v1 = foundation + home screen + app row;
     v2 = mornings + music; v3 = voice, house, ambient mode.

## Nick's stated goal

Make JARVIS the ever-learning, ever-changing, self-adjusting, self-healing
assistant from Iron Man: local, persistent character, proactive, and able to
improve itself without being told twice. Everything below serves that.

## Principles for anyone building this

1. **Sense before act, propose before apply.** Every self-* feature follows
   the pattern shipped in `brain/tool_health.py` (sense) and
   `brain/self_repair.py` (propose, approve by id, apply, verify, rollback).
   Reuse it; do not invent a second approval mechanism.
2. **Cheap model for background work, main model for conversation.**
   `memory_extractor` and `study_brief` already do this. New learners should
   use the extraction model and never block a turn.
3. **Keep the persona block byte-stable.** Anything that varies per turn goes
   into the dynamic system block so prompt caching keeps working.
4. **Runtime owns behaviour.** Front ends stay thin. New capabilities are
   runtime methods, monitors, or tools, never UI or voice-loop logic.
5. **Never silent source rewrites.** Source-level self-modification must go
   through patch, tests, approval, activation, rollback.

## Tier 1: highest value, build next

### 1. Source-level self-modification (governed)
The roadmap's "governed self-modification" item, minus the config half that
shipped. Design:
- `brain/self_patch.py`: JARVIS drafts a unified diff against a small
  allowlist of files (`brain/tools.py` handlers, `brain/personality.py`
  wording, a new `brain/skills/*.py` folder), stores it as a proposal with a
  rationale and the test command that should pass.
- Apply flow: write to a git worktree or branch, run
  `python -m unittest discover -s tests`, report pass/fail, and only on
  Nick's approval of the id merge it and hot-reload the affected module
  (`importlib.reload` for tool handlers; a restart for anything else).
- Rollback: keep the pre-patch commit hash; `rollback_patch` resets to it.
- Start with the smallest useful case: letting JARVIS add a *new tool* that
  wraps an existing integration, because it cannot break existing behaviour.

### 2. Reflection after failed turns
JARVIS should learn from turns that went badly, not only from explicit
corrections.
- Signals: Nick repeats himself within two turns, says "no", "that's wrong",
  "not that", or a tool errored and the reply pretended it worked.
- A background reflector (extraction model) writes a one-line lesson to a new
  `memory/lessons.json` store with the tool involved and the situation, and
  the top few recent lessons ride in the dynamic block.
- Distinct from standing orders (Nick's explicit rules) and facts (about
  Nick). Lessons are about JARVIS's own mistakes and decay unless reinforced.

### 3. Skill acquisition from repetition
`brain/habits.py` already notices patterns. Next step: when the same
multi-step sequence recurs (lights off, music on, thermostat down), JARVIS
proposes a named routine. Prefer creating it as a Home Assistant script via
the existing automation workshop so HA owns it; JARVIS gets a one-word
trigger. Confirm-gated like every other write.

### 4. Health dashboard in the cockpit and companion
Expose `tool_health.report()` and repair proposals through the runtime and
the server API so the GUI status panel and the iOS "Now" screen show
degraded subsystems with an "apply repair" button. Panels should subscribe to
runtime events rather than poll (see the performance debt list).

## Tier 2: strong value, moderate effort

### 5. Self-tuning settings
JARVIS already edits `.env` via `update_setting`. Let it tune the knobs it
can measure: raise `MIC_RMS_THRESHOLD` after repeated false wakes, shorten
`FOLLOWUP_TIMEOUT_S` when Nick rarely follows up, adjust
`STUDY_BRIEF_HOUR` if Nick keeps reading briefs late. Each change is a
proposal with the evidence, applied on approval, and reverted automatically
if the measured signal gets worse over the next day.

### 6. Memory consolidation
Nightly job that merges near-duplicate facts, lowers confidence on facts
never referenced, promotes repeatedly confirmed inferred facts to explicit,
and writes a short "what changed this week" note to the Obsidian vault.
Also the roadmap's embedding index for the vault once keyword retrieval
gets noisy.

### 7. Proactive check-ins with restraint
A "should I speak?" policy on the runtime: score each pending event by
urgency, Nick's presence (HA occupancy), time of day, and whether he is in a
conversation. Speak for urgent things, queue the rest for the next natural
pause or the morning briefing. Log every decision so the policy can be
tuned. This is the Presence layer's missing piece.

### 8. Watchdog and self-restart
The NAS integration now includes runtime-owned arr health polling, restricted
confirmed recovery, and optional Healthchecks heartbeats that stop if its arr
monitor stalls. See `PROGRESS.md` for activation status. Process supervision
and automatic monitor-thread repair below remain separate future work.

A tiny supervisor (launchd on the Mac, systemd on the Optiplex) that restarts
the process on crash, plus an in-process watchdog thread that detects a
hung turn or dead monitor thread, records it in `tool_health`, and restarts
the monitor. Pair with the health dashboard so restarts are visible.

## Tier 3: nice to have

- **Email awareness** (roadmap): unread summaries in the morning briefing.
- **Ambient music control** (roadmap): auto-dim on speech.
- **Double-clap wake** (roadmap).
- **Local model routing phase two** (roadmap): route more tool calls to the
  local model with a confidence gate and fall back to Claude.
- **Voice interruption polish**: barge-in while JARVIS speaks.
- **Session summaries surfaced on the phone** so a chat picks up context.

## Known debt to clear when convenient

Ordered as in `JARVIS.md` "Performance debt":
- UI panels poll and rebuild; move to runtime change events.
- Per-turn memory disk reads; cache behind an mtime check.
- Per-turn SQLite housekeeping; shared connection, prune every N turns.
- Streaming still opt-in (`STREAMING_TTS=false`); make it default once
  trusted.
- `tests/test_runtime_monitors.py` writes the real `memory/tool_health.json`;
  patch `tool_health._FILE` in setUp.

## How to spend a short session on this repo

1. Read `SELF_HEALING_HANDOFF.md` and clear its checklist first if unreviewed.
2. Pick one Tier 1 item. Ship sensing and proposal first, application second.
3. Add tests in `tests/` using the `patch.object(module, "_FILE", tmp)`
   pattern from `tests/test_tool_health.py`.
4. Update `JARVIS.md` (file map, tools, Recently Shipped) and this file, bump
   the iOS version in `project.pbxproj`, commit.
