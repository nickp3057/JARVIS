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

## Status

| Part | State | Commit |
| --- | --- | --- |
| 1. Confirmation token churn + mismatch echo | done | `88300e9` |
| 2. Alert false positives (hysteresis, maintenance hold) | done | `db837a8` |
| 3. Per-indexer alerts | done | `b7fede7` |
| 4. Notification router, companion push stubbed | done (stub) | `ddd1506` |
| 1b. Post-restart real status, async follow-up | done | `0e774b3` |
| 5. Performance pass | in progress | |
| 6. Setup TODO audit | see bottom | |

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

## Deploy (Nick)

```bash
ssh jarvis
cd ~/jarvis && git pull
# optional .env additions: see .env.example (all have safe defaults)
sudo systemctl restart jarvis-api
```
