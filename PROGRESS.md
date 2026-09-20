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
