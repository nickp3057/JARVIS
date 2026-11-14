# Future sessions: what Nick wants and what would move JARVIS forward

Written 2026-09-16 at the end of a budget-limited session. This is the
backlog for the *self-everything* direction: an assistant that learns, adjusts,
heals, and grows on its own while staying governed. Read it alongside
`ROADMAP.md` (milestones), `SELF_HEALING_HANDOFF.md` (what just shipped and
needs review), and `ARCHITECTURE.md` (the layered design and runtime rule).

## Standing rules for the NAS (Nick, 2026-09-22 and 2026-10-27)

- Do **not** widen the NAS SSH permission model (restricted forced-command
  keys only, no Docker socket, no root) without Nick saying so.
- JARVIS **never** restarts a container or runs the arr recovery without
  asking. Every restart or stack fix is a one-tap proposal through
  `brain/self_repair.py` / `brain/stack_fixes.py`; don't add a fourth approval
  gate. The NAS's own root-cron DHT/indexer watchdogs keep auto-acting.
- Knaben and Torrent9 stay disabled ("they can be off"); don't re-enable or
  suggest them.

## Nick's wishlist (ever-growing — add to it, tick things off)

Nick's running to-do list for the project. When one ships, move it to
`JARVIS.md` "Recently Shipped" and delete it here. Cleaned up 2026-11-14:
everything shipped, deployed or made obsolete by the move to the Surface
was removed (the old per-release "check" lists live in git history).

### His own voice: a local Piper voice trained on Paul Bettany (planned 2026-11-10; Core wiring built 2026-11-14, training in progress)

Nick wants JARVIS to sound like Paul Bettany's JARVIS, for his own room only (never shared or published), and to stop paying ElevenLabs. Never clone it on ElevenLabs: their terms forbid cloning a voice without consent, and a ban would take STT with it. Chosen route: **plan B, fine-tune a Piper voice**; RVC voice conversion (plan A) is the fallback if Piper sounds too flat, built from the same dataset.

- **Training box:** Nick's Windows PC (RTX 3060 12 GB), only while training; WSL2 + CUDA. Dataset in `~/jarvis-voice`.
- **Audio:** Iron Man 1–3 and Age of Ultron are in Plex. Missing and worth adding: The Avengers (2012), WandaVision, Civil War, Infinity War.
- **Core wiring is shipped** (JARVIS.md "Local Piper voice behind one setting"): swapping the trained voice in is `TTS_PROVIDER=piper` + `PIPER_VOICE_MODEL=/path/bettany.onnx` (with `piper-tts` in the Surface venv). Still open: the optional light EQ (hook noted in `speech/piper_voice.py`) and measuring first-audio latency on the Surface (`python -m speech.tts --say ...` prints it; aim near ElevenLabs' ~170 ms).
- **Dropping ElevenLabs entirely** would also mean `STT_PROVIDER=local` (Whisper small.en on the Surface); measure it against Scribe's ~0.5 s before switching.

### Voice Studio / Voicebox as a test bench (asked 2026-11-12)

Nick saw a video on "Voice Studio" (likely VoiceStudio by debpalash, or Jamie Pine's Voicebox; both are local GUIs over several open TTS engines, incl. Qwen3-TTS, Chatterbox). Use it as an A/B bench on the PC's 3060, not as JARVIS's runtime: feed every engine the same cleaned ~15 s Bettany reference (`~/jarvis-voice/r2-tune/ref-jarvis.wav`) and judge by ear against "sounds like a real person" (the bar Piper r2 and Chatterbox missed). If one wins, wire that engine directly behind `TTS_PROVIDER`, served from the PC, with a fallback voice when the PC is off.

### Home network, Optiplex and homelab (asked 2026-11-12)

**Switch at the xFi pod (cheap, worth it now).** The pod outside Nick's room already wires his PC. An 8-port gigabit switch on the pod (an easy-smart one like TP-Link TL-SG108E if he wants VLANs later) wires the Surface (Core), the Mac when docked and the Optiplex. Caveat: everything on the pod still shares the pod's wireless backhaul to the gateway. Main win is stability for Core, which is on Wi-Fi today. **Surface catch:** its one USB-C port is the charger and its one USB-A port feeds the mic/speaker splitter, so Ethernet needs either a USB-C hub with Ethernet and power passthrough, or charging through the Surface Connect port to free USB-C.

**Optiplex → Minecraft server (doable now, over Wi-Fi).** Paper (Java) + GeyserMC + Floodgate so Bedrock friends join a Java server; MCXboxBroadcast so consoles see it; playit.gg for friends outside the house (no router port forward). Reserve the Optiplex's IP on the Xfinity gateway first (its DHCP address drifted before). Upgrade RAM to 16 GB (DDR4 SO-DIMM) before running anything real: it has ~4 GB.

**Plex off the NAS: not on the Optiplex.** The real fix is Plex Media Server on a box with hardware transcoding reading media from the NAS; the Optiplex is Wi-Fi-only and its Skylake Quick Sync can't decode HEVC 10-bit/4K HDR, so wait for new hardware. Needs Plex Pass (check whether Nick has it). When it moves: copy Plex's config, keep the same media paths, and repoint JARVIS (`PLEX_URL`), Tautulli, letterboxd-plex-sync and Cleancut.

**Future homelab (after moving out).** Mini rack; NAS keeps the arr stack and storage; a Mac mini (Apple Silicon, lots of unified memory) runs Plex and a local LLM. Plan JARVIS as **hybrid, ~90% local by volume**: wake word, STT, TTS, quick commands, memory search and background summaries local; Claude for multi-step tool chains, Donna and judgment calls. UPS with NUT keeps modem, router, Wi-Fi and the JARVIS box up.

### Bedroom blind tilt automation (planned 2026-11-13, waiting on hardware, nothing built)

2" faux-wood venetian, **cord tilt**, square metal tilt rod. Tilt only.

- **Hardware (Nick buys):** a wand tilter matching the rod (measure the square bar, likely 1/4" or 5/16", and the headrail's inside width) plus a **SwitchBot Blind Tilt** (BLE only, no hub). **Keep the original tilter** (it's his parents' blind).
- **Chosen wiring, Option A:** JARVIS drives it from the **Surface's Bluetooth** with `pySwitchbot`, as an `integrations/` module + tool ("open/close the blinds", "tilt to 50%"), usable in scenes and the wake-up, mirrored to HA. Option B if A is flaky: an ESP32 ESPHome Bluetooth proxy near the window.
- **Before building:** `bluetoothctl show` on the Surface and check BLE range. Nick asked to wait until the SwitchBot is in hand.

### Nightstand iPad mini (planned 2026-11-13, waiting on hardware, nothing built)

A free iPad mini from work **replaces the Google Nest Hub on the nightstand**; the iPad Air stays as the room panel by the door; the desk (Surface, mic, speaker) is ~17 ft away.

- **Phase 1 (no new code):** companion app, plugged in, Guided Access on the Room screen. Check the dim night look, movie mode, camera view, scenes. List what the Hub still does that the mini doesn't (alarms, a clock readable from bed, timers).
- **Phase 2, voice satellite:** the app listens for "Hey JARVIS" while on screen, streams the utterance to Core, plays the reply on the mini. **Arbitration:** when the desk mic and the mini both hear the wake word within ~1 s, the louder/closer one answers.
- **Open questions:** does the mini replace the Hub's alarm? Should the door iPad also be a satellite? Wake word on iOS: openWakeWord in Core ML vs. Apple's on-device speech.

### Voice ID and "he just knows I'm talking to him"

- [ ] **Re-enrol voice ID on the Surface** (it scores Nick as a guest there, 0.41/0.46 vs 0.55). Nick said hold until he has the better mic and the Surface sits at his desk. Then watch scores a few days, test his sister (her score sets the threshold), tune `VOICE_ID_THRESHOLD`. Money tools by room voice now need a positive voiceprint match, so this matters for trading by voice.
- [ ] **Better room mic**: a far-field array (ReSpeaker XVF3800 or similar) beats the CMTECK for across-the-room speech and voice ID.
- [ ] **Always listening, and he knows when Nick is talking to him** (asked 2026-11-06): addressee detection on the follow-up transcript (cheap classifier or Haiku: "is this said to JARVIS?"), and close the follow-up window when more than one voice is heard.
- [ ] Guest refusals without a model call ("Sorry, only Nick can do that") when a guest's command maps to a locked tool; a "who am I talking to?" line in the iPad's voice overlay; maybe per-person profiles later.
- [ ] **Leave home → lights out / Peace**: check whether his iPhone shows in HA as `person.*` / `device_tracker.*` (HA Companion app); if so, a runtime monitor runs the scene after a few minutes `not_home`, with a push. Same signal could arm the room sentry.
- [ ] **Baby motion alert (low priority, Nick isn't sure)**: a ~$15 PIR sensor in HA is simplest and most private; the iPad diffing frames on-device is the alternative.

### Schwab and investing

- [ ] **Fractional orders were REJECTED live** (2026-11-10, order 1008619430771, `CANCELED_BY_FIRM`, "No trades are currently allowed"). Find out whether it's fractional-only or the whole account (ask traderapi@schwab.com, or try one whole share once Nick holds one). Every holding is under a share today.
- [ ] **Parked, Nick likes it ("wait till later"):** swap to low-priced Schwab ETFs (SCHX ~$29 for VOO, SPYM ~$88, SCHG ~$34, SCHB ~$29) so JARVIS trades whole shares and carries leftover cash. Test first: one SCHX share through `place_trade`.
- [ ] Untested live: `account_hash`, the order `Location` header id, `quotes`/`price_history` parsing, cash vs. margin (`cashAvailableForTrading` vs. `cashBalance`), unsettled sale proceeds counting toward a following buy.
- [ ] Amazon and SpaceX: Nick named them as holdings but the API doesn't show them. Another account, unticked on the hub's consent screen, or not bought yet?
- [ ] Memory extractor saved a stale fact (id 754514f8, "pending confirmation with code 1957"): forget it.
- [ ] Next when Nick says "go full crazy mode": playground + Coopa accounts and the $20/$5 split (`INVESTING_MAIN_ACCOUNT`); orders through the Schwab hub with hub-enforced limits; Approve/Deny on push notifications (WAKE_UP-style actions) for background-agent proposals; the playground's real account replaces the paper book.

### Room, scenes and screens

- [ ] **Plex movie picker in iPad movie mode** (asked 2026-10-29): grid of Plex posters with search; a tap starts it on the TV's Plex app (Plex client `playMedia` or HA `media_player.play_media`). Needs Core endpoints for the library and play.
- [ ] Govee effects (Aurora, Candlelight, Starry Sky...) in the light sheet.
- [ ] Scenes on the iPhone Home Screen / Control Center (widget target).
- [ ] iPad Room tab: a now-playing / music card.
- [ ] "Hey Google, game time / peace" → `scene.game_time` / `scene.peace` (needs HA linked to Google Home).
- [ ] Answer screens, later cards: a movie's details, the TV remote, sending a card only to the screen nearest the voice that asked.
- [ ] **Progress bar on the iPad for anything running** (2026-11-02): long jobs (Cleancut first, then downloads) publish progress events on the bus; the iPad Room tab shows them.
- [ ] Desk lights: if the desk still disagrees with the app after Goodnight/Peace, point the HA scenes at the LAN entity `light.desk_lights` and keep only the DIY-scene selects on the cloud one.
- [ ] **Room sentry on the work iPad** (2026-10-27): on-device person detection while "armed" (an ARM button by the door, DISARM / Face ID on the way in) → Barn Door + one line spoken on TV, iPad and the room speaker; stand down after ~30 s empty. Tell the household it exists. Camera Face ID gate is in the memory backlog too.
- [ ] **Whole-house Barn Door** ("far off"): glass-break / door sensors in HA; a night break-in puts every room into Barn Door and plays Back in Black.

### Media and NAS

- [ ] **Cleancut: wake the Windows PC to process movies** (2026-11-02; no code yet). Cleancut is Nick's own filter, already worked out; **don't design or rebuild it**. JARVIS only does the PC side: Cleancut's own program calls a JARVIS endpoint (e.g. `POST /api/pc/wake`, scoped client token) → Wake-on-LAN from the Surface → poll the PC's SSH (20–40 s, time out cleanly) → run Cleancut as a CLI job over SSH → PC reads/writes the NAS over SMB → sleep when the queue is empty (sleep, not shutdown). Own module, job queue, logged. **Ask Nick first:** is the PC on Ethernet? motherboard/NIC model? Cleancut's command line and progress output? where output goes and how Plex names it? PC setup: WoL in BIOS (ErP off), NIC "Wake on Magic Packet", Fast Startup off, fixed IP on the PC, OpenSSH Server with JARVIS's key, SMB mount.
- [ ] **Letterboxd** (2026-10-27): pull Nick's diary/ratings (RSS) so JARVIS knows what he's watched and liked, tied into Radarr requests.
- [ ] **Nick's top 5s** (2026-10-27): his top fives (films, albums, etc.) as standing knowledge Nick can edit.
- [ ] NAS: allow-listed, confirm-gated "update one container" action (pull + recreate one named container from compose); per-container restart needs a new `recover_stack.sh` verb, i.e. a permission change: ask first.
- [ ] Put the bus alert id in the APNs payload so tapping a push opens that incident report.
- [ ] `download_stalled` incident reports should call `media_doctor.diagnose(title)` for that title (today it runs without one).

### JARVIS TV

v1 shipped 2026-10-27 and is the TV's home screen (JARVIS.md "JARVIS TV"). Open:
- [ ] Updates itself from Core (or a one-command sideload) so it never needs a cable.
- [ ] Same persona: greetings, alerts and briefings in JARVIS's voice through Core.
- [ ] Same data, same rules: to-dos, assessments, maintenance and alerts from the same Core endpoints, checked off everywhere at once.
- [ ] Today line: work shift and birthdays (needs a source: Google Calendar? work app?).
- [ ] Remote's play/pause works anywhere in the launcher (today only on the wake screen).
- [ ] House: light/switch tiles like the phone's Hub; timers and reminders as toasts that go off audibly.
- [ ] Ambient mode after idle (clock + reactor + next thing); sleep timer; screen off at night with critical alerts still allowed.
- [ ] Wake-up options once Nick has lived with it ("idk what i want yet"): snooze length, a gentler start.
- [ ] TV LED under the screen still flashing overnight? If so, close the SSE stream on screen-off.

### Loose ends and small debt

- [ ] Add the Surface's extra packages to `requirements.txt` (distro, httpcore, httptools, httpx, pathvalidate, piper-tts, pyee, uvloop, watchfiles).
- [ ] `ios/install.sh` prints ✓ even when a device's install fails (the echo runs after the `&&` chain); make it print ✗.
- [ ] Document `/api/camera/leave` and `/api/device/battery` in JARVIS.md.
- [ ] Time-sensitive wake push: Nick accepts the updated Program License Agreement at developer.apple.com, then add `com.apple.developer.usernotifications.time-sensitive` to `JarvisCompanion.entitlements`. Until then Focus/Sleep can hold the wake-up push.
- [ ] Room voice: start speaking the first sentence of long answers sooner (skipped: spoken replies are one sentence).
- [ ] Reminders only fire while something pops them (room voice in server mode); a Core without the room voice never sends them.
- [ ] Playwright on the Surface (`pip install playwright` + `python -m playwright install --with-deps chromium`) if JARVIS should click and fill forms; without it he reads pages over plain HTTP.

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
  `python -m unittest`, report pass/fail, and only on
  Nick's approval of the id merge it and hot-reload the affected module
  (`importlib.reload` for tool handlers; a restart for anything else).
- Rollback: keep the pre-patch commit hash; `rollback_patch` resets to it.
- Start with the smallest useful case: letting JARVIS add a *new tool* that
  wraps an existing integration, because it cannot break existing behaviour.

### 2. Reflection after failed turns *(shipped 2026-11-13)*
**Shipped** as `brain/reflector.py` + `brain/lessons.py` (see JARVIS.md
"Lessons from failed turns"). Follow-ups: watch jarvis.log for
`Reflector flagged` false alarms and tune the heuristics; a companion view of
lessons if Nick wants one (today: `list_lessons` / `forget_lesson` by voice);
let lessons feed Tier 2 #5 self-tuning when one names a setting.

Original spec: JARVIS should learn from turns that went badly, not only from explicit
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

A tiny supervisor (launchd on the Mac, systemd on the Surface) that restarts
the process on crash, plus an in-process watchdog thread that detects a
hung turn or dead monitor thread, records it in `tool_health`, and restarts
the monitor. Pair with the health dashboard so restarts are visible.

## Tier 3: nice to have

- **Email, after the triage subagent (shipped 5.89)**: Nick links Gmail
  (`python -m integrations.gmail --auth`, JARVIS.md "Gmail setup"), makes the
  `Mission Emails/<name>` labels (linked and live 2026-11-10; first pass
  sorted 24 mails cleanly), and ideally runs a day with
  `EMAIL_TRIAGE_DRY_RUN=true`. Then: kept-mail summary in the morning
  briefing; "what did <person> say" / draft a reply by voice (send behind the
  confirm gate); a weekly "inbox cleanup" routine.
- **More subagents on `brain/subagents.py`**: research (web search to a vault
  note), shopping (price_watch + comparison), scheduled routines Nick sets by
  voice ("every Sunday, clean my inbox and tell me what's due"). Each gets
  only its own tools and handler-side guards.
- **Ambient music control** (roadmap): auto-dim on speech.
- **Double-clap wake** (roadmap).
- **Local model routing phase two** (roadmap): route more tool calls to the
  local model with a confidence gate and fall back to Claude.
- **Session summaries surfaced on the phone** so a chat picks up context.

## Known debt to clear when convenient

Ordered as in `JARVIS.md` "Performance debt":
- UI panels poll and rebuild; move to runtime change events.
- Per-turn SQLite housekeeping; shared connection, prune every N turns.
- Streaming still opt-in (`STREAMING_TTS=false`); make it default once
  trusted.

## How to spend a short session on this repo

1. Read `SELF_HEALING_HANDOFF.md` and clear its checklist first if unreviewed.
2. Pick one Tier 1 item. Ship sensing and proposal first, application second.
3. Add tests in `tests/` using the `patch.object(module, "_FILE", tmp)`
   pattern from `tests/test_tool_health.py`.
4. Update `JARVIS.md` (file map, tools, Recently Shipped) and this file, bump
   the iOS version in `project.pbxproj`, commit.
