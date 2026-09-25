# Overnight overhaul (2026-09-22): handoff and review checklist

The brief is recorded in `FUTURE_SESSIONS.md` ("Overnight overhaul"). This
file tracks what shipped, what was decided without Nick, and what still needs
him. Nothing here was deployed: every change is local commits on `main`,
unit-tested (`.venv/bin/python -m unittest discover -s tests`), and not
exercised against the live NAS/Optiplex. The one failing test,
`test_study_brief.test_composed_briefs_shape`, was failing before this work
(date-dependent fixture) and is untouched.

Hard constraint honoured throughout: the NAS SSH permission model is
unchanged (restricted forced-command keys only, no Docker socket, no root).
No file under `ops/nas/` was modified.

## Follow-up session (2026-09-23)

Nick reported the Optiplex lost power (outage), came back manually; jarvis-api
and tailscaled running. Work in this session, in order:

| Item | State | Commit |
| --- | --- | --- |
| Google OAuth "could not locate runnable browser" | fixed | `cfb0c46` |
| Boot-time "Network is unreachable" race | fixed (needs Nick's sudo) | `b563250` |
| #2 status key dead | root-caused, guard written (needs Nick's sudo) | `281f586` |
| #3 Prowlarr key | verified working live | — |
| #4 NAS Tailscale IP → LAN | decided: swap (Nick edits `.env`) | — |
| #6 DHT watchdog double restart | fixed (Nick deploys via install-recovery.sh) | `837c14f` |
| BIOS AC-recovery mitigation | documented in `ops/optiplex/README.md` | `b563250` |
| Real APNs push pipeline (server sender + iOS registration) | done; needs APNs key | `bfc662d`, `7dc1a8c` |
| #7 deploy | see "Deploy checklist" below | |

Findings:
- **Boot race.** `jarvis-api.service` already had
  `After=/Wants=network-online.target`, but on this ifupdown + dhcpcd host
  `ifupdown-wait-online.service` was *disabled*, so `systemd-analyze
  critical-chain` showed the target reached at 2.344 s and jarvis-api started
  1 ms later. Fix: `ops/optiplex/install-network-wait.sh` enables the wait unit
  with `WAIT_ONLINE_METHOD=ping` to the IPv4 gateway (10.0.0.1), timeout 90 s.
  `route` was rejected: it accepts an IPv6 RA default route before the IPv4
  lease HA/NAS need. App side: `STARTUP_NETWORK_GRACE_S` (120) suppresses
  outage events during boot; weather retries with 30/60/120 s backoff instead
  of waiting 30 minutes.
- **The journal also showed a phone notification attempt at 22:53:15** —
  the deployed (pre-overhaul) arr monitor alerting on its first failed poll.
  The debounce from last night already prevents that.
- **Google OAuth.** Worse than a log line: at boot the token refresh hit a
  DNS failure, the code discarded the credentials, tried the browser flow,
  failed, and set `_service_failed` — calendar dead for the whole process.
  Now: network errors on refresh are retried; no in-process browser flow on
  headless hosts (one log hint per hour, new token picked up without
  restart); `python -m integrations.google_calendar --auth` does consent over
  `ssh -L 8765:localhost:8765 jarvis` (device flow does not allow Calendar
  scopes). The Optiplex token (2026-09-20) is fine and needs no action.
- **Status key (#2).** SSH from the Optiplex with the status key returns
  `This account is currently not available.` — the NAS `Jarvis` account's
  shell is `/usr/sbin/nologin`. sshd runs forced commands via the login
  shell. `/etc/passwd` + `/etc/shadow` were rewritten 2026-09-20 22:54, 1h45m
  after the installer set `/bin/bash` — a UGOS account save. **The recovery
  key shares the account, so arr restarts have also been failing since
  then.** Fix: `usermod -s /bin/bash Jarvis` plus the root-cron guard
  `ops/nas/ensure_jarvis_shell.sh` (restores only the shell, refuses if the
  account is admin). Jarvis now reports this cause by name.
- **Prowlarr (#3).** `PROWLARR_API_KEY` is set; `/api/v1/health`,
  `/api/v1/indexerstatus` (empty = all healthy) and `/api/v1/indexer`
  (6 enabled indexers with `id`/`name`/`enable`) all answer as the code
  expects. A non-empty `indexerstatus` entry has still not been seen live.
- **Tailscale vs LAN (#4).** Warm latency is equivalent (both ~15–90 ms; a
  first LAN sample was a cold 0.4 s). Swap chosen because jarvis-api is not
  ordered after tailscaled, so the Tailscale IPs can be unreachable at boot
  even with the network wait, and a tailnet problem shouldn't take arr
  monitoring down. The pinned NAS host key already covers `10.0.0.147`.
- **DHT watchdog (#6).** Confirmed from `watchdog.log`: the 05:40 run failed
  verification at 05:47:59; the cooldown file is only written on success,
  so the 05:50 check ran again ("consecutive check 4/3"). Now the cooldown
  starts on every attempt, with exactly one retry after a failure.
- **Outage timeline.** The NAS booted 2026-09-22 20:26 (it has AC recovery
  on); the Optiplex stayed off until the button press at ~22:53.

### APNs pipeline (2026-09-23)

- Server: `brain/notifications.py` signs an ES256 provider JWT with the .p8
  via `cryptography` (no PyJWT), reuses it 50 min, and posts over one HTTP/2
  `httpx` client (new dependency `h2`). `BadDeviceToken` retries the other
  environment once and remembers it; `410/Unregistered` prunes; any exception
  falls back to HA. Verified with a real P-256 key and a mock Apple endpoint
  (18 tests), not against Apple itself.
- iOS: Push Notifications entitlement, `PushAppDelegate`, `PushRegistrar`
  (permission once configured, register each foreground, upload on
  token/server change, environment from the embedded profile), Settings row.
  Device-SDK build and 19 simulator tests pass. Not yet run on the phone.
- Values from the project: Team ID `47543PU92M`, bundle ID
  `com.nickolaspearson.JarvisCompanion`.

### Deploy checklist (in order)

1. ✅ **Push to GitHub** (Mac): `git push origin main` — done 2026-09-23.
2. ✅ **Optiplex code + deps** — done 2026-09-23 (`f1aa372` on disk, `h2` 4.4.1
   installed, full suite run on the Optiplex: 402 tests, only the known
   study-brief failure). **Not active until the restart in step 5.**
   `ssh jarvis 'cd ~/jarvis && git pull && .venv/bin/pip install -r requirements.txt'`
3. ✅ **Optiplex `.env`: NAS over LAN (#4)** — verified 2026-09-24: qBit/Radarr/Sonarr URLs and `NAS_SSH_HOST` all on 10.0.0.147. (`ARR_STACK_ALERT_COOLDOWN_S` is still there; harmless.) — replace the Tailscale IP:
   `ssh jarvis "cd ~/jarvis && cp .env .env.bak-lan && sed -i 's#100\.97\.179\.53#10.0.0.147#g' .env && grep -n 10.0.0.147 .env"`
   (touches `QBITTORRENT_URL`, `RADARR_URL`, `SONARR_URL`, `NAS_SSH_HOST`; the
   pinned host key already lists 10.0.0.147). Optionally delete the obsolete
   `ARR_STACK_ALERT_COOLDOWN_S` line.
4. ✅ **Optiplex boot wait:** verified 2026-09-24 (`ifupdown-wait-online` enabled, ping 10.0.0.1, 90 s). `ssh -t jarvis 'cd ~/jarvis && sudo sh ops/optiplex/install-network-wait.sh 10.0.0.1'`
5. ✅ **Restart:** jarvis-api restarted 2026-09-23 09:39. `ssh -t jarvis 'sudo systemctl restart jarvis-api'`, then
   `ssh jarvis 'journalctl -u jarvis-api -n 30 --no-pager'`.
6. ✅ **NAS shell fix + guard (#2)** — applied 2026-09-24 21:58 (shell `/bin/bash`, guard + root cron line verified). Before that it had never run: `getent passwd Jarvis` still ends in `/usr/sbin/nologin`, `~/jarvis-ops/` doesn't exist, no guard in `/volume1/docker/watchdog/`, and the status key from the Optiplex still gets `This account is currently not available.` This is why "confirm restart arr stack" failed with `NAS restricted SSH failed (exit 1)`.
  
   `ssh nas 'mkdir -p ~/jarvis-ops' && scp -O ops/nas/ensure_jarvis_shell.sh ops/nas/dht_watchdog.sh ops/nas/recover_stack.sh ops/nas/indexer_watchdog.sh ops/nas/install-recovery.sh nas:jarvis-ops/`
   (`-O` is required: plain scp goes through UGOS's SFTP root `/` and fails. Done 2026-09-24, hashes verified.)
   then on the NAS: `sudo usermod -s /bin/bash Jarvis`, install the guard and
   its cron line with the step-by-step block in `ops/nas/README.md` → "Shell reset"
   (not the one-line `crontab -l | crontab -` pipe, which races two sudo prompts).
   Evidence none of this ran before 2026-09-24: `/etc/passwd` unchanged since 2026-09-20 22:54,
   no `~/jarvis-ops`, no guard file, no cron line.
6b. **NAS share ACL** — found 2026-09-24 after the shell fix (done 21:58): the keys log in
   but get `Permission denied` on the scripts, because UGOS's `docker` share ACL is admin-only
   (`ugacltool get /volume1/docker`). Fix: UGOS UI → Shared folders → docker → Permissions →
   Jarvis: Read only (`ops/nas/README.md` → "Share ACL").
7. ❌ **NAS DHT watchdog (#6)** — NOT applied: the deployed `dht_watchdog.sh` (2026-09-20) still differs from the repo; `recover_stack.sh`, `indexer_watchdog.sh`, `stack_status.sh` match. on the NAS, `cd ~/jarvis-ops && sudo bash install-recovery.sh --preview`, review, then `--apply`.
8. **Verify status key** from the Optiplex: ask JARVIS "arr stack status"
   (the "watchdog activity" line should no longer say unavailable).
9. **APNs key:** developer.apple.com → Certificates, IDs & Profiles → Keys →
   "+" → name it, tick **Apple Push Notifications service (APNs)** →
   Continue → Register → **Download** the `AuthKey_<KEYID>.p8` (one download
   only) and note the Key ID. Then:
   `scp AuthKey_<KEYID>.p8 jarvis:jarvis/memory/ && ssh jarvis 'chmod 600 ~/jarvis/memory/AuthKey_*.p8'`
   and add to the Optiplex `.env`:
   `APNS_ENABLED=true`, `APNS_KEY_PATH=/home/nick/jarvis/memory/AuthKey_<KEYID>.p8`,
   `APNS_KEY_ID=<KEYID>`, `APNS_TEAM_ID=47543PU92M`,
   `APNS_BUNDLE_ID=com.nickolaspearson.JarvisCompanion`; restart jarvis-api.
10. **App:** open the project in Xcode, build to the phone (automatic signing
    adds Push to the App ID), open the app, allow notifications. Settings
    should show "Push alerts on"; `GET /api/push/status` should report
    `"primary": "app_push"`. HA then fires only if APNs fails.
11. **BIOS:** AC Recovery → Power On (`ops/optiplex/README.md`).
12. **Calendar:** nothing to do — the existing token is fine; the fix just
    keeps a boot-time refresh failure from disabling it.

## Status (overnight session)

| Part | State | Commit |
| --- | --- | --- |
| 1. Confirmation token churn + mismatch echo | done | `88300e9` |
| 2. Alert false positives (hysteresis, maintenance hold) | done | `db837a8` |
| 3. Per-indexer alerts | done | `b7fede7` |
| 4. Notification router, companion push stubbed | done (stub) | `ddd1506` |
| 1b. Post-restart real status, async follow-up | done | `0e774b3` |
| 5a. Dashboard hub: single-flight cache, recent-list TTL, parallel pings | done | `85b9f49` |
| 5b. Whisper single deterministic pass + timing log | done | `d832bc2` |
| 5c. Skip snapshot rebuild on attribute-only HA events | done | `25cc24c` |
| 5d. Remaining perf findings | documented only (below) | |
| 6. Setup TODO audit | below | |

## What was found (evidence)

- **Every phone alert in the last two days came from JARVIS, not HA.** HA's
  recorder (`call_service` events for `notify.mobile_app_nicks_iphone_17_pro`)
  shows only "Jarvis — arr stack" messages. `automations.yaml` on the NAS is
  empty and there are no ping/uptime integrations. Nothing named "NAS power
  loss" or "device offline" was sent through HA in that window; if those
  arrive, they come from another app (UGOS's own notifications are the likely
  source) and are outside this repo.
- **One-poll blips.** qBit/Radarr/Sonarr "unreachable" then "Recovered" 60–65 s
  later: 2026-09-22 17:38, 09:24, 05:40, 05:51; 2026-09-21 04:50, 01:25,
  01:16. The 05:40, 05:50 and 01:15/01:25 ones line up exactly with the NAS
  DHT watchdog's own `recover_stack.sh` runs (`watchdog.log`); the rest have
  no NAS-side action, so they were transient network/API hiccups on the
  Optiplex→NAS path.
- **Prowlarr indexer backoff churn.** The aggregate "Indexers unavailable
  due to failures: …" line toggled every 10–15 minutes.
- **Status key not working on the Optiplex.** Every alert ended with "NAS
  watchdog activity: unknown (read-only SSH unavailable)". That also explains
  the "Arr recovery outcome is unknown" message: without the status key the
  old code could never see the correlated NAS result.
- **The Optiplex did not answer SSH** (LAN or Tailscale) during this session;
  the NAS answered on LAN only (its Tailscale address timed out).
- **DHT watchdog ran twice 10 minutes apart** (05:40 and 05:50 on 09-22)
  despite its 30-minute cooldown, probably because the cooldown is only
  written after a successful run. Not changed (NAS scripts are root-owned and
  out of scope); worth a look.

## Decisions made without Nick (change if you disagree)

- Hysteresis defaults: 3 consecutive failing polls to alert (~3 min at the
  60 s interval), 2 clean polls to recover, 10 polls of zero DHT (a normal
  recovery holds DHT at zero for ~8 min; the NAS watchdog itself waits 15).
  All env-tunable: `ARR_STACK_ALERT_CONFIRM_CHECKS`,
  `ARR_STACK_RECOVERY_CONFIRM_CHECKS`, `ARR_STACK_DHT_ZERO_CHECKS`.
- Maintenance hold uses the NAS `recovery-status.json` (running, or finished
  within `ARR_STACK_MAINTENANCE_SETTLE_S`=300) — only effective once the
  status key works — plus Jarvis's own follow-up thread.
- Per-indexer notices get a new `warning` severity: pushed like critical,
  but they don't open the post-recovery grace window. Both "down" and
  "Recovered" are pushed (batched per poll with any other notices).
- Confirmation near-miss detection is deliberately narrow: bare token, text
  containing the token, text starting with "confirm" plus restart/arr/stack,
  or ≥0.75–0.8 similarity to the phrase. A bare "yes" still goes to the model.
  Re-asking reuses the open token and does **not** extend its 10-minute life.
- Post-restart follow-up is asynchronous (reply now, report later) rather
  than blocking the turn: the old 10-minute wait overran the companion's
  3-minute timeout and blocked all other turns. Follow-up window
  `ARR_RECOVERY_FOLLOW_S`=720.
- Media-add gate had no churn (receipts are not consumed on a miss); only its
  rejection message changed to echo the rejected ID and the offered IDs.

## Review checklist

- [ ] `brain/arr_stack.py` lock handoff: `conversation_action` acquires
      `_restart_lock`; on success `_follow_and_report` (another thread)
      releases it. `threading.Lock` allows cross-thread release; confirm you
      are happy with the pattern.
- [ ] Prowlarr `/api/v1/indexerstatus` and `/api/v1/indexer` shapes were
      coded from the documented v1 API, **not** verified live (the Prowlarr
      key is unreadable on the NAS without sudo and the Optiplex was down).
      If the call fails the code falls back to the old aggregate line, so the
      worst case is today's behaviour. Verify with:
      `curl -H "X-Api-Key: $PROWLARR_API_KEY" $PROWLARR_URL/api/v1/indexerstatus`
- [ ] Gluetun "up" without the status key is inferred from qBit answering
      with DHT > 0 (qBit lives in gluetun's network namespace).
- [ ] The follow-up result is pushed + put in the inbox but is not appended
      to the chat transcript. Decide if it should be.
- [ ] `memory/arr_stack_alerts.json` gained `pending` and `clear_count`
      fields; old files load fine.

## Part 5 findings

Changed (safe, unit-tested):
- Hub cache was not single-flight; parallel app requests duplicated NAS calls.
- `/api/status/media` downloaded the **entire** Radarr and Sonarr libraries
  every 15 s to show five "recently added" items. Now cached 120 s
  separately; queue still 15 s.
- Service pings were sequential (a down service added 5 s); now parallel.
- Whisper: default temperature fallback re-decoded up to 5 extra times on
  low-confidence clips — the textbook cause of "highly variable" CPU
  latency. Now one pass by default (`WHISPER_TEMPERATURE_FALLBACK=true`
  reverts). Each utterance logs `STT <secs> for <secs> of audio`.
- HA WebSocket monitor rebuilt the snapshot on attribute-only events.

Found, not changed:
- **Faster-Whisper is not on the server path at all.** `jarvis-api` runs
  `--server`; the iOS app uses native dictation. STT only runs in
  `--voice`/GUI mode. If voice mode on the Optiplex is still slow after the
  single-pass change, the next steps are (1) `WHISPER_MODEL=base.en`
  (~2–3× faster, somewhat less accurate) — a one-line `.env` change to try,
  and (2) moving STT to the gaming PC's GPU, which is a real architecture
  change (network STT service + fallback) and was not attempted.
- Claude integration (not changed, flagged): no cache breakpoint on the
  message history, so every extra round of a tool loop re-sends the whole
  history at full price; 96 tool schemas (~9k tokens, cached) ride every
  call; `max_tokens` 1024 and default model `claude-haiku-4-5` are fine.
  The dynamic system block (memory retrieval keyed on the latest text)
  legitimately changes every turn, so it correctly sits after the cached
  persona.
- `switch_session()` reloads the full session from SQLite on every companion
  turn; small today.
- `profile.json` per-turn reads: evaluated and deliberately skipped — ~60
  facts parse in well under a millisecond and callers mutate the returned
  dict, so a cache adds risk for no measurable gain.
- Background monitors (HA WebSocket, calendar 120 s, qBit 30 s, arr 60 s,
  weather 30 min, price 6 h) are reasonable for the i5-6500T. The arr
  monitor makes ~6 HTTP calls a minute and SSHes only when something is
  already wrong.

## Part 4: what companion push still needs

Server side is done (router, stub, `POST/DELETE /api/push/register`,
`GET /api/push/status`). To make the JARVIS app the primary channel:

1. **Apple Developer Program** membership ($99/yr). Free provisioning
   cannot use push.
2. In the developer portal: create an **APNs Auth Key (.p8)**, note its
   Key ID and your Team ID. Copy the .p8 to the Optiplex (e.g.
   `~/jarvis/memory/`, chmod 600).
3. In Xcode: add the **Push Notifications** capability to JarvisCompanion
   (creates the `aps-environment` entitlement), and in the app call
   `UIApplication.shared.registerForRemoteNotifications()` after the existing
   notification permission grant; forward the token from an
   `UIApplicationDelegateAdaptor` to `POST /api/push/register`
   (`{"device_token": "<hex>", "environment": "sandbox"|"production"}`).
   Not done unattended: adding the entitlement without a paid team breaks
   signing.
4. Implement `_send_apns` in `brain/notifications.py` (ES256 JWT + HTTP/2
   POST; the docstring spells out the request). Add `httpx[http2]` and
   `PyJWT[crypto]` to `requirements.txt`.
5. Set `APNS_ENABLED=true`, `APNS_KEY_PATH`, `APNS_KEY_ID`, `APNS_TEAM_ID`,
   `APNS_BUNDLE_ID` in the Optiplex `.env`. `GET /api/push/status` will show
   `primary: app_push`. HA then fires only when APNs fails.

## Part 6: what Nick has to do

Accounts / keys / certs
- [ ] Apple Developer Program + APNs key, if you want app push (steps above).
- [ ] Get the **read-only status key** working on the Optiplex
      (`NAS_STATUS_SSH_KEY`, `NAS_SSH_KNOWN_HOSTS`, `NAS_SSH_HOST`); every
      alert says it is unavailable. Without it the maintenance hold only
      covers Jarvis-initiated recoveries, and the recovery follow-up relies
      on live probes alone (which still works).
- [ ] Confirm `PROWLARR_API_KEY` is set in the Optiplex `.env` (per-indexer
      alerts and proxy detection need it).

Physical / network
- [ ] The Optiplex did not answer SSH on LAN (10.0.0.80) or Tailscale during
      this session. Check it is up and `jarvis-api` is running.
- [ ] The NAS Tailscale address (100.97.179.53) timed out while LAN worked;
      the Optiplex reaches Radarr/Sonarr/qBit over the NAS Tailscale IP, so a
      flaky tailnet path is a plausible cause of the non-watchdog blips.
      Consider pointing those URLs at the NAS LAN IP.
- [ ] If "NAS power loss" / "device offline" alerts keep arriving, check
      which app sends them (UGOS notifications are the likely source); they
      did not come through HA or Jarvis in the last two days.

Deploy
- [ ] `ssh jarvis 'cd ~/jarvis && git pull' && ssh -t jarvis 'sudo systemctl restart jarvis-api'`

Decisions you may want to change (defaults picked overnight)
- [ ] Alert thresholds (3 fail / 2 clear / 10 DHT) and whether per-indexer
      "Recovered" notices should push or be inbox-only.
- [ ] Whether the recovery result should also appear in the chat transcript.
- [ ] Whether to keep Whisper's temperature fallback off.
- [ ] The DHT watchdog on the NAS ran twice ten minutes apart despite its
      30-minute cooldown; worth reviewing `dht_watchdog.sh`'s cooldown write.

Stubbed / incomplete
- Companion push sender (`_send_apns`) and all iOS push code.
- Prowlarr per-indexer API shapes unverified against the live instance
  (safe fallback to the old aggregate alert).

## Deploy (Nick)

```bash
ssh jarvis
cd ~/jarvis && git pull
# optional .env additions: see .env.example (all have safe defaults)
sudo systemctl restart jarvis-api
```
