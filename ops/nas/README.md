# UGOS arr recovery boundary

Live project: `stack`, `/volume1/docker/docker-compose.yaml`. Root cron only.
No Watchtower, Docker socket mount, generic Docker sudo grant, or root SSH.

## Install without a restart

Stage this directory and the two Optiplex-generated ed25519 **public** keys as
`jarvis_nas_recovery.pub` and `jarvis_nas_status.pub` in a temporary NAS folder.
Keep private keys on the Optiplex, mode 600. Run both installers with
`--preview` first; then `sudo bash install-recovery.sh --apply` and
`sudo bash install-restricted-ssh.sh --apply`. Each application repeats its
diff, takes stack.lock, and backs up existing scripts before edits. The first
installer checks hashes of the scripts inspected on 2026-09-20; if they have
changed, stop and review new diffs instead of bypassing the check. Both are
short jobs and do not execute recovery. `sed` writes the reviewed replacements.

The restricted installer also removes unsafe ACL/write permissions on the
Compose/watchdog path and the restricted account's SSH files. This is
**non-recursive**: service data folders are untouched. Creating new top-level
`/volume1/docker` entries or editing Compose thereafter requires sudo.
Existing root cron schedules are preserved. Permission backups can be restored
with `setfacl --restore` from the root-only backup, but remove the Jarvis sudo
rule first if rolling back these security protections. Restore backed-up
files only after inspecting the backup path printed by the installer.

### The restricted keys live on a dedicated `Jarvis` account, not Nick

UGOS ships a system-wide `ForceCommand /etc/ssh/force_command.sh` in
`/etc/ssh/sshd_config` (no `Match` scoping it). A global `ForceCommand`
overrides any per-key `command=` restriction in `authorized_keys` entirely —
sshd resolves each keyword to the first value obtained for a connection, and
the global directive is obtained before any per-key option ever would be.
`force_command.sh` gives **admin/root-group** logins an arbitrary shell
(`exec "$SHELL" -c "$SSH_ORIGINAL_COMMAND"`, ignoring `command=` completely)
and limits everyone else to `rsync`/`scp`/`sftp-server`, denying everything
else — including our own forced commands. Discovered 2026-09-20 by hand-testing
the first install against the admin `Nick` account: `probe` ran as a literal
(nonexistent) shell command and `id` returned a real, unrestricted shell.

The fix (installed by `install-restricted-ssh.sh`) is `sshd-jarvis.conf`, a
`/etc/ssh/sshd_config.d/` drop-in:

```
Match User Jarvis
    ForceCommand none

Match all
```

This restores normal `authorized_keys` `command=` enforcement for the `Jarvis`
account only — Nick, admin access, rsync, and SFTP are untouched, since a
Match block's directives are only "obtained" for connections that satisfy it.
The trailing `Match all` closes the block explicitly so it can never leak
into whatever a later-sorted `sshd_config.d/*.conf` file declares. The
installer runs `sshd -t` immediately after installing this file and reverts
it automatically (before touching anything else) if validation fails; it
never reloads sshd itself — run `sudo systemctl reload ssh` yourself once the
installer reports success, with your existing SSH session left open in case
something is still wrong. Rollback: `sudo rm -f
/etc/ssh/sshd_config.d/jarvis-nas.conf && sudo sshd -t && sudo systemctl
reload ssh`.

On 2026-09-20 this file briefly disappeared after a working `--apply` +
reload cycle, reproducing the original all-commands-denied symptom below.
The NAS journal showed the cause was Nick running the rollback command above
himself (`sudo rm -f /etc/ssh/sshd_config.d/jarvis-nas.conf` at 21:05) —
**not** a UGOS reset; there's no evidence UGOS touches this directory on its
own. **Re-run the four hand tests below any time you suspect this file might
have moved** (after running the rollback command, after a NAS/UGOS update, or
if `sshd_config.d/` was otherwise touched) to be sure. If
`sshd_config.d/jarvis-nas.conf` is missing, both restricted keys get denied
entirely — UGOS's vendor wrapper falls back to its non-admin branch, which
only allows `rsync`/`scp`/`sftp-server`, so `probe` and `status` return
`nologin`'s "This account is currently not available." too, exactly as
confirmed live on 2026-09-20. That's a lockout, not a leak; the leak this
whole setup exists to prevent (`id` returning a real shell) only happens if
these keys ever end up on an admin/root-group account again.

`Jarvis` (uid 1001, primary group `users`, **not** `admin`) also needs a
working login shell — sshd invokes a forced command via the account's shell
(`$SHELL -c "..."`), and `/usr/sbin/nologin` (UGOS's GUI-created default for
this account) ignores that and just refuses, exactly like `nologin-origin` in
the deny branch above. The installer sets it to `/bin/bash`. This is safe
specifically because every key on the `Jarvis` account carries a `command=`
restriction — there is no unrestricted key on this account to fall back to.
The installer also refuses to run at all (`RESTRICTED_USER` check) if pointed
at any admin/root-group account, and cleans the two restricted-key lines out
of `Nick`'s `authorized_keys` once they're installed for `Jarvis`, leaving
Nick's own login key untouched.

Sudo permits `Jarvis` to invoke one fixed root launcher with a validated UUID.
The launcher accepts exactly one UUID argument; its wildcard sudo rule cannot
supply shell commands or extra arguments. A durable request directory prevents
replays. The recovery key's forced command is exactly
`/volume1/docker/watchdog/recover_stack.sh`, with pty, port/agent/X11 forwarding,
and user rc disabled. Ordinary commands are rejected, not silently restarted.

A separate status key runs `stack_status.sh` without sudo. It returns fixed
status/cooldown files and the last 20 lines (at most 8 KiB) of each watchdog log.
Tradeoff: one extra key to manage, but no HTTP listener and no chance that a
status poll invokes the recovery capability. OpenSSH forced-command options
are documented at <https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT>.

## Shell reset (UGOS reverts Jarvis to nologin)

Symptom: every restricted SSH call fails with exit 1 and
`This account is currently not available.`; Jarvis reports "NAS Jarvis
account shell was reset to nologin". sshd runs `command=` forced commands via
the account's login shell, and UGOS rewrites `/etc/passwd` with
`/usr/sbin/nologin` for non-admin users whenever the account is saved in its
UI. Found 2026-09-22: `/etc/passwd` and `/etc/shadow` were rewritten
2026-09-20 22:54, 1h45m after the installer set `/bin/bash`, and both keys
had been dead since.

Fix now, then keep it fixed (on the NAS as an admin):

```bash
sudo usermod -s /bin/bash Jarvis
sudo install -o root -g root -m 755 ~/jarvis-ops/ensure_jarvis_shell.sh /volume1/docker/watchdog/ensure_jarvis_shell.sh
( sudo crontab -l; echo '*/5 * * * * /volume1/docker/watchdog/ensure_jarvis_shell.sh' ) | sudo crontab -
```

(copy `ops/nas/ensure_jarvis_shell.sh` to `~/jarvis-ops/` first, e.g.
`scp ops/nas/ensure_jarvis_shell.sh nas:jarvis-ops/`.) The guard only
restores `/bin/bash` for `Jarvis`, refuses if that account is ever in an
admin group, and logs each restore to `watchdog.log`. It touches no key,
restriction, sudo rule or sshd setting. Avoid editing the Jarvis account in
the UGOS UI; if you must, the guard repairs it within five minutes.

Verify from the Optiplex:

```bash
ssh -F /dev/null -T -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/jarvis_nas_status \
    -o UserKnownHostsFile=~/.ssh/jarvis_nas_known_hosts Jarvis@<NAS_SSH_HOST> status | head -c 200
```

## DHT watchdog cooldown (2026-09-23 revision)

`dht_watchdog.sh` now starts its 30-minute cooldown at every remediation
attempt, not only after a success, and allows exactly one retry after a
failed attempt. Before, a failed run (2026-09-22 05:40, verification timed
out) left the counter at threshold and the next five-minute check re-ran the
full recreate immediately (05:50); a persistent fault would have looped every
~13 minutes. Deploy with the usual `sudo ./install-recovery.sh --preview`
then `--apply`; its hash pin accepts the currently deployed version and this
one. State files: `.last_action` (attempt time), `.last_action_failed`,
`.retry_used`.

## Hand tests on the Optiplex

Use `ssh -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes
-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/nick/.ssh/jarvis_nas_known_hosts
-i /home/nick/.ssh/jarvis_nas_recovery -T Jarvis@100.97.179.53 probe`
(on one line). It must return `restricted; no restart performed`.
Change `probe` to `id`, `sh`, or `status`: each must return exit 64 and no shell
output — if any of them instead returns a real shell result (e.g. `id` prints
a `uid=...` line), STOP; the sshd drop-in above is not in effect. A
port-forwarding attempt must fail; do not enable agent forwarding.
With the status key, `status` must return JSON and `id` must be rejected.
The pinned NAS ed25519 host key was read through the existing trusted NAS
connection; never use `StrictHostKeyChecking=no`.

Only after Nick explicitly confirms downtime, invoke the recovery key with
`recover <new UUID>`. It returns a request ID promptly; `nohup` + `setsid` +
`disown` detach recovery from UGOS's SSH timeout. Poll the status key until that
**same** request has state `success` or `failed`. Success requires healthy
gluetun and positive DHT. A stale/missing status or lost SSH connection is an
unknown result, never success and never an automatic retry with a new UUID.

The chain now explicitly `rm -f`s the five dependents (qbittorrent, radarr,
sonarr, prowlarr, flaresolverr) before recreating them, the same way it
already did for gluetun. Found live on 2026-09-20 during a manual recovery: a
dead container left behind by a prior failed/aborted run keeps its name, and
`docker compose up -d --force-recreate` then refuses with "Conflict. The
container name ... is already in use" until it's removed first.

The existing weekly stack updater is unchanged. It already uses the common
flock and excludes the mooney backend; it remains a separate update operation.

Tests: `python3 -m unittest tests.test_nas_recovery tests.test_nas_ssh -v`.
The recovery harness requires Linux, jq, flock and procfs; all Docker/network
commands and waits are replaced in a temporary script. Tests never restart the
real stack.

## Jarvis monitor and confirmation tests

The Optiplex uses the existing Home Assistant iPhone notification service.
Configure the `ARR_STACK_*`, `NAS_*`, and heartbeat fields in `.env.example`;
the implementation is documented in `ARCHITECTURE.md`. Monitoring is opt-in.
Enable recovery only after the restriction tests above. Never pass SSH/Docker
commands through the generic self-repair command registry.

Run `python -m unittest tests.test_arr_stack tests.test_runtime_monitors
 tests.test_agent_serialization tests.test_media_selection_safety tests.test_server -q`
(on one line). With restart enabled, say `restart arr stack` and check that it
warns about interrupted downloads and returns a confirmation phrase. No SSH
recovery runs until you send the exact phrase in that same conversation. Reject,
expire, or switch sessions to verify the gate; automated tests cover each case.

Healthchecks success pings run every 300 seconds by default, with no LLM or
secret URL logging. Configure the check's external alert integration, five-minute
period and at least five-minute grace. Check its dashboard for a received ping;
a controlled Jarvis shutdown should produce a missed-ping notification after
period + grace. Confirm that shutdown/restart separately. The heartbeat also
stops when the enabled arr monitor dies or stops completing cycles; it does not
assert conversational model responsiveness.

For the 2026-09-20 deployment, source and `.env` are backed up in
`/home/nick/jarvis-backups/arr-20260920/before.tar`. Restore reviewed files from
that archive to roll back the Optiplex patch, then restart Jarvis only after
confirmation. New unused Python modules may remain on disk after restoration.
`PROGRESS.md` distinguishes installed source from activated/live-tested behavior.
