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
Compose/watchdog path and Nick's SSH files. This is **non-recursive**: service
data folders are untouched. Creating new top-level `/volume1/docker` entries
or editing Compose thereafter requires sudo. Existing root cron schedules are
preserved. Permission backups can be restored with `setfacl --restore` from
the root-only backup, but remove the Jarvis sudo rule first if rolling back
these security protections. Restore backed-up files only after inspecting
the backup path printed by the installer.

Sudo permits Nick to invoke one fixed root launcher with a validated UUID.
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

## Hand tests on the Optiplex

Use `ssh -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes
-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/nick/.ssh/jarvis_nas_known_hosts
-i /home/nick/.ssh/jarvis_nas_recovery -T Nick@100.97.179.53 probe`
(on one line). It must return `restricted; no restart performed`.
Change `probe` to `id`, `sh`, or `status`: each must return exit 64 and no shell
output. A port-forwarding attempt must fail; do not enable agent forwarding.
With the status key, `status` must return JSON and `id` must be rejected.
The pinned NAS ed25519 host key was read through the existing trusted NAS
connection; never use `StrictHostKeyChecking=no`.

Only after Nick explicitly confirms downtime, invoke the recovery key with
`recover <new UUID>`. It returns a request ID promptly; `nohup` + `setsid` +
`disown` detach recovery from UGOS's SSH timeout. Poll the status key until that
**same** request has state `success` or `failed`. Success requires healthy
gluetun and positive DHT. A stale/missing status or lost SSH connection is an
unknown result, never success and never an automatic retry with a new UUID.

The existing weekly stack updater is unchanged. It already uses the common
flock and excludes the mooney backend; it remains a separate update operation.

Tests: `python3 -m unittest tests.test_nas_recovery tests.test_nas_ssh -v`.
The recovery harness requires Linux, jq, flock and procfs; all Docker/network
commands and waits are replaced in a temporary script. Tests never restart the
real stack.
