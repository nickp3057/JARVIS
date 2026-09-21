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
installation/tests; heartbeat URL is blank. Nick subsequently approved restarting **only** `jarvis-api`. It was restarted
at 16:51 MDT, with an authenticated HTTP 200 health check and the first live
arr alert at 16:52 MDT. No phone-delivery failure was reported. The final NAS test suite now has eleven passing tests, including
proof that the detached fake worker retains flock and duplicate UUIDs do not
launch twice.

Remaining live checks:
1. Nick runs the staged NAS installers with interactive sudo (diffs reviewed).
2. From Optiplex, recovery `probe` succeeds; shell/SCP/forwarding requests fail;
   status key returns JSON and rejects recovery. Then enable the restart flag.
3. Jarvis monitoring is active; confirm receipt of the iPhone alert on the device.
4. Supply Healthchecks success URL, configure its alert integration/period/grace,
   activate heartbeat, and test a missed heartbeat in a maintenance window.
5. Separately confirm the disruptive NAS recovery test, inspect the matching
   result and positive DHT. The existing Sonarr name conflict may cause the
   prescribed chain to abort; do not improvise extra container removals.

Final focused validation after the lock dependency: **42 tests passed on the
Optiplex**, including 21 arr-specific tests. The local suite's earlier full-run
study-brief failure is unchanged; no new full-suite pass is claimed. All three
requested task commits are present in the Mac repository. NAS sudo installation,
restricted-key hand tests, Healthchecks URL,
and disruptive recovery verification remain pending user input.


### Activation follow-up — 2026-09-20

Nick approved the Jarvis API restart only. Startup verification exposed lazy
runtime initialization: monitors previously waited for the first companion
request. The server now starts the runtime during its ASGI lifespan when arr
monitoring or Healthchecks is configured, and stops monitors on shutdown.
Runtime construction is protected against simultaneous first requests.
Three regression tests cover no-request startup, disabled/lazy startup, and
concurrent initialization. All 35 arr/server/companion tests passed locally and
on the Optiplex. The older deployed companion-test fixture was also aligned
with the already-backported runtime turn lock.

The original server file and test fixture are backed up beside `before.tar`;
`/tmp/jarvis-arr-startup.patch` records this follow-up deployment. Only the
Jarvis API service was reloaded to activate it. No NAS container was restarted.
NAS installation/key testing and the Healthchecks URL remain outstanding.

## Recovery chain fix: stale dependent containers block force-recreate — 2026-09-20

Nick recovered the stack by hand tonight (outage from the Sonarr container-
name conflict noted earlier) and hit a real bug in the shared chain: after
`docker rm -f gluetun` + recreate, `docker compose up -d --force-recreate
qbittorrent radarr sonarr prowlarr flaresolverr` failed with "Conflict. The
container name /qbittorrent is already in use" — a dead container left behind
by a prior failed/aborted recovery keeps its name, and Compose refuses to
recreate it until it's gone. `docker rm -f` on the five, then recreate, fixed
it by hand.

`ops/nas/recover_stack.sh` now explicitly `rm -f`s the five dependents
(qbittorrent, radarr, sonarr, prowlarr, flaresolverr — never
mooney-plaid-backend) right before recreating them, the same pattern already
used for gluetun. Chain is now: `rm -f gluetun` → recreate gluetun → sleep 20
→ `rm -f` the five dependents → recreate the five → sleep 130 → verify. Each
step is still checked independently; any failure aborts and records a failed
result exactly as before.

Added `test_stale_dependent_conflict_is_cleared_before_recreate` to
`tests/test_nas_recovery.py`, reproducing the exact failure (a fake `docker`
that refuses `--force-recreate qbittorrent...` with the real Conflict message
until the new `rm -f` step has run) and asserting the chain still reaches
`success`. Also updated `test_order_and_success` and
`test_each_chain_failure_aborts` for the new step's position. Since this
harness is gated to Linux (recover_stack.sh uses `/proc/$$/fd/9` and GNU
`stat`, unavailable on macOS) and this Mac lacks `flock` too, verified for
real by installing Homebrew `flock`, confirming the platform gate still skips
it, then running the full `tests.test_nas_recovery tests.test_nas_ssh` suite
inside a `python:3.12-slim` Linux container (with `jq`/`util-linux`
installed) via local Docker Desktop: all 12 tests pass, including the new
one. `install-recovery.sh` pins SHA256 hashes of the *pre-existing* live
`dht_watchdog.sh`/`indexer_watchdog.sh` it found on the NAS on 2026-09-20 —
`recover_stack.sh` itself was never hash-pinned (it was new, not a
pre-existing live script), so this change needs no hash update, only a
re-run of the installer to pick up the new file content.

Full local suite (`.venv/bin/python -m unittest discover -s tests -q`): 318
tests, one unrelated pre-existing failure in
`test_study_brief.StudyBriefEndpointTests.test_composed_briefs_shape` (same
quiz-ordering/date-fixture issue already noted earlier today), 7 skipped
(the Linux-gated NAS tests on this Mac). `ARCHITECTURE.md` and `JARVIS.md`
were also corrected here: both still said the restricted keys authenticate
as NAS user `Nick`, left over from before the `Jarvis`-account migration
below; both now describe the dedicated non-admin `Jarvis` account and the
`sshd-jarvis.conf` requirement, and `JARVIS.md`'s recovery-chain summary now
mentions the dependents `rm -f` step.

Nothing was installed or restarted on the NAS for this fix — it is a local
repo change only. Since `install-restricted-ssh.sh` installs `recover_stack.sh`
too, re-running it (once Nick applies the `Jarvis`-account migration) will
pick up this fix in the same pass; a separate `install-recovery.sh --apply`
run is not required but is harmless if Nick prefers to run it explicitly.

### `install-recovery.sh` re-run bug — 2026-09-20

Nick actually ran `install-recovery.sh --preview`/`--apply` from the NAS to
pick up the fix above and both failed: `dht_watchdog.sh: FAILED`,
`indexer_watchdog.sh: FAILED`, `sha256sum: WARNING: 2 computed checksums did
NOT match`. Cause: the installer pinned only the two hashes of the original
pre-refactor scripts it read live on 2026-09-20; the first `--apply` run
earlier that same night already replaced them with the refactored versions
(from commit `0489557`), so a second run no longer matches. The claim in the
previous entry that the pins "still match" was wrong beyond a first, fresh
run.

Fixed by accepting either hash per file (original pre-refactor, or the
already-installed refactored version) and failing closed only on a third,
unreviewed value — implemented as a `check_known_hash` function rather than
the previous single-shot `sha256sum -c -`, since `sha256sum -c` has no
built-in concept of "any of these." Backup step is unchanged. Added
`tests/test_nas_install_recovery.py` (4 tests: accepts the original pin,
accepts the already-refactored content — the exact re-run case — rejects
unreviewed drift, rejects a missing live file), run for real inside a
`python:3.12-slim` Linux container via local Docker Desktop (same reasoning
as the recover_stack.sh test: the installer needs `flock` and GNU
`sha256sum`, neither reliably available on macOS). Also confirmed the *old*
script genuinely reproduces Nick's exact reported error
(`sha256sum: WARNING: 2 computed checksums did NOT match`) under the same
harness, and that the new script no longer does.

Full local suite: 322 tests (up from 318), same one pre-existing unrelated
`test_study_brief` failure, 11 skipped (the Linux-gated NAS/install tests on
this Mac). Nothing changed on the NAS for this fix.
