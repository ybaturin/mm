#!/usr/bin/env bash
# Provision THIS host to run the trading system. Idempotent.
# Run from the repo root on the server:  bash scripts/provision.sh
set -euo pipefail

if [ ! -x "$HOME/.local/bin/uv" ] && ! command -v uv >/dev/null 2>&1; then
  echo ">> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo ">> uv sync"
uv sync --frozen

echo ">> tests"
uv run pytest -q

install_bot_service() {
  # Keep the Telegram command bot (python -m trading.bot) running under systemd so the
  # command menu survives reboots and crashes. Idempotent: rewrite unit, restart on new code.
  if ! command -v systemctl >/dev/null 2>&1; then
    echo ">> no systemd here — skipping command-bot service (run 'uv run python -m trading.bot' manually)"
    return 0
  fi
  local uv_bin repo_dir
  uv_bin="$(command -v uv)"
  repo_dir="$PWD"
  if [ ! -f "$repo_dir/.env" ]; then
    echo "!! warning: $repo_dir/.env not found — the bot needs TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID there or it will crash-loop"
  fi
  echo ">> installing systemd unit mm-bot.service"
  cat > /etc/systemd/system/mm-bot.service <<EOF
[Unit]
Description=mm Telegram command bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$repo_dir
EnvironmentFile=$repo_dir/.env
ExecStart=$uv_bin run python -m trading.bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable mm-bot.service >/dev/null 2>&1 || true
  systemctl restart mm-bot.service   # picks up the freshly synced code
  echo ">> mm-bot.service: $(systemctl is-active mm-bot.service 2>/dev/null || echo unknown)"
}

install_bot_service

echo ">> provisioned. Smoke-check the scheme with:"
echo "   uv run python -m trading.orchestrator.simulate --days 30"
echo ">> command bot logs:  journalctl -u mm-bot.service -f"
