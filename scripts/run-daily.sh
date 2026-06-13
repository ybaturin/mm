#!/usr/bin/env bash
# Daily pre-market run, for cron. Sources .env, prevents overlapping runs (flock),
# and caps a stuck run (timeout). Cron redirects this script's output to a log.
#
#   0 13 * * 1-5  /root/mm/scripts/run-daily.sh >> /root/mm/run.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
exec flock -n /tmp/mm-daily.lock timeout 3600 "${HOME}/.local/bin/uv" run python -m trading.run
