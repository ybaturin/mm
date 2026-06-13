#!/usr/bin/env bash
# Deploy (or update) the trading system on a remote host from your machine.
#
#   scripts/deploy.sh root@95.179.169.77
#
# Incremental: rsync ships only what changed, then re-provisions (uv sync + tests).
# Use it for the first deploy AND for every update when the code changes.
#
# Env overrides:
#   SSH_KEY      ssh identity file        (default ~/.ssh/id_ed25519)
#   REMOTE_DIR   path on the host         (default /root/mm)
set -euo pipefail

HOST="${1:?usage: scripts/deploy.sh user@host}"
KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-/root/mm}"

echo ">> syncing code to $HOST:$REMOTE_DIR"
# NOTE: exclude '/data' is anchored to the repo root on purpose — a bare 'data'
# would also wrongly exclude src/trading/data. Do not change it to 'data'.
rsync -az --delete -e "ssh -i $KEY" \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.idea' --exclude '.git' --exclude '/data' --exclude '*.db' \
  --exclude '.claude' --exclude '.env' \
  ./ "$HOST:$REMOTE_DIR/"

echo ">> provisioning on $HOST"
ssh -i "$KEY" "$HOST" "cd $REMOTE_DIR && bash scripts/provision.sh"

echo ">> done. To run a daily cycle on the host:"
echo "   ssh -i $KEY $HOST 'cd $REMOTE_DIR && ~/.local/bin/uv run python -m trading.run'"
