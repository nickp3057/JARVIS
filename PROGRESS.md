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

## NAS restricted-key enforcement fix — 2026-09-20

Nick ran `install-recovery.sh` and `install-restricted-ssh.sh` (`--preview`
then `--apply`) against the `Nick` admin account, exactly as staged; both
completed cleanly with backups at
`/volume1/docker/watchdog/backups/20260920T230040Z-recovery` and
`/var/backups/jarvis-nas/20260920T230040Z-ssh`. Hand-testing the two
restricted keys immediately after found they granted a full, unrestricted
shell: `probe` ran as a literal nonexistent command and `id` returned a real
`uid=1000(Nick) gid=10(admin)...` line for both the recovery and status keys.
`authorized_keys` itself was byte-for-byte what was reviewed; sshd's own `-vv`
debug output even confirmed it recognized the per-key `command=` option
(`key options: command`) — it just never got applied.

Root cause: `/etc/ssh/sshd_config` carries an unconditional, vendor-owned
`ForceCommand /etc/ssh/force_command.sh` (UGOS, not ours). A global
`ForceCommand` overrides any per-key `command=` restriction entirely. That
wrapper gives admin/root-group logins (`Nick` is `gid=10(admin)`) an arbitrary
shell, and limits every other account to `rsync`/`scp`/`sftp-server`,
denying everything else. So a non-admin account alone would not have fixed
this — it would have made both keys deny-all instead of unrestricted, since
`probe`/`status`/`recover <uuid>` don't match the wrapper's allowed patterns.

Fix, approved by Nick: a `/etc/ssh/sshd_config.d/jarvis-nas.conf` drop-in
(`ops/nas/sshd-jarvis.conf`) with `Match User Jarvis` / `ForceCommand none`,
closed with a trailing `Match all` so it cannot leak into later-sorted
`sshd_config.d/*.conf` files. This restores normal `command=` enforcement for
`Jarvis` only, leaving `Nick` and everyone else unaffected. Nick created a new
non-admin NAS account for this (`Jarvis`, uid 1001, primary group `users`,
Personal Folder enabled, all shared folders Access Denied); confirmed via
`ls /home` that UGOS stored it as `Jarvis` (title case), not `JARVIS`.

`install-restricted-ssh.sh` was rewritten to be reproducible for this
architecture rather than a one-off manual fix: it installs the sshd drop-in
first (highest blast radius) and runs `sshd -t` immediately, auto-reverting
just that file and aborting before touching anything else if validation
fails; it never reloads sshd itself. It also fixes `Jarvis`'s shell (UGOS's
GUI-created default was `/usr/sbin/nologin`, which ignores forced commands
the same way it ignores an interactive login — sshd invokes a forced command
via `$SHELL -c "..."`) to `/bin/bash`, safe here only because every key on
that account carries a `command=` restriction. It refuses to run at all if
`RESTRICTED_USER` resolves to an admin/root-group account, moves the sudoers
rule for `jarvis-nas-launch` from `Nick` to `Jarvis` (same narrow scope, only
that script, UUID-arg only), removes the two restricted-key lines from
`Nick`'s `authorized_keys` (backed up first, Nick's own login key untouched),
and verifies (fixing via a targeted `setfacl`, not the shared-folder
permission GUI, if needed) that `Jarvis` can read the watchdog status/log
files `stack_status.sh` needs and cannot read `qbit_creds.env`.

`NAS_SSH_USER` changed from `Nick` to `Jarvis` in `.env.example`; the
Optiplex's live `.env` needs the same one-line edit (handed to Nick, not run
here — SSH writes to `jarvis`/`nas` are blocked in this environment). No code
changes were needed in `integrations/nas_stack.py` or `brain/arr_stack.py`;
they already read the account purely from `NAS_SSH_USER`/`NAS_*_SSH_KEY`.

**Re-run all four hand tests (recovery+probe, recovery+id, status+status,
status+id) any time you suspect `/etc/ssh/sshd_config.d/jarvis-nas.conf`
might have moved** — after running the rollback command, after a NAS/UGOS
update, or if that directory was otherwise touched — not just after this
initial install.

State as of this entry: sshd drop-in, shell change, `Jarvis` `authorized_keys`/
sudoers, and `Nick` cleanup are all drafted in `ops/nas/` but **not yet
applied** — Nick is about to run the updated `install-restricted-ssh.sh`
(`--preview` then `--apply`) plus `sshd -t` and `systemctl reload ssh`
himself, then all four hand tests get re-run against the `Jarvis` account
before anything further proceeds. `ARR_STACK_RESTART_ENABLED` stays `false`
and no live recovery runs until Nick separately approves that step.

Correction: none of the `Jarvis`-account migration above (sshd drop-in,
shell change, `authorized_keys`/sudoers move, `Nick` cleanup) had actually
been run when this file first said so — Nick had not yet executed the
installer commands. Nothing changed on the NAS from that section; it is
still purely staged in this repo, awaiting Nick.

### `Jarvis`-account migration: installed, verified live — 2026-09-20

Nick ran the full updated install sequence on the NAS: `install-recovery.sh
--preview`/`--apply` (now re-runnable) and `install-restricted-ssh.sh
--preview`/`--apply` both succeeded — `sshd -t` passed, `Jarvis`'s shell is
`/bin/bash`, sudoers is parsed and points at `Jarvis`, and the two restricted
key lines are off `Nick`'s `authorized_keys`. He updated the Optiplex's live
`.env` to `NAS_SSH_USER=Jarvis` himself. `systemctl reload ssh` was run
separately once he was back home.

After that reload, all four hand tests initially came back
all-commands-denied (`nologin`: "This account is currently not available.")
for both keys, including `probe`/`status`, which should have worked. Cause:
`/etc/ssh/sshd_config.d/jarvis-nas.conf` was missing — confirmed via the NAS
journal to be Nick running the rollback command
(`sudo rm -f /etc/ssh/sshd_config.d/jarvis-nas.conf`) at 21:05, not a UGOS
reset. (An earlier version of this entry and `ops/nas/README.md` incorrectly
speculated a UGOS firmware/security-scan reset; there's no evidence for that,
and the wording has been corrected above and in the README.) Nick reinstalled
the drop-in and reloaded again (`sudo sshd -t && sudo systemctl reload ssh`);
the file survived this time.

All four hand tests then passed against `Jarvis`, over Tailscale (`jarvis-ts`
/ `nas-ts` aliases — the LAN routes were briefly unreachable):
`probe` → `jarvis-recovery-key: restricted; no restart performed` (exit 0);
recovery key `id` → `DENIED: only probe or recover UUID is allowed.` (exit
64); `status` → valid JSON (exit 0); status key `id` → `DENIED` (exit 64).
The restricted-key enforcement fix is confirmed working end to end.

The `status` response also incidentally confirmed the `recover_stack.sh`
dependent-conflict fix works live: `indexer_watchdog.log` shows an 18:00:01
recovery failing with the exact "Conflict. The container name ... is already
in use" error (from before the fix was installed), and a clean 19:30:01
indexer-triggered recovery succeeding afterward with no conflict, gluetun
healthy, DHT climbing normally (319+ by 21:10:01).

`ARR_STACK_RESTART_ENABLED` is still `false`; no live recovery has been
requested through Jarvis.

## Task 5, dry run — arr restart confirmation gate — 2026-09-20

Confirmed the confirmation gate end to end without ever sending a real
`recover`: ran the existing `tests.test_arr_stack.RestartApprovalTests` (7
tests, all pass — wrong token, single-use consumption, expired/cancelled,
disabled-at-prepare, success/timeout/failure replies) and separately drove
the real `brain.arr_stack.conversation_action` (the same function
`brain/agent.py` calls at the top of every turn, before the model sees the
message) with only `integrations.nas_stack.start_recovery` mocked. Verified
every branch by exact reply text and mock call assertions: wrong token →
`invalid or expired`; correct token → consumed once, replay rejected; flag
disabled at prepare time → refused outright; a matched, mocked "success"
response → the real recovered/DHT reply text. `nas_stack.start_recovery` is
the only line in the whole flow that would ever reach the NAS, and it was
mocked in every run.

`ARR_STACK_RESTART_ENABLED` stays `false` in the live `.env`; Nick has not
yet approved flipping it. `nas_stack.enabled()` reads `os.getenv()` fresh on
every call (confirmed only two call sites, neither caches it), so flipping
it needs no Jarvis restart.

## Healthchecks dead-man heartbeat setup — 2026-09-20

Nick created the Healthchecks.io check (period 5 min, grace 5 min) and has
the ping URL, kept out of this repo, chat, and terminal history entirely.

Read the full call chain (`brain/runtime.py`'s `_deadman_loop`/
`start_monitors`, `integrations/nas_stack.py`'s `ping_deadman`) rather than
assuming the usual "`.env` is read live" rule applied here — it doesn't
fully: `JARVIS_HEALTHCHECKS_URL`'s presence is checked exactly once, inside
`JarvisRuntime.__init__` → `start_monitors()`, gated by a `_deadman_started`
flag that's never re-evaluated, and `JarvisRuntime()` is a guarded singleton
constructed once per process. Adding the URL to `.env` alone does not start
the heartbeat thread; `sudo systemctl restart jarvis-api` is required (NAS
containers are on a separate machine and untouched by this).

Chose `JARVIS_HEALTHCHECKS_INTERVAL_S=60` (updated `.env.example`'s default
from `300`, which left near-zero margin for a single missed ping, to `60`,
matching the value's own enforced minimum in `nas_stack.seconds()`): with
period 5 min + grace 5 min giving 10 minutes of tolerance, a 60s interval
means even several consecutive missed pings stay well inside that window.

Verified the exposure surface rather than assuming it: `ping_deadman()` never
logs, and swallows `requests.RequestException` (timeouts/connection errors —
the cases most likely to otherwise embed the URL in an exception message)
silently, returning `False`. The only logging in the whole path is a fixed,
generic failure string in `_deadman_loop`'s `record_event()` call — never the
URL. `brain/settings.py`'s `list_settings`/`get_setting`/`update_setting`
only touch an explicit allowlist of voice/persona settings that does not
include this variable, so it is structurally unreachable there, not merely
unused. No code anywhere dumps `os.environ`, and no HTTP endpoint in
`app/server.py` returns raw config. Nothing needed fixing.

Confirmed the heartbeat is tied to the arr monitor loop actually completing
cycles, not just the process being alive: `_deadman_loop`'s `monitor_ok`
check requires the arr monitor thread to be alive and to have completed a
poll cycle within the last `max(180s, 3× its interval)`. Expected transient:
right after any `jarvis-api` restart, the very first deadman check can race
the arr monitor's first poll cycle and report `not ok`, producing one
harmless `Dead-man heartbeat not delivered` runtime-inbox event that
resolves on the next 60s cycle — far short of the 10-minute Healthchecks
tolerance, so it will not reach Healthchecks as a real "down."

There is no explicit success log line anywhere in this path — only the
failure transition logs (via `record_event`'s own `log.info`). Confirmation
of live delivery is the Healthchecks dashboard ("Up" status, advancing "Last
Ping"); absence of the `Dead-man heartbeat not delivered` line in
`jarvis.log` is the local-side "no news is good news" signal, not a positive
confirmation.

Nick ran the recommended test: `sudo systemctl stop jarvis-api`, waited, and
confirmed a Healthchecks alert email actually arrived, then
`sudo systemctl start jarvis-api` to restore. Live delivery, the dashboard
"Up" transition, and real end-to-end alerting are all now confirmed, not
just traced through code.

Nothing was written to the Optiplex's `.env` from here (SSH writes are
blocked in this environment) and the URL was never pasted into this
conversation. `.env.example`'s `JARVIS_HEALTHCHECKS_INTERVAL_S` default is
the only file changed in this repo.

## Arr monitor notification-noise fix — 2026-09-20, approved and implemented

Nick approved the plan from the read-only investigation above and granted
autonomy to implement, test, commit, and push (no restarts, no writes to
Optiplex/NAS, mooney-plaid-backend untouched, no secrets/URLs in commits, no
force pushes).

### Byparr check replaced with Prowlarr health

Confirmed via `docker port gluetun` (Nick) that Byparr's `:8191` is not
published on the host — only `7878`/`8080`/`8989`/`9696` are — so Byparr is
reachable from Prowlarr internally but was never reachable from our own
direct check by design, not a monitoring bug. `poll_health()`'s direct
`byparr-api` check is removed entirely; a down Byparr/indexer-proxy is now
expected to surface through Prowlarr's own authenticated `/api/v1/health`
list instead (existing `prowlarr-health` issue key, now classified
critical — see below).

**Could not verify live that Prowlarr's health API actually reports a
Byparr/proxy failure** — `PROWLARR_API_KEY` is currently blank on the live
Optiplex `.env` (confirmed: 0-length value), so the authenticated endpoint
returns 401 and my own read-only test against it couldn't inspect a real
payload. `poll_health()` already silently falls back to the unauthenticated
`/ping` (reachability only, no health detail) when the key is blank — same
as today, not a regression, but it means this fix has no effect until a real
key is added. **Action needed from Nick**: add a real `PROWLARR_API_KEY`,
redeploy, and with Byparr down, confirm Prowlarr's health list actually
contains a proxy-related entry. If it doesn't, this replacement doesn't
restore Byparr-failure detection and should be revisited.

### Dedupe by condition (alert once, silent while active, one recovery notice)

`AlertTracker` rewritten: replaced the `{key: last_alerted_ts}` + fixed
cooldown re-alert loop with `{key: detail}` active-condition tracking. A
condition fires exactly once when it first appears, stays completely silent
every subsequent cycle while it persists (regardless of how long), and fires
exactly one `Recovered: ...` notice the cycle it clears. A condition that
resolves and later reappears is treated as new again, not a continuation.
`_arr_stack_monitor_loop`/`describe_status` needed no changes — `notices()`
kept its `list[str]` return shape for this step. `ARR_STACK_ALERT_COOLDOWN_S`
is no longer read anywhere; removed from `.env.example` (existing live
`.env` values are simply ignored now, not harmful to leave in place).

State file format changed from a flat `{key: timestamp}` to
`{"active": {...}, "zeros": n}`; old-format files are silently treated as
empty (safe — worst case is every then-active condition re-announcing once
after this deploys, not a crash or wrong state).

Caught and fixed a real bug in review, not just in a test: the "3
consecutive zero-DHT checks" streak counter lived only in memory, while the
new `active` dict is persisted to disk. Since `_arr_stack_monitor_loop`
constructs a fresh `AlertTracker()` on every process start, a restart
occurring right after `dht-zero` had already fired would reset the in-memory
streak to 0, and the *next* poll would see `dht-zero` missing from that
cycle's issues (streak not yet back at 3) and spuriously announce it as
"Recovered" even though DHT was still actually zero the whole time. Fixed by
persisting the streak counter (`zeros`) alongside `active` in the same file.
A dedicated regression test (`test_restart_right_after_threshold_does_not_
spuriously_recover`) covers this; without the fix it fails.

Tests: new/persist-silent/recover-once/reappear-as-new/old-format-migration/
restart-does-not-spuriously-recover, plus the two pre-existing DHT and
per-service tests updated for the new semantics (both still pass unchanged
in spirit — dedup produces the same observable result they were already
asserting for a short time gap, just for a different reason).

### Critical vs advisory severity + routing

`AlertTracker.notices()` now returns `Notice(key, text, severity, kind)`
instead of plain strings (`kind` is `'new'`/`'recovered'`, with
`'escalation'` reserved for the next change). `radarr-health`/`sonarr-health`
are classified advisory (a fixed set, not an env var — a code-level
judgment call, not something to tune per deploy); everything else
(unreachable services, `dht-zero`, `prowlarr-health` — which is also how a
down Byparr/proxy now surfaces) is critical. `_arr_stack_monitor_loop`
splits notices by severity: critical still announces + pushes to the phone
(unchanged path); advisory goes to the runtime inbox/event feed only, no
announce, no phone push. Every notice, either severity, is logged in
`jarvis.log` as `Arr stack condition <key>: <kind> (<severity>)` — the
condition key and a short status, satisfying that requirement directly.

Not unit-tested separately: the phone-push/inbox routing wiring itself and
the log line, since `_arr_stack_monitor_loop` (an infinite thread loop) was
already untested at that boundary before this change — consistent with the
existing convention of testing `AlertTracker`/`nas_stack` logic directly
rather than the runtime loop wrapper. The severity *classification* (the
part with actual judgment, and the part a future edit could get wrong) is
covered by `test_radarr_sonarr_health_is_advisory_everything_else_critical`.

### Escalation — one reminder per long window

New `ARR_STACK_ESCALATION_INTERVAL_S` (default 21600 = 6h). `active` state
per condition grew from a plain detail string to `{detail, first_seen,
last_escalated}` (both timestamps persisted, same restart-safety reasoning
as the `zeros` streak counter fixed earlier — an in-memory-only escalation
clock would let a restart delay or skip a due reminder). Once a condition
has been unresolved for `last_escalated + interval`, one `Notice(...,
kind='escalation')` fires with `"Still unresolved after Xh: <detail>"`, and
`last_escalated` resets to now — silent again until the next boundary.
Applies to both severities (advisory conditions still eventually remind, on
the same long cadence, just never phone-push per the routing above).

Old (commit-era) state files — both the original flat `{key: timestamp}`
shape and the intermediate dedup-only `{"active": {key: "detail string"}}`
shape — are recognized as incompatible (`first_seen` missing) and safely
dropped per key, same graceful-migration approach as before: worst case is
one avoidable re-announcement per still-active condition after this deploys,
never a crash or wrong escalation timing.

Tests: fires once per window while unresolved, stays silent between
boundaries, fires again at the next boundary; escalation timing survives an
`AlertTracker` restart (persisted `last_escalated`).

### Post-recovery grace window

New `ARR_STACK_POST_RECOVERY_GRACE_S` (default 300; `0` disables). When a
**critical** condition recovers, `grace_until = max(grace_until, now +
grace)` opens (persisted, restart-safe like everything else here). Any
condition that first appears while `now < grace_until` is tracked (so it can
still resolve or eventually surface) but not announced — `active[key]`
carries a new `announced` flag. Once grace ends, if it's still present it
gets its proper `new` notice (fresh `last_escalated` baseline); if it
resolved first, it's dropped with no notice either way (never telling Nick
about something he was never told started). Already-active conditions are
unaffected by grace — it only holds back brand-new ones, and escalation of
something already active keeps firing on schedule regardless. Chose to key
grace off *any* critical recovery rather than scoping it to specific
services (e.g. only the five recreated dependents) — simpler, and the
downside (a genuinely new, unrelated problem's first notice delayed by up to
`grace` seconds) is bounded and backstopped by escalation; can be narrowed
later if that proves too broad in practice.

Tests: suppressed during grace then surfaces once grace ends; resolves
silently before ever being announced; only a critical (not advisory)
recovery opens grace; escalation of an already-active, already-announced
condition is unaffected by a grace window opened by something else
recovering at the same time.

### Config summary (new/changed since this section started)

| Variable | Default | Meaning |
|---|---|---|
| `ARR_STACK_ESCALATION_INTERVAL_S` | 21600 (6h) | One reminder per window while a condition stays unresolved |
| `ARR_STACK_POST_RECOVERY_GRACE_S` | 300 | Suppress a brand-new condition's first notice for this long after a critical recovery; 0 disables |
| `PROWLARR_API_KEY` | (blank) | Now required (not just optional) for proxy/Byparr-failure detection via Prowlarr health; currently blank on the live Optiplex |

Removed: `ARR_STACK_ALERT_COOLDOWN_S` (no longer read anywhere).
Unchanged: `ARR_STACK_MONITOR_ENABLED`, `ARR_STACK_MONITOR_INTERVAL_S`,
`ARR_STACK_NOTIFY_SERVICE`, `PROWLARR_URL`, `BYPARR_URL` (kept, documented
as no longer directly checked), `ARR_STACK_RESTART_ENABLED`.


## 2026-09-22 overnight overhaul (not deployed)

Local commits only; see `OVERNIGHT_HANDOFF.md` for evidence, decisions and
the review checklist. Activation notes:

- The hysteresis, maintenance hold, per-indexer alerts and async recovery
  follow-up activate on `git pull` + `sudo systemctl restart jarvis-api`; all
  new env vars have defaults (see `.env.example`).
- The maintenance hold needs the read-only status key working on the
  Optiplex; every recent alert said "read-only SSH unavailable".
- Per-indexer alerts need `PROWLARR_API_KEY` on the Optiplex (this file
  earlier recorded it blank; recent alerts suggest it is now set).
- Companion push is stubbed: `APNS_ENABLED` stays false and HA
  `notify.mobile_app_*` remains the delivery path.

## 2026-10-25 arr reliability + media doctor (Phase 1)

### 1a — Servarr/qBittorrent reliability fixes

Audit findings fixed in `integrations/servarr_common.py`, `radarr.py`,
`sonarr.py`, `qbittorrent.py`:

- `servarr_common.call()` returns a classified `Result(ok, payload, kind)`
  (`unreachable`/`timeout`/`auth`/`not_found`/`rejected`/`server`);
  `request()` keeps its old `(ok, payload)` shape. Timeouts are now
  `(connect 4 s, read 15 s)` so a powered-off NAS fails fast.
- GETs retry once (1 s pause) on connection errors, timeouts and 5xx.
  Writes never retry: a timed-out add re-checks the library instead and
  reports success if it landed, or says it couldn't confirm.
- Error text is spoken-friendly: no raw urllib3 traces; a 401 names the
  `.env` key; Servarr validation errors (`[{"errorMessage": ...}]`) are
  extracted.
- Quality-profile/root-folder caches expire after 5 minutes.
- Profile matching: the most specific (shortest) containing name wins, a tie
  is ambiguous (Nick gets the list), and a profile naming a different
  resolution is never picked ("720p" can't land on "HD-1080p").
- Titles with a trailing year ("Dune 2021", "Dune (2021)") search the bare
  title and narrow to that year; "Blade Runner 2049" falls back to the
  literal search.
- qBittorrent: logins are serialized across threads, and the IP-ban (403)
  and wrong-password cases get their own messages.

Tests: `tests/test_servarr_reliability.py` (13). The full suite has the same
15 environment-only errors as before this change (missing optional deps in
the cloud container + the known study-brief fixture failure).

### 1b — generic confirm gate

`brain/confirm_gate.py`: tools *propose* a side-effecting fix; it runs only
on Nick's own next message ("yes") or "approve <code>" within 10 minutes,
session-bound, consumed before running, never retried. Wired into the agent
boundary next to the arr restart gate (which keeps its own tuned code; the
distinct "approve" verb keeps the two from answering each other). Tests:
`tests/test_confirm_gate.py` (7).

### 1c — media doctor

`integrations/media_doctor.py` + `integrations/plex.py`. `diagnose(title)`
walks: Radarr/Sonarr reachable → in library (year-aware; ambiguous titles ask)
→ monitored → released (Radarr `isAvailable`) → last history event
(`downloadFailed` / grabbed-then-vanished) → indexers (Prowlarr backoff via
`nas_stack.indexer_outages`) → queue record → qBittorrent torrent by
`downloadId` (error/missingFiles, paused, metaDL > stall window, stalledDL
idle > stall window, missing from client) → import (importBlocked/Pending/
Failed with Radarr's own status messages) → Plex (`/hubs/search`, skipped
without `PLEX_TOKEN`). No title sweeps all queue items, services and indexers.

Exposed as the `diagnose_media` / `apply_media_fix` tools and
`GET /api/status/media/diagnose`. Fixes are proposed only; they run after
Nick's "yes"/"approve <code>" through `brain/confirm_gate.py`, and "restart
arr stack" hands off to the existing restart gate. The endpoint is read-only;
applying from the iOS app currently means asking in chat.

Tests: `tests/test_media_doctor.py` (19, mocked Servarr/qBit/Prowlarr/Plex),
plus an endpoint auth test in `tests/test_server.py`.

**Verify live on the Optiplex:** `PLEX_TOKEN` presence and that
`/hubs/search` returns JSON for this Plex version; that Radarr's queue
`downloadId` matches qBittorrent hashes (it should, case aside); and a real
`curl -H "Authorization: Bearer …" ':8000/api/status/media/diagnose?title=…'`
against a known-stuck and a known-finished title.

### 1d — stalled-download alert

The runtime download monitor now also records a `Download stalled` alert
(once per torrent until it moves again) when a torrent sits in
`stalledDL`/`metaDL` with no data for `MEDIA_DOCTOR_STALL_MINUTES`
(default 30). Paused torrents don't alert; errors keep their own alert.
`download_snapshot` carries seeds/peers/last_activity/time_active for this.
Today these alerts land in the runtime inbox like download errors; Phase 2
routes them through the event bus to the phone.

## 2026-10-25 model auto-update

Nick wants JARVIS to stay on the cheap Haiku tier but follow new releases.
`brain/model_select.py`: `ANTHROPIC_MODEL=auto` (or empty) resolves the
newest `claude-haiku-*` from the Models API once a day (cached in
`memory/model_cache.json`, gitignored; failures keep the last id or the
built-in Haiku 4.5 default). Haiku 5.5 is ~10x cheaper than 4.5 but thinks by
default, so 5.x+ requests carry `effort: low` and, on Haiku, `thinking:
disabled` (no thinking blocks means no history-editing check on replayed
thinking, and replies stay inside `_MAX_TOKENS`). Settings go through
`extra_body` so the Optiplex SDK version doesn't matter. A model that 400s on
them is retried once plain and remembered. `refusal` stop reasons get an
in-character reply. Tests: `tests/test_model_select.py` (9).

**Verify live:** the Optiplex `.env` probably pins
`ANTHROPIC_MODEL=claude-haiku-4-5-20251001`; change it to `auto` (or ask
JARVIS to `update_setting ANTHROPIC_MODEL auto`). Then check `jarvis.log` for
"Conversation model for family haiku: ... -> claude-haiku-5-5" and do a voice
turn and a tool turn (e.g. "what's downloading") to confirm latency and tool
use on the new model.

## 2026-10-25 companion streaming fix

Nick: replies "don't stream to my app". The app does use `/chat/stream`, but
URLSession content-sniffs up to 512 bytes of a `text/plain` body before
delivering any, and most JARVIS replies are shorter, so they arrived whole.
`/chat/stream` now sends `X-Content-Type-Options: nosniff` (plus
`Cache-Control: no-cache`, `X-Accel-Buffering: no`). **Verify live** after
restarting jarvis-api: a short reply should type out in the app.

## 2026-10-25 no more guessed media adds; quieter tool turns

Nick saw "I assume you mean the 2016 movie starring Matthew McConaughey...
it's added": the model picked one of several matches and added it. Now, when
the session's latest `find_movie`/`find_series` offered more than one title,
`add_movie`/`add_series` don't write: they propose the exact title and profile
through `brain/confirm_gate.py`, and the add runs only on Nick's next "yes" /
"approve <code>". A single match keeps the one-step "yes → added" flow.
Persona: no text before or between tool calls (speak once, after the last
result), and never assume which of several matches he means.
Test: `test_pick_from_several_matches_needs_nicks_yes`.

## 2026-10-25 one bubble per model round in the companion

Nick: everything lands in one run-on chat bubble. `/chat/stream` now accepts
`X-Jarvis-Stream-Segments: 1` (echoed back); the agent then emits
`agent.ROUND_BREAK` (U+001E) instead of a space between model rounds, via the
`stream_round_breaks` contextvar set in the server's producer thread. The
marker is persisted in that session's history so recovery/sync splits the same
way, and `_turn_messages` replaces it with a space before history goes back to
the model. Voice, GUI and old apps are unaffected (default off). iOS:
`JarvisAPIClient.stream` sends the header; `MessageBubble` renders one bubble
per segment (header on the first only). Tests: `tests/test_stream_rounds.py`
(3) + server opt-in test. **Not compiled here (no Xcode on Linux): build in
Xcode and check a tool turn ("add Gold") shows separate bubbles.**

## 2026-10-25 Phase 2 — alert bus, client event stream, scoped tokens

- `brain/event_bus.py`: one `publish()` for every proactive alert, with
  severity, `push` flag, dedupe/cooldown, a 200-alert replay history and named
  subscribers (one failing subscriber can't block the rest).
- Runtime: `raise_alert()` (inbox + bus) now carries download errors, stalled
  downloads, the dead-man heartbeat failure and the new device watch;
  `push_to_phone()` (arr incidents) publishes with its thread/collapse ids.
  The phone is a bus subscriber: APNs companion push first, HA notify only as
  fallback (unchanged `brain/notifications.py`); a failed delivery is recorded
  in the inbox and never re-published.
- Device offline: `JARVIS_WATCH_DEVICES` (HA entity ids) alert after
  `DEVICE_OFFLINE_ALERT_MIN` unavailable, once, plus a non-push "back online".
- Indexer down: per-indexer pushes now wait `ARR_STACK_INDEXER_ALERT_AFTER_S`
  (20 min) instead of the 4-minute stack threshold.
- `GET /api/clients/events`: SSE of bus alerts, `Last-Event-ID` replay,
  keep-alives, unsubscribes on disconnect.
- `app/client_tokens.py`: per-client tokens with `events:read` /
  `status:read`, SHA-256 only on disk (0600), CLI create/list/revoke. The
  read-only status endpoints accept them; chat, sessions, control, links, home
  and push stay admin-only.
- NAS unreachable keeps flowing through the arr monitor's incident policy.
  Unknown-SMS-sender alerts were dropped with the SMS phase.

Tests: `tests/test_event_bus.py` (10), indexer-threshold expectation updated
in `tests/test_arr_incidents.py`. Also fixed a regression from the bubble
commit: `chat_stream` called directly (not via FastAPI) crashed on the header
default.

**Verify live:** after deploy, `curl -N -H "Authorization: Bearer <tv token>"
http://optiplex:8000/api/clients/events` should print keep-alives; publish a
test by pausing a torrent past the stall window or setting
`JARVIS_WATCH_DEVICES` to an unplugged device. Check phone push still arrives
for an arr incident (APNs if enabled, HA otherwise).
