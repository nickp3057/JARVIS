# NAS arr stack connection — 2026-09-20

## Task 1 — shared recovery

Prepared `ops/nas/recover_stack.sh`, exact-trigger-preserving DHT/indexer
refactors, and a preview-first installer. Live scripts were read from NAS;
installer checks their SHA256 before replacing anything, backs up every
existing script, and serializes installation with the existing stack lock.

Recovery checks every command: remove gluetun, recreate only gluetun, wait
20s, recreate exactly the five dependents, wait 130s, then poll for healthy
VPN and positive DHT for about five minutes. It reuses an inherited flock,
logs failure phases, and writes an atomic status result. `--no-deps` prevents
Compose from starting unrelated services. The mooney backend is excluded.

Test: `python -m unittest tests.test_nas_recovery -v` on Linux with jq/flock;
the harness substitutes all Docker/curl/sleep commands. NAS preview:
`sudo bash install-recovery.sh --preview`. Installation does not restart the
stack. A live recovery test requires Nick's separate confirmation.

Deployment pending: NAS sudo currently requires an interactive password;
no NAS scripts have been changed and no stack restart has been requested.

## Task 2 — restricted SSH

Generated recovery/status ed25519 keys on the Optiplex under `/home/nick/.ssh/`;
private keys stayed there. Pinned the NAS host public key in
`jarvis_nas_known_hosts`. Recovery fingerprint:
`SHA256:EGVGvLJVeSjj4Kp2MFgp2uygjGqq1TprtYizVI7RieI`;
status: `SHA256:QU1w0b0yhFko6oNyLZykvLutsVmAshns7IqsP/HCHVY`.

Added the forced-command gate, detached idempotent launcher, separate read-only
status capability, and preview-first installer with backups and narrow sudo.
No key grants a shell or forwarding. Installation also secures the previously
world-writable script/Compose/SSH paths, with ACL backups. See `ops/nas/README.md`
for tradeoffs, installation, rollback, and hand tests.

All ten recovery/restriction tests passed on the NAS with fake Docker commands.
Optiplex lacks jq, so recovery harness tests there were explicitly skipped;
the actual NAS run passed without skips. Installers/public keys are staged at
`/tmp/jarvis-nas-tests/ops/nas` on the NAS, and the complete review is at
`/tmp/jarvis-nas-install-review.txt` on the Mac. Awaiting interactive sudo
installation before real key restriction tests. No recovery has been run.

## Task 3 — runtime monitor, phone alerts, restart confirmation, heartbeat

Implemented the runtime-owned Python polling loop (no background LLM),
three-zero DHT rule, per-condition persistent alert cooldowns, service health
and readiness checks, and watchdog activity in each alert. Existing HA service
`notify.mobile_app_nicks_iphone_17_pro` was discovered and selected; runtime
inbox/voice also receive notices. HA shares the NAS failure domain, so whole-NAS
outages may prevent phone push. Dead-man alerts use Healthchecks separately.

The restart tool only prepares a session-bound, expiring confirmation phrase.
The original user-message boundary consumes it once, in streaming and ordinary
turns, and waits for a matching NAS result plus independent DHT post-check.
Unknown outcomes never launch an automatic retry. Environment settings remain
admin-only. Healthchecks URL has been requested; delivery stays disabled until
it is supplied and the account's alert integration is configured.

Validation: 20 new Python tests passed; 41 focused integration/runtime/agent/API
tests passed both locally and on an isolated Optiplex candidate based on its
exact deployed revision `d4ba99f`. Text-mode smoke test with the model offline
returned the restart warning and confirmation phrase without executing SSH.
The temporary candidate has no Piper voice files, so speech stayed silent.
Full local suite: 312 tests, one unrelated existing failure in
`test_study_brief.StudyBriefEndpointTests.test_composed_briefs_shape`, five Linux
recovery tests skipped on macOS. The study-brief failure reproduces on the
older Optiplex baseline (quiz ordering/date fixture); all ten shell/security
tests passed separately on the real NAS using mocked commands.

Live read-only poll: qBit, Radarr, Sonarr, Prowlarr, and Byparr are unreachable.
NAS logs already showed a Sonarr name conflict in the indexer watchdog chain,
followed by its old misleading success message. qBit fails from both LAN and
Tailscale. No recovery or unrelated container changes were performed here.
The existing weekly updater and mooney-plaid-backend remain untouched.

### Optiplex deployment/activation state

The targeted patch is applied to `/home/nick/jarvis` on disk, based on its
existing `d4ba99f` revision. It does **not** deploy the unrelated later
self-repair commits. The existing runtime lock split is backported as a required
dependency: long recovery turns must not block event/monitor state. A regression
test proves monitor events remain writable during normal and streaming turns. It is currently a working-tree patch on that host;
reconcile it before a future `git pull`. The pre-existing `memory/profile.json`,
`memory/habits.json`, and untracked `app/api.py` were not changed.
Affected original source/docs/version files and `.env` are backed up in
`/home/nick/jarvis-backups/arr-20260920/before.tar` (private directory).
The reviewed deployment patch is `/tmp/jarvis-arr-release.patch` on both hosts.
The 41 focused tests also passed in the actual patched checkout.

Only new env keys were appended: monitor enabled, interval 60s, cooldown 1800s,
existing iPhone notify service, restricted key paths, and service URLs. Existing
API credentials/URLs were preserved. Recovery is **disabled** pending NAS key
installation/tests; heartbeat URL is blank. Runtime activation awaits Nick's
confirmation to restart **only** `jarvis-api` on the Optiplex. No restart was
performed. The final NAS test suite now has eleven passing tests, including
proof that the detached fake worker retains flock and duplicate UUIDs do not
launch twice.

Remaining live checks:
1. Nick runs the staged NAS installers with interactive sudo (diffs reviewed).
2. From Optiplex, recovery `probe` succeeds; shell/SCP/forwarding requests fail;
   status key returns JSON and rejects recovery. Then enable the restart flag.
3. Confirm and restart Jarvis API to activate monitoring. Verify HA accepts the
   resulting alert and Nick receives it on his phone.
4. Supply Healthchecks success URL, configure its alert integration/period/grace,
   activate heartbeat, and test a missed heartbeat in a maintenance window.
5. Separately confirm the disruptive NAS recovery test, inspect the matching
   result and positive DHT. The existing Sonarr name conflict may cause the
   prescribed chain to abort; do not improvise extra container removals.

Final focused validation after the lock dependency: **42 tests passed on the
Optiplex**, including 21 arr-specific tests. The local suite's earlier full-run
study-brief failure is unchanged; no new full-suite pass is claimed. All three
requested task commits are present in the Mac repository. NAS sudo installation,
restricted-key hand tests, service activation confirmation, Healthchecks URL,
and disruptive recovery verification remain pending user input.
