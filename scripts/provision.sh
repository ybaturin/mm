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

install_daily_timer() {
  # Run the daily pre-market cycle from a systemd timer instead of crontab, so it logs
  # to journald and is managed alongside the bot. Idempotent. New runs take run_lock, so
  # the command bot pauses automatically while a cycle runs.
  if ! command -v systemctl >/dev/null 2>&1; then
    echo ">> no systemd here — skipping daily timer (keep your crontab entry)"
    return 0
  fi
  local repo_dir home_dir
  repo_dir="$PWD"
  home_dir="$HOME"
  echo ">> installing systemd units mm-daily.service + mm-daily.timer"
  cat > /etc/systemd/system/mm-daily.service <<EOF
[Unit]
Description=mm daily pre-market trading run
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$repo_dir
Environment=HOME=$home_dir
ExecStart=$repo_dir/scripts/run-daily.sh
TimeoutStartSec=3600
EOF
  cat > /etc/systemd/system/mm-daily.timer <<EOF
[Unit]
Description=Run the mm daily trading cycle on weekday mornings (13:00 UTC)

[Timer]
OnCalendar=Mon..Fri 13:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable mm-daily.timer >/dev/null 2>&1 || true
  systemctl start mm-daily.timer
  # Migrate off the legacy crontab entry so the cycle never double-runs.
  if crontab -l 2>/dev/null | grep -q "run-daily.sh"; then
    ( crontab -l 2>/dev/null | grep -v "run-daily.sh" ) | crontab -
    echo ">> removed legacy run-daily.sh crontab entry (now managed by mm-daily.timer)"
  fi
  echo ">> mm-daily.timer: $(systemctl is-active mm-daily.timer 2>/dev/null || echo unknown)"
}

install_bot_service
install_daily_timer

echo ">> provisioned. Smoke-check the scheme with:"
echo "   uv run python -m trading.orchestrator.simulate --days 30"
echo ">> command bot logs:   journalctl -u mm-bot.service -f"
echo ">> daily run logs:     journalctl -u mm-daily.service -f   (trigger now: systemctl start mm-daily.service)"
echo ">> next scheduled run: systemctl list-timers mm-daily.timer"
