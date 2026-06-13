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

echo ">> running tests locally before shipping anything"
uv run pytest -q || { echo "!! tests are red — refusing to deploy. Fix them first."; exit 1; }

echo ">> syncing code to $HOST:$REMOTE_DIR"
# NOTE: exclude '/data' is anchored to the repo root on purpose — a bare 'data'
# would also wrongly exclude src/trading/data. Do not change it to 'data'.
rsync -az --delete -e "ssh -i $KEY" \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.idea' --exclude '.git' --exclude '/data' --exclude '*.db' \
  --exclude '.claude' --exclude '.env' --exclude '*.log' \
  ./ "$HOST:$REMOTE_DIR/"

echo ">> checking for an in-flight daily run on $HOST"
if ssh -i "$KEY" "$HOST" 'systemctl is-active --quiet mm-daily.service' 2>/dev/null; then
  echo "!! mm-daily.service is running — leaving it untouched (it may be awaiting your Telegram"
  echo "   confirmation). The command bot is still restarted; run_lock keeps them from colliding."
fi

echo ">> provisioning on $HOST"
ssh -i "$KEY" "$HOST" "cd $REMOTE_DIR && bash scripts/provision.sh"

echo ">> done."
echo ">> the Telegram command bot now runs under systemd (mm-bot.service), restarted with this deploy."
echo "   check it:  ssh -i $KEY $HOST 'systemctl status mm-bot.service'"
echo ">> to run a daily cycle on the host:"
echo "   ssh -i $KEY $HOST 'cd $REMOTE_DIR && ~/.local/bin/uv run python -m trading.run'"
