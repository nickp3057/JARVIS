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

### His own voice: a local Piper voice trained on Paul Bettany (planned 2026-11-10; Core wiring built 2026-11-14, training in progress)

Nick wants JARVIS to sound like Paul Bettany's JARVIS, for his own room only (never shared or published), and to stop paying ElevenLabs. Never clone it on ElevenLabs: their terms forbid cloning a voice without consent, and a ban would take STT with it. Chosen route: **plan B, fine-tune a Piper voice**; RVC voice conversion (plan A) is the fallback if Piper sounds too flat, built from the same dataset.

- **Training box:** Nick's Windows PC (RTX 3060 12 GB), only while training; WSL2 + CUDA, since Piper training on plain Windows is painful. Check where Piper's training code lives now (rhasspy/piper was archived; the successor repo moved) before writing setup steps.
- **Audio, mostly automatic:** the Plex library already has Iron Man (2008), Iron Man 2, Iron Man 3 and Avengers: Age of Ultron (NAS `/volume2/data/media/movies`). Missing and worth adding: The Avengers (2012), WandaVision (Vision is close to JARVIS), Civil War, Infinity War. Interviews would add his everyday voice, but weight the set toward the calm JARVIS delivery.
- **Dataset script (to write, runs on the PC):** take the 5.1 **center channel** (mostly dialogue) with ffmpeg → vocal isolation (Demucs) → Whisper large-v3 with timestamps → 2–12 s clips → speaker embeddings with the TitaNet model voice ID already uses (`models/speaker/nemo_en_titanet_small.onnx`, `brain/speaker_id.py`) → keep clips close to a few seed clips Nick confirms by ear (~10 minutes of his time) → LJSpeech-style `wavs/` + `metadata.csv`. Drop overlaps, music-heavy and heavily processed lines. Target 15–60 min of clean speech; fine-tune from a British male Piper checkpoint (en_GB medium) so less audio is needed. One night of training.
- [x] **Core wiring: shipped 2026-11-14** (JARVIS.md "Local Piper voice behind one setting"): swapping the trained voice in is `TTS_PROVIDER=piper` + `PIPER_VOICE_MODEL=/path/bettany.onnx` (with `piper-tts` installed in the Surface venv). Still open: the optional light EQ (hook noted in `speech/piper_voice.py`), and measuring first-audio latency on the Surface (`python -m speech.tts --say ...` prints it). Original plan: `TTS_PROVIDER=piper` + `PIPER_VOICE_MODEL` path; load `PiperVoice` once in-process (today's fallback shells out to `python -m piper` and reloads the model every line) and stream raw PCM into `aplay -t raw` like `_stream_elevenlabs`; works with the TTS cache, room streaming, barge-in/hush; also `speech/remote_voice.py` so the phone voice matches; ElevenLabs becomes the fallback. Optional light EQ for the film's digital sheen. `length_scale`/`noise_scale` as `.env` knobs.
- **Latency:** comparable to the streamed ElevenLabs path (~170 ms to first audio) and never depends on the internet; measure on the Surface.
- **Dropping ElevenLabs entirely** would also mean `STT_PROVIDER=local` (Whisper small.en on the Surface); measure it against Scribe's ~0.5 s before switching.

### Voice Studio / Voicebox as a test bench (asked 2026-11-12)

Nick saw a video on "Voice Studio" (likely VoiceStudio by debpalash, or Jamie Pine's Voicebox; both are local GUIs over several open TTS engines, incl. Qwen3-TTS, Chatterbox). Use it as an A/B bench on the PC's 3060, not as JARVIS's runtime: feed every engine the same cleaned ~15 s Bettany reference (`~/jarvis-voice/r2-tune/ref-jarvis.wav`) and judge by ear against "sounds like a real person" (the bar Piper r2 and Chatterbox missed). If one wins, wire that engine directly behind `TTS_PROVIDER`, served from the PC, with a fallback voice when the PC is off.

### Home network, Optiplex and homelab (asked 2026-11-12)

**Switch at the xFi pod (cheap, worth it now).** The pod outside Nick's room already wires his PC. An 8-port gigabit switch on the pod (an easy-smart one like TP-Link TL-SG108E if he wants VLANs later) wires the Surface (Core), the Mac when docked and the Optiplex. Caveat: everything on the pod still shares the pod's wireless backhaul to the gateway, so traffic to the NAS (wired at the gateway) is only as good as that link; traffic between devices on the switch is full gigabit. Main win is stability for Core, which is on Wi-Fi today. **Surface catch:** its one USB-C port is the charger and its one USB-A port feeds the mic/speaker splitter, so Ethernet needs either a USB-C hub with Ethernet and power passthrough, or charging through the Surface Connect port to free USB-C. Later: Ethernet or MoCA into the room replaces the backhaul.

**Optiplex → Minecraft server (doable now, over Wi-Fi).** Paper (Java) + GeyserMC + Floodgate so Bedrock friends join a Java server; MCXboxBroadcast so consoles see it in their friends tab (they can't add custom servers); playit.gg for friends outside the house (handles Bedrock's UDP 19132; consoles can't run Tailscale), no router port forward. Reserve the Optiplex's IP on the Xfinity gateway first (its DHCP address drifted before). Upgrade RAM to 16 GB (DDR4 SO-DIMM) before running anything real: it has ~4 GB.

**Plex off the NAS: not on the Optiplex.** Plex can't send only transcoding elsewhere (ClusterPlex exists but is fragile); the real fix is running Plex Media Server on a box with hardware transcoding and reading media from the NAS. The Optiplex is Wi-Fi-only and its Skylake Quick Sync can't decode HEVC 10-bit/4K HDR, so wait for new hardware. Hardware transcoding needs Plex Pass (check whether Nick has it). When it moves: copy Plex's config, keep the same media paths (`/movies`, `/tv`, `/data`) so nothing rescans, and repoint JARVIS (`PLEX_URL`), Tautulli, letterboxd-plex-sync and the CleanCut pipeline.

**Future homelab (after moving out).** Mini rack; NAS keeps the arr stack and storage; a "powerhouse" box runs Plex and a local LLM. A Mac mini (Apple Silicon, lots of unified memory) fits both: Plex runs natively with hardware transcoding, idles at a few watts. Plan JARVIS as **hybrid, ~90% local by volume**: wake word, STT, TTS, quick commands, memory search and background summaries on the local model; Claude for multi-step tool chains, Donna and judgment calls (local models are still weaker at reliable tool use). Fits the existing fallback router and per-job model picker. UPS with NUT: on battery, shut down non-essentials, keep modem, router, Wi-Fi and the JARVIS box up (internet only lasts as long as Xfinity's node has power).

### Bedroom blind tilt automation (planned 2026-11-13, waiting on hardware, nothing built)

Cheap and portable (Nick takes it when he moves out). **The blind:** 2" faux-wood venetian with **cord tilt** (two strings into a tilter box at the end of the headrail; the strings wind on a spool that drives a gear on the tilt rod) and a **square metal tilt rod**. Tilt only; raising and lowering stays manual.

- **Hardware (Nick buys):** a wand tilter matching the rod (measure the square bar flat-to-flat, likely 1/4" or 5/16", and the inside width of the headrail; search "2 inch faux wood blind wand tilter 1/4 square") plus a **SwitchBot Blind Tilt** (~$60–70, clips on the wand, solar). Swap the tilter in ~10 minutes; **keep the original** so it goes back exactly as it was (it's his parents' blind). The Blind Tilt is **Bluetooth LE only** (no Thread; SwitchBot's Matter goes through their hubs). No hub needed.
- **Why not other routes:** a motor on the bar won't work with the cord tilter still in place (its gearing won't backdrive); SOMA-style pullers are for beaded loop chains. Fallbacks if the swap is vetoed: tie the two cords into a loop around a DIY ESP32 + geared motor pulley stuck to the sill (no change to the blind), or buy a ~$15–30 wand blind and shelve the original.
- **Chosen wiring, Option A:** JARVIS drives it directly from the **Surface's Bluetooth** (Linux/BlueZ) with `pySwitchbot` (the library HA uses), as `integrations/` module + tool ("open/close the blinds", "tilt to 50%"), usable in scenes and the wake-up scene, and mirror its state to HA. HA can't see it natively because HA runs in Docker on the NAS, which has no usable Bluetooth. Option B (if A is flaky or out of range): a ~$10 ESP32 flashed as an ESPHome Bluetooth proxy near the window, so HA picks it up as a cover entity with no new JARVIS code.
- **Before building:** `bluetoothctl show` on the Surface (`Powered: yes`) and check it's within BLE range of the window. Nick asked to wait until the SwitchBot is in hand to test.

### Nightstand iPad mini (planned 2026-11-13, waiting on hardware, nothing built)

Nick is getting a free iPad mini from work. Plan: it **replaces the Google Nest Hub on the nightstand**; the iPad Air stays as the room panel by the door. The desk (Surface, the current mic and speaker) is at the other end of a ~17 ft room.

- **Phase 1 (no new code):** install the companion app, keep it plugged in, Guided Access on the Room screen. Check the dim night look (no pulsing or flashing, see Nick's taste), movie mode, camera view, scenes. Find what the Hub still does that the mini doesn't (alarms, a clock face readable from bed, "Hey Google" timers) and list it.
- **Phase 2, the mini as a voice satellite:** the app listens for "Hey JARVIS" on device while it's on screen (iOS only allows the mic in the foreground, fine for a plugged-in panel), streams the utterance to Core (`/chat/stream` or a new room-voice endpoint), and plays the reply on the mini's speaker. **Arbitration:** when the desk mic and the mini both hear the wake word within ~1 s, Core lets the louder/closer one answer and the other stays quiet (compare wake scores or RMS). Reuse the room voice's interrupt (tap to stop) and night volume. Decide whether replies to the mini also play on the desk speaker (probably not).
- **Open questions for Nick:** does the mini replace the Hub's alarm (needs a reliable wake-up sound with the app in front, or use iOS Clock)? Should the door iPad also become a satellite? Wake-word model on iOS: openWakeWord ported to Core ML vs. Apple's on-device speech recognition watching for the phrase.

### Speed pass (2026-11-09, 5.98 / build 241): check
Full audit and fixes in JARVIS.md "Recently Shipped" (5.98). Tested and timed
against the real services, but **not deployed and never played on the
speaker**. After `git pull` + `sudo systemctl restart jarvis-api` and an app
install:
- [ ] Say "Hey JARVIS, what time is it?" and a longer question. Speech should
  start noticeably sooner and sound the same (no click, no clipped first
  word, no stutter). If anything is off: `TTS_STREAM_PLAYBACK=0` in the
  Optiplex `.env` (read live, no restart) goes back to whole clips.
- [ ] `grep "first audio" ~/jarvis/jarvis.log | tail` should show
  `4. TTS first audio (ElevenLabs stream): ~150-250 ms` (was `TTS synthesis
  ... 400-1000 ms`). A `Streamed playback failed` warning means aplay
  refused the raw stream on that device; it will have fallen back.
- [ ] Barge-in ("Hey JARVIS" while he talks) and a tap-to-hush still cut him
  off at once, and he doesn't repeat the line afterwards.
- [ ] A routine line said twice (e.g. "Sleep well, sir.") still replays
  instantly the second time (streamed lines are cached from the PCM).
- [ ] Voice ID still logs `Voice ID: Nick (score ...)` for Nick and locks
  tools for a guest.
- [ ] Room camera: open it on the phone; the iPad's green orb should appear
  within about a second and go off within ~6 s of closing (or at once on
  "kill the camera"). Then `journalctl -u jarvis-api --since "-20 min" |
  grep -c camera/status` should be near 60, not 725.
- [ ] Study alerts still arrive in the morning (the brief is now fetched on
  change / every 10 min, not every 5 s).
- Not done, by choice: `VOICE_SILENCE_END_MS` stays 1000 (750 cut Nick
  off); the room voice still waits for the full one-sentence reply; the 134
  tool schemas are untouched (JARVIS.md "Performance debt").

### Real Schwab trading + Approve/Deny buttons (2026-11-10, companion 6.6 / build 248): check
Nick: "enable jarvis trading on my charles schwab" and "if jarvis needs
approval, give me an approve or deny button". JARVIS.md "Trading" and
"Approve / Deny buttons in chat". Built against fakes; no live order sent yet.
- [ ] Deploy Core + install 6.6 on the iPhone and iPad. In chat: "place P3
  and P4" (approved 2026-11-10: sell $27 SPCX, buy $27 VOO). Expect two cards
  with the exact order; tap Approve on P3 first. Check the order in the
  Schwab app and `memory/trades.json`.
- [ ] **Fractional orders were REJECTED live** (2026-11-10 10:12 MST, order
  1008619430771, sell 0.1914 SPCX at market: `CANCELED_BY_FIRM`, "No trades
  are currently allowed"; market open, SPCX `securityStatus` Normal). Find out
  whether it's fractional-only or the whole account (ask traderapi@schwab.com,
  or try one whole share once Nick holds one). If fractional is the block,
  the API can't trade Nick's account at all today: every holding is under a
  share and VOO is ~$600.
- [ ] **Parked idea, Nick likes it but said "wait till later" (2026-11-10):**
  swap to low-priced Schwab ETFs so JARVIS can trade whole shares. Prices
  that day: SCHX $29 (stands in for VOO $687), SPYM $88 (S&P 500), SCHG $34
  (stands in for AAPL/NVDA/TSLA), SCHB $29. JARVIS would buy whole shares only
  and carry leftover cash forward. Nick sells VOO once in the Schwab app (the
  API refused a fractional sell). Test first: one SCHX share through
  place_trade, to tell "fractional refused" from "account can't trade by API".
  Other options weighed: JARVIS hands Nick fractional orders to place in the
  app; a dollar-amount API broker (e.g. Alpaca) for the $5/month playground.
- [ ] Memory extractor saved a stale fact (id 754514f8, "pending
  confirmation with code 1957"): forget it.
- [ ] **(was) Fractional orders:** VOO is ~$600 a share, so P4's $27 is a
  fractional market order. Whether Schwab's Trader API accepts fractional
  quantity is unconfirmed; if it refuses, the reply quotes Schwab. Options
  then: ask traderapi@schwab.com, or whole shares only (most of Nick's
  ~$250 account can't be traded that way).
- [ ] P4 needs P3's sale money: the buy counts open Schwab sell orders as
  cash. If P3 fills before P4 is approved, check `cashAvailableForTrading`
  includes the unsettled proceeds (cash account) so P4 isn't refused.
- [ ] `account_hash` (`/trader/v1/accounts/accountNumbers`) and the order's
  `Location` header id were written from Schwab's docs, untested live.
- [ ] Is Nick's account cash or margin? `trade_cash` falls back to
  `cashBalance` when there's no `cashAvailableForTrading`.
- [ ] Buttons: every confirm-gate proposal (scenes, stack fixes, media
  requests) now shows a card too. Tap Deny once to see "Denied: …".
- Next: Approve/Deny on push notifications (WAKE_UP-style actions) for
  proposals from the background agents, and the playground's real account.

### Investing agents + Agents map (2026-11-09, 5.92 / build 235): check
Plan agreed with Nick 2026-11-09 (JARVIS.md "Investing"). Suggestions only:
**no real orders** until Nick says "go full crazy mode".
- [ ] Deploy, then within ~2 min the "suggest weights" job should run and push
  "Portfolio weights to approve". Read it; Nick approves ("approve the
  weights") or edits. His live account 2026-11-09: VOO $83, NVDA $62, TSLA
  $26, AAPL $20, INTC $12, $50 cash (total $253, -11.7% overall). He named
  Amazon and SpaceX as holdings but neither shows in the Schwab API: ask
  whether they're in another account, unticked on the hub's consent screen,
  or not bought yet.
- [ ] The $50 cash is uninvested: the first review should propose where it goes.
- [ ] Confirm `quotes`/`price_history` work live (the `/marketdata/v1/quotes`
  parsing was written from the docs, untested).
- [ ] Web search inside a subagent is new (`web_search_uses`, `pause_turn`):
  watch the first review's log for a 400 on the tool spec.
- [ ] Cost check after a week: Anthropic console spend for the Sonnet runs.
- [ ] Agents view on iPad (Room menu > Agents) and phone (Memory tab toolbar).
- Next, when Nick says go: (1) he opens the playground account and a separate
  Coopa account, splits the $25 transfer $20 main / $5 playground, and sets
  `INVESTING_MAIN_ACCOUNT`; (2) stage 2 orders through the Schwab hub with
  hub-enforced limits (cash only, per-trade/day caps, kill switch, main account
  orders only after Nick's Approve tap, playground automatic); (3) approve and
  decline buttons for proposals in the companion (push actions like WAKE_UP);
  (4) the playground's real account replaces the paper book (carry the
  journal and scorecard over).

### Tap to hush the room voice (2026-11-06, 5.87 / build 230): check

- Deploy Core + install 5.87 on the iPad, then with friends over: say "Hey
  JARVIS", let him start talking, tap the iPad. He should stop at once, the
  words go away and he must not answer the room's chatter afterwards. Also
  tap while "One moment…" shows: the answer should never be spoken.
- Nick's real goal (asked 2026-11-06): **always listening, and he just
  knows when Nick is talking to him.** Building blocks already here: voice
  ID (`brain/speaker_id.py`, `VOICE_ID`) to ignore guests in follow-ups;
  next would be addressee detection (a cheap classifier or Haiku call on the
  follow-up transcript: "is this said to JARVIS?"), and closing the
  follow-up window when more than one voice is heard.

### Room rainbows + iPad scenes (2026-11-04, 5.78 / build 221): check

- Nick: make a Smart Life Tap-to-Run "Fan Light Rainbow" (fan light, Scene
  mode, its rainbow), then on the Optiplex `git pull`,
  `.venv/bin/python ops/optiplex/room_rainbows.py`, restart jarvis-api.
- Check the ring really goes multi-colour (not one hue) from the Tap-to-Run;
  if Smart Life's action can't pick the ring's scene, the fallback is local
  control (tinytuya + the device's local key) to write its scene DP.
- Good Morning / Study / Game Time / Rainbow: lamp on in Rainbow. Movie:
  fan ring on with the strip. Barn Door: ring stays dark, comes back after.
- iPad: background the app on the Room tab and come back; scenes and the
  lit tile should refresh (server log shows `/api/home/scenes` again).

### Phone live voice (2026-11-03, 5.77 / build 220)

Nick chose live phone transcription to HQ over raw microphone uploads. Hold the
phone Home orb to speak, release to commit once; HQ receives partial text live
and streams its existing voice and reply text back. Core remains on the
Optiplex. Native cancellation, session/request identity, streamed PCM playback
and text-only fallback are implemented. No NAS access changes. Keep the phone’s
single tap inert and triple-tap Barn Door. Core deployed and restarted after 74 host regressions passed. Full local Python
suite: 961 tests, 12 skipped; iOS simulator: 38 passed. Live WebSocket smoke
verified partial acknowledgement, cancelled capture with no saved turn, real
streamed PCM (57,344 bytes), completed history, and same-ID recovery. First text
1.61 s, first audio 2.64 s in that test; temporary conversation removed.
Core backup: `/tmp/jarvis-live-voice-backup-20261103-175221` on the Optiplex.
Original memory/profile changes and untracked `app/api.py` were preserved.
Backend patch includes the earlier alert review fixes; no NAS changes.
Signed 5.77 (220) installed on Nick’s iPhone 17 Pro. On-device hold/microphone
interaction still needs Nick’s check; iPad stays on 5.76.

### Chat chrome and orb taps (2026-11-03, 5.76 / build 219)

Nick asked to remove the phone orb’s one-tap briefing and the bars appearing
around Chat. One tap now does nothing; triple-tap Barn Door remains. Chat’s
header/composer surroundings are transparent and the online connection strip
is hidden while idle. Phone tabs stay full height and hide during keyboard
presentation instead of rising into the typing area. Hold-to-talk was subsequently implemented in 5.77 (see above).
All 35 iOS simulator tests passed; Chat and keyboard renders inspected. Signed
5.76 (219) installed and reopened on Nick’s iPhone and iPad.

### Orb polish (2026-11-03, 5.75 / build 218)

Nick liked the phone swipe update. Home’s orb is now slightly larger (190 pt,
was 170; accessibility layout stays 130). Room uses the same classic reactor
with ring strokes/tick depth capped at the phone reference and a lighter
waveform at larger sizes, keeping the iPad’s responsive size without heavy lines.
Signed 5.75 (218) installed on both Nick’s iPhone 17 Pro and iPad Air; the
existing visual snapshot test passed and the phone navigation render was checked.

### Phone swipe and alert review (2026-11-03, 5.74 / build 217)

Local fixes: native interactive phone tab slides replace the half-speed offset
and fixed-distance tab swap; idle tasks no longer restart every drag sample.
Repeat suppression quiets duplicate pushes while keeping alerts visible after
restart; resolved recurrences/escalations push again. Refresh checks actual arr
health and keyed NAS telemetry, never a same-source recovery note. Legacy NAS
warnings lacking condition keys stay until cleared or aged out. Validation: 951 Python tests (12 skipped), 34 iOS simulator tests passed;
rendered navigation layout inspected. Signed 5.74 (217) installed on Nick’s
iPhone 17 Pro via devicectl. Core changes were subsequently deployed with 5.77;
iPad was not updated. Check phone feel, keyboard/back gestures and cancelled
drags on device.

### Lamp, alerts, Schwab (2026-11-03, iOS 5.72, Core c1b6ecb..051688f): check

All pushed and installed on the iPhone and iPad; Core needs `git pull` and a
restart (Nick runs it). Tests pass (945); none of this was tried live.
- [ ] Govee lamp is `light.lamp` (swap done by Nick with
  `ops/optiplex/swap_lamp.py`). Its Rainbow button picks the built-in
  "Rainbow" from `select.lamp_scene`: confirm it's the rotating one Nick
  wanted. It isn't in HA's Rainbow scene yet (offer to add it).
- [ ] Barn Door now turns the lamp red and restores it (`brain/protocols.py`
  `_COLOUR`). Nick said not to test Barn Door that day; check next time he
  runs it.
- [ ] Needs attention ↻ Refresh (`brain/alert_checks.py`): fixed alerts drop
  off. Alerts raised before the restart have no `entity_id`/`torrent_hash`
  context, so offline-device and download-error ones from then stay until a
  restart empties the bus.
- [ ] Repeat alerts: one push per problem (`collapse_id`), one Hub row with
  "3×", repeat pushes within `ALERT_REPEAT_WINDOW_S` suppressed (5.74 retains visible history). Watch that a real
  second failure isn't swallowed.
- [ ] Schwab revoked the refresh token an hour after Nick's 10:55 login. Cause
  found 2026-11-08: Nick's Coopa Trada login (same Schwab user, other app)
  revokes JARVIS's and vice versa. Fix built: the Schwab hub holds one login and
  JARVIS and Coopa borrow it (JARVIS.md "Borrowed login"); go live per the
  schwab-hub README. Stage 2 (later): JARVIS places orders through the hub with
  a phone Approve tap and hub-enforced limits. Now it counts as logged out
  and alerts "ended the login early". If it keeps happening, check the app's
  status on developer.schwab.com.
- [ ] qBittorrent 5 pause/resume (`/torrents/stop`/`start`) untested on the
  NAS: try "pause <torrent>" once.
- [ ] `ios/install.sh` prints ✓ even when a device's install fails (the echo
  runs after the `&&` chain); on 2026-11-03 the iPhone silently stayed on
  5.60. Make it print ✗ on failure.

### Room camera + Barn Door polish (built 2026-10-31 from work, untested on hardware)

All three asks are in iOS 5.40 (type-checked, not run on a device). At home,
install on the phone and iPad, then check:

- [ ] **Slide-to-activate Barn Door** (`SlideToEngage` in
  `RoomCameraView.swift`): drag the red block fully across to engage; let go
  early and it springs back. Stand-down is a plain tap. The reactor
  triple-tap (`barnDoorTaps`) is unchanged; decide if it needs a slide too.
- [ ] **Green orb replaces the iPad reactor**: `RootView` passes
  `camera.watching` down as `\.cameraWatching`, and every `JarvisCoreMark`
  draws `CameraWatchOrb` instead of the blue reactor. Floating overlay gone.
- [ ] **Barn Door keeps the phone feed alive**: the camera cover moved from
  `HubView` (inside `.id(palette)`) to `RootView`, opened via
  `RoomCameraPresenter.shared`. Open the viewer, slide Barn Door, the feed
  should keep running.

- [ ] **iPad movie mode + battery (5.42)**: movie mode keeps the screen
  awake for the whole film (even with Keep screen on off). It drops to 15%
  after 20 s idle; a tap lights it to 100%, then it re-dims. The old
  brightness comes back when the movie ends. The battery badge updates the
  moment iOS reports a change (iOS only gives apps 5% steps).
- [ ] **Night screen shows "JARVIS is watching"** in light grey while the
  iPad's lens is on, so Nick can see it from bed. Check it's readable at
  brightness 0 and not too bright to sleep with.

- [ ] **Green orb goes out instantly (5.43 + Core)**: closing the phone's
  camera view posts `POST /api/camera/leave`; Core then refuses the iPad's
  next frame (409) and it stops within ~0.2 s. Frames are now refused
  whenever no viewer is live. Needs `git pull` + restart on the Optiplex.
- [ ] **iPad low battery alert (5.43 + Core)**: the iPad posts
  `POST /api/device/battery` when its level/charging changes; below 20% and
  not charging, Core raises a `device_battery` alert (once per 4 h).
- [ ] **Camera on mid-movie** lights the iPad to full, then re-dims after 20 s.
- [ ] Add `/api/camera/leave` and `/api/device/battery` to JARVIS.md (left
  out because JARVIS.md had another session's uncommitted edits).

### Overnight 2026-11-01: check when you wake (iOS 5.46)

- [ ] **Deploy Core** (`git pull` + restart): the theme-sync fix lives in
  `app/server.py`. Until then Core refuses "experimenting" (422), so the
  phone and iPad don't follow each other on that theme.
- [ ] **Theme sync**: pick Experimenting on the phone; the iPad should follow
  within ~10 s. Flip an experiment switch on one; the other follows too.
- [ ] **Flowing plasma**: does the speeding up / easing off read as
  "flowing", not twitchy? Knobs in `PlasmaBackdrop`: `Gust` amplitudes
  (0.42/0.28/0.18), base loop times (`34 + seed * 4` s, `27 + seed * 5` s),
  blob opacities.
- [ ] **Neural core**: brain shape, size (`* 1.3` in `project`), firing
  rate (2.4/s idle, 7/s thinking), gold colour. Watch it on the iPad while
  saying "Hey JARVIS": it should get busier while he thinks. Check the
  iPad doesn't get warm or drop frames (it's ~200 lines + glows at 30 fps).
- [ ] Decide what graduates: a real theme, a switch on every theme, or
  binned. Orbit orb is still there (off by default).

### Dark pool, Room menu, memory import (iOS 5.56, TV 9.3, 2026-11-01): check

- [ ] **Deploy Core**: `/api/knowledge/import`, section search, push traffic.
- [ ] Dark pool under the core: too dark / not enough? (`PlasmaBackdrop`
  pool 0.78/0.5 dark; TV `PlasmaView` same.) Core line weight on the iPad.
- [ ] Room full screen reaches the bottom edge; Menu items all open.
- [ ] Memory map: tap Journal, pick a day, it opens (from the Memory tab and
  from the Room menu).
- [ ] Import Nick's Claude/ChatGPT "everything about me" list; ask JARVIS
  something only that list knows.

### Calmer plasma, Memory map, Link map (iOS 5.55, TV 9.2, 2026-11-01): check

- [ ] **Deploy Core** (`git pull` + restart): `/api/traffic/stream`,
  `/api/knowledge/graph` and the traffic tap live there. Until then the Link
  map says "Connecting…" and the Memory map can't load.
- [ ] iPad Room: is the red pool under JARVIS enough (knobs: pool opacity
  0.55 and radius ×1.9, cut 0.62/0.4 at ×1.6 in `PlasmaBackdrop`)? Any
  remaining corner "pop"? (`Gust swing` 0.5, stretch ramp `speed / 0.015`.)
- [ ] Core size in the left card, and the lights card reaching the bottom.
- [ ] Neural core firing with real traffic: busy enough, not static? Pending
  cap 4, idle thoughts ×3 slower while wired (`NeuralNetwork.fire`).
- [ ] Link map: are phone vs iPad told apart (`X-Jarvis-Device`)? Does the
  TV token's name contain "tv"? NAS calls classified right (check
  `TRAFFIC_NAS_HOSTS` if HA shows as internet)?
- [ ] Memory map with the real vault: layout readable at a few hundred notes?
- [ ] TV 9.2: the pool sits under the header core, not offset.

### Wake-up fixes, Memory view (iOS 5.52, TV 9.0, 2026-11-01): check

- [ ] Goodnight / Peace actually turn the desk lights off (Core forces every
  "off" light through both Govee entities after a scene). If the desk still
  disagrees with the app, consider pointing the HA scenes at the LAN entity
  `light.desk_lights` (real state, no cloud rate limit) and keeping only the
  DIY-scene selects on the cloud one.

- [ ] **Deploy Core** (`git pull` + restart): I'm up pausing the TV, the
  ringing state the app reads, the time-sensitive push and `/api/knowledge`
  all live there.
- [ ] **Time-sensitive entitlement:** Nick accepts the updated Program License
  Agreement at developer.apple.com (Xcode refused new profiles until then),
  then add `com.apple.developer.usernotifications.time-sensitive` = true to
  `ios/JarvisCompanion/JarvisCompanion.entitlements` and rebuild. Until then
  the push still arrives with its buttons, but Focus/Sleep can hold it.
- [ ] Next alarm: I'm up on the phone stops the TV's music; opening the app
  while it rings shows I'm up / Snooze; after Snooze it shows "Back at …".
- [ ] iPad Memory tab: profile note and journal render readably.
- [ ] Room settings › Theme on the iPad (picking one closes the sheet as the
  app re-themes; fine?).
- [ ] (5.53 / TV 9.1) Vivid Liquid glass backdrop on phone/iPad; still TV
  plasma; Barn Door fly-in on a real Barn Door (checked with the TV preview
  receiver on 2026-11-01). Too bright / too slow?

### Neural core v2 (iOS 5.47): check

- [ ] **Deploy Core** (`git pull` + restart) so switch flips reach the other
  device at once (home stream `look`), not only after a theme change.
- [ ] **Thinking**: round and layered enough? Knobs in `NeuralNetwork`:
  `shellRadii`/`shellCounts`, shell sway (`0.32 * sin(...)` in `pose`),
  thought intervals in `step` (1.8–3.6 s idle, 0.5–1.1 s active).
- [ ] **Eye**: open the camera on the iPad with Experimenting on; the brain
  should open into the green eye over ~1–2 s and close back when it stops.
  Lid shape `0.56 / 0.33` in `build`, glance size in `step`.
- [ ] **Barn Door**: engage it with Experimenting picked; the brain should
  bristle red (not cut to the old red reactor), and come back on stand-down.
  Spike lengths `1.42 + 0.18·r` in `build`.
- [ ] Performance on the iPad: ~190 nodes, ~400 wires batched into 12
  strokes, at 30 fps.
- [ ] **TV (8.6, deployed; needs Core pulled for `core`)**: with
  Experimenting picked the TV header shows the neural core; Barn Door shows
  it spiky full-screen and fades in. Untested on screen (the TV was asleep;
  a JVM stress run of `NeuralNet` through every mood was clean). Watch for
  frame drops on the TCL (`adb shell dumpsys gfxinfo com.tvhome`).
- [ ] **Barn Door crossfade (5.48–5.49)** on phone and iPad: should dissolve
  over ~1.4 s both ways with no dim dip (5.49 backdrop under the fade).
- [ ] **Liquid glass + TV plasma (5.49 / TV 8.7)**: needs Core pulled for
  `lab`. Cards glass on all three; backdrop frost thinner. iOS follows the
  system Liquid Glass setting (5.50); TV `glassCard` alphas (95/80, kept clear),
  `PlasmaView` frost wash (60 with glass, 110 without).

### Voice ID, presence, motion (asked 2026-11-01)

- [x] **Enrolled 2026-11-06** (5 samples; first checks scored 0.78 and 0.63 vs
  threshold 0.55). Still to do from the item below: watch scores for a few days,
  test his sister, tune `VOICE_ID_THRESHOLD`.
- [ ] **Deploy and enrol voice ID** (backbone built 2026-11-01, JARVIS.md
  "Voice ID backbone"): on the Optiplex `pip install sherpa-onnx`, download
  the TitaNet model, `VOICE_ID=1`, restart, type "learn my voice" in the
  phone app, say five sentences to the room mic. Then watch `jarvis.log` for
  `Voice ID: Nick (score …)` / `guest (score …)` for a few days and tune
  `VOICE_ID_THRESHOLD`. Test with his sister: siblings sound alike, so her
  score is the one that sets the threshold. Short commands ("Barn Door")
  may score lower than sentences; if Nick gets refused a lot, look at
  scoring only the speech after the wake word or keeping several centroids.
- [ ] **Voice ID next steps**: the room voice could say "Sorry, only Nick
  can do that" without a model call when a guest's command maps to a locked
  tool; a "who am I talking to?" line in the iPad's voice overlay; maybe
  per-person profiles (sister, guests) later. Goodnight still lets anyone
  answer the alarm question; decide if that should be Nick-only.
- [ ] **Leave home → lights out / Peace (backup-to-the-backup)**: when
  Nick leaves the house, turn the room off or run Peace automatically. First
  check whether his iPhone shows up in Home Assistant as a `person.*` or
  `device_tracker.*` (the HA Companion app on the phone provides one with
  zones; JARVIS's own app doesn't report location). If it does, add a
  runtime monitor that watches it going `home` → `not_home` for a few
  minutes and runs the scene (with a push saying so). Same signal would arm
  the motion alert below.
- [ ] **Baby motion alert (low priority, Nick isn't sure he needs it)**:
  two options. (a) The iPad diffs tiny greyscale frames on-device, posts a
  frameless "motion" event; Core raises an alert when armed (away, asleep,
  or "arm the motion sensor"). Needs the app open on the iPad, iOS's green
  dot shows, keep a small "Motion sensor armed" label. Watch for false
  alarms from the TV, headlights and the iPad's own dimming. (b) A ~$15
  Zigbee/Wi-Fi PIR sensor in HA; Core just watches a `binary_sensor`. No
  camera, works with the iPad off; simplest and most private.
- [ ] **Better room mic** if "Barn Door Protocol" still mishears after the
  Whisper vocabulary hint (added 2026-11-01): a far-field array (ReSpeaker
  XVF3800 / similar with beamforming + noise suppression) beats the CMTECK
  USB mic for across-the-room speech; it also helps voice ID.

### Loose ends (Nick)

- [ ] Reserve the Optiplex's IP on the home router (it drifted 10.0.0.80 →
  10.0.0.81 on 2026-10-27). Ssh now uses Tailscale, so this is insurance.
- [ ] Check the iPhone app's server address in Settings; if it's 10.0.0.80,
  switch it to the Tailscale address `100.69.8.11`.

### Voice fixes from Nick's 2026-10-29 logs (check after deploy)

- **Fake tool calls:** Haiku 5.5 sometimes wrote `[calls control_device ...]`
  as text (copied from the persona's style examples), so "fan to 30%" and
  "add Captain Phillips" + "yes" did nothing. Examples reworded; the agent now
  hides such text, asks once for the real call, and scrubs it from history.
  Watch jarvis.log for "Model wrote a tool call as text": if it keeps firing,
  pin `ANTHROPIC_MODEL` to a Sonnet.
- **Approval codes:** `confirm_gate` held one proposal per session, so
  staging two media fixes killed the first code. Now several at once; "yes"
  approves everything staged that turn; "approve 1631, approve 5774" works;
  "Hey Jarvis, yes" counts as yes.
- **Fewer movie confirms:** a plain top match adds straight away.
- **Room voice timing:** silence that ends a command 750 → 1000 ms, follow-up
  window 5 → 8 s. Still clipped? Raise `VOICE_SILENCE_END_MS` in the
  Optiplex `.env` and restart. Cloud STT returning ''
  for a real 1.5 s+ utterance now retries on local Whisper.
- **"Fan to 30"** is a quick command now (`status_hub.set_fan`). Also fixed:
  "turn the fan on" had been ambiguous with the Fan Light and went to the model.
- **Follow-up window after a question:** it only opened when no tools ran,
  so "Add Captain Phillips" (tools, then a question) closed the mic and
  Nick's instant "yes" was lost. Now a reply with a "?" or an armed
  confirm_gate yes keeps listening.
- **Phone "critical" arr alerts were "Prowlarr: New update is available"**
  every ~6 h for 54 h. Servarr `UpdateCheck` messages are no longer alerts.
- **"Update the arr stack"** (`update_arr_stack`): runs the NAS's weekly
  compose updater now, via the recovery key's new `update <uuid>`. Needs Nick
  to re-run `ops/nas/install-restricted-ssh.sh --apply` on the NAS (sudo).
  That also re-locks `stack_updater.sh`, which was world-writable while root
  cron runs it. Hand test: `ssh -i ~/.ssh/jarvis_nas_recovery Jarvis@nas
  "update <uuidgen>"` from the Optiplex → `{"state":"accepted","mode":"update"}`
  and a new `=== updater start` in updater.log.

### Next session starts here (left 2026-10-28, from school)

- **Built 2026-10-28 evening, from work (none of it run on hardware yet):**
  after `git pull` + restart on the Optiplex, try: "Hey JARVIS, what time is
  it", "what's the weather", "set a pasta timer for 1 minute" (it should
  beep and say so; "Hey JARVIS" stops it), "volume 4" / "louder", "good
  morning", interrupting a long answer with "Hey JARVIS", and a broadcast
  from the phone chat ("announce in my room that dinner's ready"). The mic
  fix (`MIC_DEVICE=CMTECK` + native-rate conversion) must be live first.
  Browser: `.venv/bin/pip install playwright` and `.venv/bin/python -m
  playwright install --with-deps chromium` (needs sudo for the deps) before
  JARVIS can click or fill forms; without it he reads pages over plain HTTP.

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
- **Cleanup passes (2026-10-28):** 614 → 741 Python tests (coverage 68% → 79%), 23 → 28 iOS
  tests; coverage 68% → 73% (room voice 16% → 67%, timers 31% → 94%,
  settings 17% → 93%, weather 19% → 97%, intents 53% → 82%, HA 71% → 84%).
  Fixed: `.env` line-break injection and nan through `update_setting`;
  HA service catalogue cached; one state read per spoken command; voice
  quick commands don't wait for HA to settle; weather geocodes cached;
  TTS timing prints are debug logs. Least covered now: `brain/runtime.py`
  (monitor loops), `speech/tts.py`, `integrations/spotify.py`,
  `brain/self_repair.py`, `app/main.py`.
- **"Jarvis, goodnight" (built 2026-10-28, untested on hardware):** try
  it the first night: the question should start ~1 s after "goodnight"
  (instant from night two, cached), "yes" should kill the lights within a
  second. Check Whisper hears short answers ("yes", "six thirty") reliably;
  if not, lower `VOICE_SILENCE_END_MS` or add phrasings to
  `speech/goodnight.py`.
- **Room voice (built 2026-10-28, not run on the Optiplex yet).** Turn on
  at home, in the Optiplex `.env`:
  `ROOM_VOICE=1`, `AUDIO_OUTPUT_DEVICE=plughw:1,0`, `WHISPER_MODEL=base.en`
  (small.en is too slow on the i5-6500T), then restart jarvis-api and watch
  `journalctl -u jarvis-api -f` ("Room voice listening", "Room voice heard
  ... (N ms)"). First check the speaker: `aplay -D plughw:1,0
  ~/jarvis/memory/sounds/done.wav` (or `speaker-test -D plughw:1,0 -c 2 -t
  wav`); if the speakers are on HDMI use that card instead. Tune
  `MIC_RMS_THRESHOLD` (default 350) if it cuts you off or never stops, and
  `WAKE_THRESHOLD` if it misfires. Measure: words end → jingle; aim < 1.5 s.
  Next ideas: speak reminders/timers on the room speaker, barge-in (stop
  talking when Nick speaks), cache "Yes, sir." audio, stream TTS.
- **Scenes made 2026-10-28 (not yet run):** Good Morning, Study, Movie,
  Game Time, Goodnight, Peace, Rainbow. At home: tap each once and adjust.
  Check the rainbows come up when a light was off (scene sets on + DIY
  dropdown at the same time). Open items: the fan's RGB rainbow (Tuya
  integration doesn't expose it; try the HACS "Tuya Local" integration);
  hook "Hey Google, game time / peace" to `scene.game_time` / `scene.peace`
  (needs HA linked to Google Home, not checked).
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
- **Knaben and Torrent9 stay disabled** (Nick, 2026-10-28: "they can be
  off"). They were switched off by hand, not by Prowlarr. Don't re-enable
  or suggest them. The 2026-10-28 change is deployed on the Optiplex.
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

### Small add-ons (collected 2026-10-28; none started)

Little things mentioned along the way, roughly smallest first:

- [x] Room voice caches repeated replies (`cache="auto"`) and routine lines.
- [x] Room voice: timers ring and reminders/broadcasts are spoken on the
  room speaker; barge-in on the wake word (2026-10-28).
- [ ] Room voice: start speaking the first sentence of long answers sooner
  (skipped 2026-10-28: spoken replies are one sentence, so streaming saves
  little; `tts.speak_stream` + `respond_streaming(spoken=True)` if needed).
- [x] "What scenes do I have?" by voice (`list_scenes`, 2026-10-29).
- [ ] Govee effects (Aurora, Candlelight, Starry Sky...) in the light sheet.
- [x] "Save this as a scene": the current room becomes an HA scene by name
  (`save_scene`, confirm-gated, 2026-10-29; not run on live HA yet: try
  "save this as a scene called Test", approve, check HA's scene list, run it).
- [ ] Scenes on the iPhone Home Screen / Control Center (widget target).
- [ ] iPad Room tab: a now-playing / music card.
- [ ] Fan's RGB rainbow: try the HACS "Tuya Local" integration.
- [ ] "Hey Google, game time / peace" → `scene.game_time` / `scene.peace`
  (needs HA linked to Google Home; not checked).
- [ ] TV LED under the screen still flashing overnight? If so, close the SSE
  stream on screen-off (plan above).
- [x] "Let me know when X happens" (asked 2026-11-08; shipped 2026-11-14, see
  JARVIS.md "Let me know when watches"). Due reminders now push to the phone
  and TV through `raise_alert`; `watch_for` / `list_watches` / `cancel_watch`
  run durable web watches on the `watcher` subagent. Check at home: "tell me
  when the AP poll is out" sets a sensible query, condition and `not_before`;
  the phone buzzes once with the answer and the room says it once; a due
  reminder buzzes the phone; the Agents page shows Watcher's cost. Loose end:
  reminders only fire while something pops them (room voice in server mode);
  a Core without the room voice never sends them.
- [ ] 1337x: `switch_indexer_mirror` stack fix, and incident reports quoting
  the real HTTP / Cloudflare code (separate chat prompt written 2026-10-28).
- [ ] NAS: allow-listed, confirm-gated "update one container" action.
- [ ] Reserve the Optiplex's IP on the router (still drifting).
- [x] iPad Room tab: weather beside the date (icon + temperature from
  `/api/glance`, refreshed every 10 min; iOS 5.67, 2026-11-02). Check it
  shows and fits on one line at home.
- [x] **Rich answer screens ("interactive returns", asked 2026-11-02;
  shipped 2026-11-13, see JARVIS.md "Answer screens").** `show_screen`
  puts weather_today, calendar_day or timers cards on the iPad Room tab,
  phone and TV. Check at home: the model picks `show_screen` for "what's
  the weather?" but not for incidental checks; the card reads well on the
  iPad, phone and TV in a couple of themes (Barn Door included); the TV card
  doesn't get in the way over Plex. Later cards: a movie's details, the TV
  remote, sending a card only to the screen nearest the voice that asked
  (today every screen shows it).

### Is the Optiplex enough? (checked 2026-10-28)

i5-6500T (4 cores, 2015), 3.7 GB RAM, 113 GB disk (6% used), idle at load
0.1 and 28-30 °C; Core uses ~170 MB. Measured: Whisper `small.en` takes
2.3 s for 3 s of audio (too slow for the room voice); `base.en` (~2-3x
faster, est.) plus `tiny.en` captions fit fine, with ~2.5 GB RAM to spare.
Enough for everything planned. Upgrade only for: a local LLM (needs a GPU
or a much newer CPU), bigger Whisper models, or running cameras/vision
(the room sentry) at the same time. Cheapest useful upgrade: 8-16 GB RAM.

### Integrations

- [x] **Charles Schwab, read-only and live** (shipped 2026-10-27, iOS 2.5 /
  TV 6.6; see JARVIS.md "Charles Schwab"). Built against fakes; first real
  run once Schwab approves JARVIS's app. Check then: streamer field numbers
  (3 last), cash fields for Nick's account type, the in-app login
  catching the `https://127.0.0.1:8182` redirect. The `tailscale serve`
  route for `/api/schwab/callback` set up on 2026-10-27 is unused now.
- [x] **Schwab trading** (shipped 2026-11-10 as `brain/trading.py`, see JARVIS.md "Trading"; Nick wanted it 2026-10-27, parked until the rest
  feels solid: "too janky to let it take my money"): "if NVIDIA hits X,
  sell; at 170, buy". Place real limit/stop orders at Schwab (they run even
  if Core is down), not price-watching in JARVIS. Agreed guardrails: every
  order confirmed on the phone with the exact order and dollar amount;
  per-order and daily caps Nick sets; stocks/ETFs only, no options or
  margin; "buy tons" becomes a number before anything is placed. Same app
  (it already has Accounts and Trading).
- [x] **Headless browser for JARVIS** (built 2026-10-28 as `brain/browser.py` + `browser_*` tools; needs `pip install playwright` + `python -m playwright install --with-deps chromium` on the Optiplex for clicks/forms) (asked 2026-10-27): Playwright on the
  Optiplex for searching, reading and filling forms. Not for logging into
  banks or brokerages (Nick agreed: no Schwab password on Core).
- [ ] **Letterboxd** (asked 2026-10-27): pull Nick's diary/ratings (RSS feed
  is the easy path, no official API) so JARVIS knows what he's watched and
  liked, and can tie it into Radarr requests.
- [ ] **Nick's top 5s** (asked 2026-10-27): his top fives in general (films,
  albums, etc.) as standing knowledge. Probably durable profile facts or a
  small dedicated store JARVIS can read and Nick can edit.
- [x] **Browser abilities** (2026-10-28: web_search + web_fetch server tools, dynamic filtering on Sonnet/Opus; price watching already existed) (asked 2026-10-27): general web search/lookup
  ("google this") and price watching (track a product URL, alert through
  `runtime.raise_alert` when it drops below a target). Price watching is a
  monitor, not a UI poll.

- [x] **Room scenes** (done 2026-10-28 as HA scenes: Good Morning, Study, Movie, Game Time, Goodnight, Peace, Rainbow; app picker + voice). Original note (Nick, 2026-10-27): one phrase sets the
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
- [x] **"Hey Google" replacement on the Optiplex** (built 2026-10-28 as `speech/room_voice.py`, `ROOM_VOICE=1`; not yet run on the hardware). Original note (Nick, 2026-10-27): the
  Optiplex has a mic and speakers (and maybe ElevenLabs set up already, to
  verify), so `--voice` mode could run there as the house's always-listening
  JARVIS once Nick wants it.

- [ ] **Cleancut: wake the Windows PC to process movies (Nick, 2026-11-02;
  not started, no code yet).** Cleancut is Nick's own VidAngel-style movie
  filter, already worked out; **don't design or rebuild it**. JARVIS's job
  is only the JARVIS → PC side: the desktop is much faster than the Mac, so
  it sleeps until a movie needs processing, then wakes, runs Cleancut, and
  sleeps again. The Mac isn't part of it.
  - **How it starts (Nick, 2026-11-02):** not by asking JARVIS. Nick
    starts a job from Cleancut's own little program on his computer (the
    Mac, presumably; confirm), and that program asks JARVIS to wake the PC
    before it scans/processes. So JARVIS's side is an API endpoint first
    (e.g. `POST /api/pc/wake` returning once SSH answers, or a job endpoint
    that queues the run), called with its own scoped client token
    (`app/client_tokens.py`, like the TV; never `JARVIS_API_TOKEN`). A
    voice/chat `process_movie` tool is optional later.
  - **Flow:** Cleancut's program calls JARVIS with the movie (a NAS path)
    → Wake-on-LAN magic packet to the PC's MAC
    from the Optiplex (or the NAS) → poll the PC's SSH port until it answers
    (expect 20-40 s, time out and say so rather than hang) → SSH in
    (key auth, Windows OpenSSH Server) and run Cleancut as a CLI job → the PC
    reads the movie from the NAS over SMB and writes the result back into
    the Plex library → PC sleeps.
  - **Rules:** a job queue, so a second request never sleeps the PC
    mid-render; sleep only when the queue is empty and the last job is done.
    Sleep, not shutdown (wakes more reliably). Log each job's start, end and
    result. Over SSH it must be a CLI process; if anything's GUI-only, use a
    scheduled-task workaround. Back it with its own module
    (`integrations/` or `brain/`) like timers/reminders, not inline in
    `tools.py`.
  - **PC setup (Nick, at home):** Wake-on-LAN in BIOS/UEFI (turn off
    ErP/EuP Ready if present); Device Manager → NIC: allow wake, magic
    packet only, "Wake on Magic Packet"; turn off Fast Startup; note the MAC
    (`ipconfig /all`) and give the PC a fixed IP (on the PC itself, like the
    TV, since router reservations need his dad's Xfinity app); install
    OpenSSH Server with JARVIS's key; mount the NAS SMB share and check
    read/write.
  - **Still to ask Nick:** is the PC on Ethernet (WoL over Wi-Fi is
    unreliable)? motherboard/NIC model (for exact BIOS steps)? Cleancut's
    command line (how JARVIS invokes it, what it reports while running)?
    where output goes and how Plex should name it (replace the original or
    a separate version)?
- [ ] **Progress bar on the iPad for anything running (Nick, 2026-11-02;
  later).** Whenever a long job is going (Cleancut first, then downloads,
  renders, etc.), a progress bar pops up on the iPad's Room tab with what
  it is and how far along, and goes away when it's done. Natural fit: jobs
  publish progress events on the event bus and the iPad shows them; needs
  Cleancut (or the job wrapper) to report percent done.

### Companion apps

- [x] **Phone hold-to-talk with JARVIS’s voice** (5.77): live transcript to
  Core, release-to-commit, streamed speech back through the phone. Ordinary
  typed Chat remains text; optional read-aloud for typed replies is separate.
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
    want yet"): snooze length, a gentler start. (Stop music on I'm up: done,
    TV 9.0.)

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

**Asked for 2026-10-29 (queued at bedtime, do these first):**
- **Barn Door reactor on the iPad matches the TV's.** Port `tv/launcher/src/com/tvhome/RedAlertView.java`
  (armour, closing teeth, targeting dial, lock-on arcs, the lens that blinks every 30 s; no pulsing)
  to a SwiftUI `Canvas`, shown in place of `JarvisCoreMark` on iPad Room while the palette is `barnDoor`.
- **Plex movie picker in iPad movie mode.** Grid of Nick's Plex movies (posters) with a search bar;
  a tap starts it on the TV's Plex app (Plex client `playMedia` via the TV's Plex player, or HA
  `media_player.play_media`). Needs a Core endpoint for the library and one to play.
- **Fan ring rotating rainbow** — waiting on Nick's Tuya IoT Access ID/Secret (iot.tuya.com cloud
  project, Western America, Smart Life app linked); then send `work_mode=scene` via Tuya's cloud API.
- **Confirm cloud STT** (`STT_PROVIDER=elevenlabs`, set 2026-10-29): jarvis.log should show
  `STT …s (elevenlabs cloud)` lines; `Cloud STT (elevenlabs) failed` means the plan/key lacks Scribe.


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

A tiny supervisor (launchd on the Mac, systemd on the Optiplex) that restarts
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
- **Voice interruption polish**: barge-in while JARVIS speaks.
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
