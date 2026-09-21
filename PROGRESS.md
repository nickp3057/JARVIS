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
