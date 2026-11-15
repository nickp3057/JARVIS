#!/bin/zsh
# Deploy JARVIS Core to the Surface, from the Mac, in one command:
#
#   deploy/surface.sh             # push main, fast-forward the Surface, restart Core, wait for /health
#   deploy/surface.sh --no-restart
#
# The Surface has no GitHub key, so main is pushed straight into its
# origin/main ref over SSH and its checkout fast-forwards to it (never a
# merge: a diverged Surface stops the deploy). If requirements.txt changed,
# its venv is updated first. The restart needs no password once the
# one-time sudoers rule is in place (deploy/surface-setup.sh, run once on
# the Surface with sudo). SURFACE=user@host overrides the target.
set -euo pipefail

HOST=${SURFACE:-nick@10.0.0.73}
restart=1
[[ ${1:-} == --no-restart ]] && restart=0
cd "${0:A:h}/.."

branch=$(git rev-parse --abbrev-ref HEAD)
if [[ $branch != main ]]; then
  echo "✗ on $branch; deploys go from main" >&2
  exit 1
fi

echo "→ pushing main ($(git log --oneline -1 | cut -c1-70)) to $HOST"
git push --quiet "$HOST:jarvis" main:refs/remotes/origin/main

ssh -o ConnectTimeout=8 "$HOST" "RESTART=$restart bash -s" <<'REMOTE'
set -e
cd ~/jarvis
before=$(git rev-parse HEAD)
git merge --ff-only --quiet origin/main
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ]; then
  echo "· Surface already at $(git log --oneline -1 | cut -c1-70)"
else
  echo "✓ Surface now at $(git log --oneline -1 | cut -c1-70) ($(git rev-list --count "$before..$after") new)"
  if git diff --quiet "$before" "$after" -- requirements.txt; then :; else
    echo "→ requirements.txt changed: updating the venv"
    .venv/bin/pip install --quiet -r requirements.txt
  fi
fi
if [ "$RESTART" = 1 ]; then
  sudo -n systemctl restart jarvis-api || { echo "✗ restart needs the sudoers rule: run deploy/surface-setup.sh on the Surface once" >&2; exit 2; }
  port=$(sed -n 's/^JARVIS_SERVER_PORT=//p' .env | tail -1); port=${port:-8000}
  host=$(sed -n 's/^JARVIS_SERVER_HOST=//p' .env | tail -1)
  case "$host" in ""|0.0.0.0|::) host=127.0.0.1 ;; esac
  for i in $(seq 1 30); do
    # /health wants the token; any HTTP answer (401 included) means Core is up.
    code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://$host:$port/health" 2>/dev/null || true)
    if [ -n "$code" ] && [ "$code" != 000 ]; then
      echo "✓ Core restarted and answering (${i}s)"
      exit 0
    fi
    sleep 1
  done
  echo "✗ Core didn't answer /health in 30 s: journalctl -u jarvis-api -n 50" >&2
  exit 3
fi
REMOTE
