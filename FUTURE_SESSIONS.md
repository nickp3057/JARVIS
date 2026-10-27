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

Phase 2 (built 2026-10-27, 1.81 / build 83; not yet installed on the NAS):
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
