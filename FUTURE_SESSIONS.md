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

### Nightstand iPad mini (hardware in hand 2026-11-14)

The free iPad mini from work **replaces the Google Nest Hub on the nightstand**; the iPad Air stays by the door (the room panel and the camera's door angle); the desk (Surface, mic, speaker) is ~17 ft away. Nothing in Core still talks to the Hub except `media_player.nick_s_room_display` in `TV_MEDIA_PLAYERS` (drop it once the Hub is unplugged).

**Built in 6.35 (277), installed and in use since 2026-11-15 (the mini answers "Hey JARVIS" from bed):** per-iPad role (Nightstand / Door), movie mode on the mini only (the Air dims instead), two-angle camera (phone shows Door + Bed), dim night clock on the mini, "I'm awake, leave the lights" (alarm screen, push, voice). See JARVIS.md "Nightstand iPad mini".

Review checklist when home:
- [ ] Settings › General › VPN & Device Management on the mini: if work's MDM supervises it, erase and release it before installing (it could block the app, Guided Access, camera or mic, and work could see it).
- [ ] Mini: Room settings › Where it lives reads "Automatic (Nightstand)"; Air reads "Automatic (Door)". Keep screen on, Full screen, Guided Access, plugged in.
- [ ] Movie scene: the mini turns into the movie remote; the Air stays the panel and dims after 20 s, a tap brightens it.
- [ ] Phone › Room camera: both angles show (stacked in portrait, side by side in landscape), Both / Door / Bed switch, both green orbs on, Kill stops both.
- [ ] Lights off at night: the mini's dark screen shows the dim clock and next alarm; check it's readable from bed but not a night light (now `NightstandDarkClock`, heavy digits since 6.49, grey from Room settings › Dark clock).
- [ ] Alarm: "I'm awake, leave the lights" stops the TV music and the screen closes, lights unchanged; "I'm up" still runs good-morning lights. Say "I'm awake, leave the lights" during a backup ring.
- [ ] Keep the Hub plugged in until the mini has woken you for a week.

**Still to build:**
- **Mini as a second mic and speaker: built in 6.37 (279), not tried live.** Check on hardware: "Hey JARVIS" from bed answers on the mini, from the desk on the Surface; the mini doesn't hear its own replies (voice processing); barge-in and a tap to hush stop the mini; Room settings › Mic and speaker shows "Listening"; the Surface's CPU with two wake detectors. Tune `SATELLITE_ARBITRATION_MS` if the wrong device answers. Later: route all replies to the mini in quiet hours (Nick to confirm), voice ID quality from the mini's mic.
- [ ] **Nightstand screen (6.37) on the real mini:** the dark clock's brightness at backlight 0 (Room settings › Dark clock), the 6 s delay, a tap shows the panel for 20 s, Goodnight → dark in ~3 s, movie mode still takes over.
- [ ] **Mini's mic link flaps (seen 2026-11-15 ~01:32-01:34, after 6.41):** the Surface log showed `Voice satellite 'bed' disconnected` / `connected` every ~50 s. **Checked 2026-11-15 morning:** it stopped by 02:15 and held one socket until 08:40 (an app install), so it looks like it was the Core restarts and installs that night. Re-open only if it comes back: check the `bye {reason}` and "socket ended" lines in `journalctl -u jarvis-api` around a drop to see which side closes it (`SatelliteVoice.session` on the iPad, or the ping/writer tasks in `voice_satellite`, `app/server.py`).
- [ ] **Voice ID from the mini's mic scored Nick as a guest** (0.44 and 0.53 vs 0.55, 2026-11-15 08:17). Per-mic prints are built (6.51): from bed say "Hey JARVIS, learn my voice", then the 5 sentences with the mini answering; then watch `Voice ID: Nick (score … on the bed print …)` in `jarvis.log`.
- [ ] **Desk timing (built 6.51):** Room settings › Mic and speaker › Desk timing, Later if the iPad trails, Earlier if the desk does, until it's one voice.
- [ ] **Camera (6.51):** the Bed angle is upright on the phone in both landscape ways the mini can stand, the Door angle still upright; nothing covers a picture; Door or Bed alone is centred.
- [ ] **Backup alarm (built 6.51, untested):** set a one-off alarm 3 min out, stop Core (`sudo systemctl stop jarvis-api`, passwordless after `deploy/surface-setup.sh`), and check the mini beeps about a minute after the time, the screen comes up, Snooze rings again 9 min later and I'm up stops it; start Core again. Loud enough from bed? Then the Hub can go.
- Open questions for Nick: portrait or landscape on the nightstand; did the Hub do anything else he'd miss (sleep sounds, photo frame, Assistant routines).

### Voice ID and "he just knows I'm talking to him"

- [ ] **Re-enrol voice ID on the Surface** (it scores Nick as a guest there, 0.41/0.46 vs 0.55). Nick said hold until he has the better mic and the Surface sits at his desk. Then watch scores a few days, test his sister (her score sets the threshold), tune `VOICE_ID_THRESHOLD`. Money tools by room voice now need a positive voiceprint match, so this matters for trading by voice.
- [ ] **Wake word misses and false fires** (Nick, 2026-11-19: "sometimes Hey JARVIS doesn't hear me the first time; sometimes I'm not saying anything and he activates; Hunger Games set him off"). Shipped: the TV filter (`VOICE_TV_FLOOR`, `speech/room_voice._sounds_like_the_tv`): while the TV plays, a turn that scores under 0.30 on voice ID and never says "Jarvis" is dropped silently (the film scored 0.13-0.21, Nick 0.44+). Next: watch `Room voice ignored` lines in jarvis.log; misses leave no log line today, so log near-misses (scores 0.4-0.6) to see if `WAKE_THRESHOLD`/`WAKE_CONSECUTIVE` should drop on the satellites; consider a custom-trained openWakeWord "Hey JARVIS" on Nick's own recordings (the dataset in ~/jarvis-voice could help) and ducking/AEC against the TV's audio.
- [ ] **Better room mic**: a far-field array (ReSpeaker XVF3800 or similar) beats the CMTECK for across-the-room speech and voice ID.
- [ ] **Always listening, and he knows when Nick is talking to him** (asked 2026-11-06): addressee detection on the follow-up transcript (cheap classifier or Haiku: "is this said to JARVIS?"), and close the follow-up window when more than one voice is heard.
- [ ] The goodnight alarm *answer* isn't voice-checked (whoever answers right after Nick's goodnight); a guest's goodnight already skips the question (2026-11-14).
- [ ] Guest refusals without a model call ("Sorry, only Nick can do that") when a guest's command maps to a locked tool; a "who am I talking to?" line in the iPad's voice overlay; maybe per-person profiles later.
- [ ] **Leave home → lights out / Peace**: check whether his iPhone shows in HA as `person.*` / `device_tracker.*` (HA Companion app); if so, a runtime monitor runs the scene after a few minutes `not_home`, with a push. Same signal could arm the room sentry.
- [ ] **Baby motion alert (low priority, Nick isn't sure)**: a ~$15 PIR sensor in HA is simplest and most private; the iPad diffing frames on-device is the alternative.

### Schwab and investing

- [ ] **Fractional orders were REJECTED live** (2026-11-10, order 1008619430771, `CANCELED_BY_FIRM`, "No trades are currently allowed"). Find out whether it's fractional-only or the whole account (ask traderapi@schwab.com, or try one whole share once Nick holds one). Every holding is under a share today.
- [ ] **Parked, Nick likes it ("wait till later"):** swap to low-priced Schwab ETFs (SCHX ~$29 for VOO, SPYM ~$88, SCHG ~$34, SCHB ~$29) so JARVIS trades whole shares and carries leftover cash. Test first: one SCHX share through `place_trade`.
- [ ] Untested live: `account_hash`, the order `Location` header id, `quotes`/`price_history` parsing, cash vs. margin (`cashAvailableForTrading` vs. `cashBalance`), unsettled sale proceeds counting toward a following buy.
- [ ] Untested live since the 2026-11-14 safety pass: `accountNumber` on `/trader/v1/orders` entries (the open-order account filter and the lost-answer lookup rely on it); the "outcome unknown" reply after a Schwab timeout; whether `cashAvailableForTrading` already includes the filled part of a still-open sell, which `_in_flight_cash` would then count twice.
- [ ] Amazon and SpaceX: Nick named them as holdings but the API doesn't show them. Another account, unticked on the hub's consent screen, or not bought yet?
- [ ] Memory extractor saved a stale fact (id 754514f8, "pending confirmation with code 1957"): forget it.
- [ ] Next when Nick says "go full crazy mode": playground + Coopa accounts and the $20/$5 split (`INVESTING_MAIN_ACCOUNT`); orders through the Schwab hub with hub-enforced limits; Approve/Deny on push notifications (WAKE_UP-style actions) for background-agent proposals; the playground's real account replaces the paper book.

### Room, scenes and screens

- [ ] **NEXT UP: calm iPad orbs like the phone** (asked 2026-11-19; delete this line once shipped). The iPads' Room orb fires a thought for every house signal (`RoomView` holds `TrafficFeed`, which drives `NeuralCore`), so it looks busy; the phone's Home orb doesn't subscribe and stays calm, reacting only to listening/thinking. Nick likes the phone's. Make the iPads (mini and Air) calm the same way, ideally a Room settings toggle ("React to house traffic", default off) so he can turn it back on.
- [ ] **GPS presence for "away"** (asked 2026-11-17): the room goes quiet today when Peace ran and every light is off (`status_hub.room_away`). Use the iPhone's location (HA `person.nick` / companion app zone, or a geofence in JarvisCompanion) as the real away signal, so the room mutes when Nick leaves whatever the scene, and wakes up again when he's home.
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

- [ ] Time-sensitive wake push: Nick accepts the updated Program License Agreement at developer.apple.com, then add `com.apple.developer.usernotifications.time-sensitive` to `JarvisCompanion.entitlements`. Until then Focus/Sleep can hold the wake-up push.
- [ ] Room voice: start speaking the first sentence of long answers sooner (skipped: spoken replies are one sentence).
- [ ] Reminders only fire while something pops them (room voice in server mode); a Core without the room voice never sends them.
- [ ] Playwright on the Surface (`pip install playwright` + `python -m playwright install --with-deps chromium`) if JARVIS should click and fill forms; without it he reads pages over plain HTTP.

### Reliability pass (2026-11-14, Core only): check after deploy

See JARVIS.md Recently Shipped.
- [ ] The Michael (2026) temp-4K entry (expires Mon Nov 16 23:24) still removes on time, and waits if it's playing on Plex (the new `/status/sessions?includeGuids=1` call is untested against the real Plex).
- [ ] A wake-up backup ring plays at alarm loudness (`ROOM_ALARM_VOLUME`, 8) and the room goes back to its normal level after; snoozing while a one-off alarm is set keeps the one-off.
- [ ] "Stand down" with Barn Door off just says so.
- [ ] The investing review skips Thanksgiving (2026-11-26).
- [ ] Yearly chore: add the 2028 NYSE holidays to `_NYSE_HOLIDAYS` in `brain/investing.py` before 2028.

### Light sync overhaul (2026-11-15): physical acceptance checks

Clock overhaul implemented (Core + TV 9.11, companion 6.68/310). See JARVIS.md **Light sync**.
Remaining acceptance needs Nick's eyes or a camera: five launches without nudges
within ±50ms, a two-minute run without wobble, 1080p/4K profiles, pause/resume,
seek, Barn Door and physical light latency. Simulated clocks do not prove screen timing.
New Part builds may take several minutes the first time a version is played.
Live smoke verified Part 12929 at 30fps, TV source with no switches or dropped
reports, pause/resume, seek state updates and Done releasing the player. The
pre-final five-launch traces still showed startup residuals above ±30ms and
periodic ~1s MediaSession discontinuities. Native timestamps, affine clock
mapping, faster heartbeats and playing-state acquisition are now implemented;
the final acquisition correction was verified offline. Nick asked about the
repeated test playback, so the loop was stopped. No further video tests were
run, and the ±50ms visual / final live-drift acceptance remains unverified.
- [ ] Check `diagnostics`: drift within ±30ms after 3s; source remains TV.
- [ ] Calibrate each display format using the 180s irregular-cut clip; nudges now save the format.
- [ ] Measure independent strip/desk latency if needed through the per-light API.
One-time on the Surface, after pulling and before restarting Core:
`cd ~/jarvis && .venv/bin/pip install imageio-ffmpeg && .venv/bin/python -m app.client_tokens grant nicks-room-tv playback:write`
- [ ] "Hey Jarvis, create a light sync for Cars 2": the build starts, the iPad's Light sync menu shows "Building… N%", and a push says it's ready. Note how long it took on the Surface (the Mac took ~3 min with VideoToolbox; the Surface is CPU only). If it's too slow or Core feels sluggish meanwhile, lower `LIGHT_SYNC_BUILD_THREADS`.
- [ ] Movie scene on, play Cars 2 on the TV: the strip, desk lights and lamp follow within a second; the fan ring stops following the strip (its automation is paused).
- [ ] Pause holds the colour; resume picks up (one strip blip on resume is expected); a seek catches up within ~1 s.
- [ ] Stop playback: the lights go back to the Movie scene (no strip stuck in streaming mode) and the fan automation is back on.
- [ ] A film with no light sync in movie mode: nothing changes.
- [ ] Barn Door mid-film: everything goes red at once (strips included); stand down: the sync comes back.
- [ ] iPad menu: Stop, Start, Subtle / Medium / Intense (feel the difference), Auto off then on, "Make a light sync for <film>" on a film without one.
- [ ] A flashing scene stays gentle (flash limiter: at most 3 big jumps a second).
- [ ] "Start light sync" / "stop light sync" by voice.
- [ ] Restart Core mid-film: the strips are released (they shouldn't stay frozen on a colour).
- [ ] TV 9.7 deployed: `adb logcat -s TvHomePlayback` shows no 403s after the grant, and Core uses the TV clock (`GET /api/lightsync` film.source = "tv"). If the TV's Plex session has no numeric media id, Core matches by title (Plex sessions or the track index).
- [ ] If two Plex players are ever playing films at once, set `LIGHT_SYNC_PLEX_PLAYER` to the room TV's player name.
- [ ] **Calibrate once** with the test clip (companion 6.34): iPad movie screen › Light sync › Calibrate… (or "Jarvis, calibrate the light sync"). The clip starts on the TV (its track builds first if missing); nudge Lights earlier / later until the strip flashes with the picture, then Done. Check the TV actually starts the clip from the deep link and that Done stops it (`MEDIA_STOP`); if Plex resumes mid-clip that's fine (flashes repeat every 2 s). Note the video-format offset you settle on.
- [ ] **Queue with 2-3 films**: "make light syncs for all the Hunger Games movies": the first builds, the rest say "Queued …, second in line"; the iPad menu shows "Building … N%" and "Next: …" rows; one push per film. Restart Core mid-build: the queue picks up again (the interrupted film from scratch).
- [ ] Per-film nudge while a film syncs (menu Lights earlier / later, Reset timing; or "the lights are late"): lands within a frame, survives a restart.

Remaining prototype open items (moved from `~/Developer/projects/light-sync/README.md`):
- [ ] **Fan ring** (`light.nicks_fan`, Tuya, cloud): about 1-2 updates a second with ~0.5 s lag; send only bigger shifts, colour only (never white), through HA.
- [ ] **Strip knocked back to its rainbow** by something external (the Movie scene, `automation.fan_light_follows_strip` or the Govee cloud): light sync now pauses the automation and re-takes on hard cuts; check the HA logbook if it still happens.
- [ ] **Re-encode the HEVC 10-bit films** that stutter on the TCL (Realtek decoder hands frames back out of order; ~185 of 240 frames dropped). Try a lossless fix of the ordering first, then HEVC 10-bit without B-pyramid at the same bitrate, then H.264. Test a 2-min clip on both TVs first.
- Keep `media/movies/Light Sync Test (2026)/` on the NAS: it's the light sync calibration clip now (`LIGHT_SYNC_TEST_TITLE`).

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

**Nick, 2026-11-19 (backburner, high on his list):** "I want JARVIS to
create new skills if he doesn't have them... instead of erroring out, or
even just storing it... eventually one day I don't wanna come to Claude to
adjust JARVIS." So the first slice: when a turn needs a capability no tool
covers, JARVIS says so plainly and files a **skill request** (what Nick
asked, when, why it failed) to a `memory/skill_requests.json` store visible
in the companion (not the to-do list); then this self-patch flow drafts the
tool for the most-asked requests and asks Nick to approve it.

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
