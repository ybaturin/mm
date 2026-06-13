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

echo ">> provisioned. Smoke-check the scheme with:"
echo "   uv run python -m trading.orchestrator.simulate --days 30"
