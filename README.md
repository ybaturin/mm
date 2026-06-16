# IBKR Trading Agents

Three risk-profile agents (conservative / moderate / aggressive) that analyse the market
once a day pre-market, propose trades via Claude, filter them through a deterministic
guardrails engine and an adversarial validation panel, execute on Interactive Brokers, and
report to you over Telegram. Paper-first; real money only after a 6-month forward track
record that beats SPY. Full design: `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md`.

## Status — all 10 plans complete

1. Guardrails engine · 2. Persistence · 3. Broker boundary · 4. Data collector ·
5. Agent core (Claude) · 6. Orchestrator + simulation · 7. Validation panel ·
8. Reporter (Telegram) · 9. Watchdog + reconciliation · 10. Daily run + deploy.

## Run it

```bash
make test                                   # unit + integration tests
make sim                                    # 30-day scheme simulation (free, deterministic)

# Full daily wiring with NO IBKR and NO money — live Claude + Telegram on simulated fills
# priced from real yfinance data:
BROKER=fake STRATEGY=claude NOTIFIER=telegram uv run python -m trading.run

# Free, no keys at all:
BROKER=fake STRATEGY=fake NOTIFIER=fake PANEL=off uv run python -m trading.run
```

## Edge measurer (pre-trading)

A standalone batch that tests whether deep LLM reading of earnings primary sources
predicts post-earnings drift, BEFORE risking money. It never touches the trading loop.

```bash
# One-time pilot (needs FMP_API_KEY — a single paid month, cancel after):
FMP_API_KEY=... EDGE_CUTOFF=2026-02-01 EDGE_TO=2026-05-31 \
  uv run python -m trading.edge.run
```

Lookahead is avoided by only testing earnings dated after the model's knowledge cutoff
(genuinely out-of-sample) plus a memory-probe filter. Output: information coefficient,
long-short spread after costs, calibration, and significance vs coin-flip and dumb-PEAD
benchmarks. Full design: `docs/superpowers/specs/2026-06-14-llm-edge-measurer-design.md`.

### Mechanical PEAD study (no LLM)

After the LLM edge measurer found deep reading adds nothing over a one-line EPS-surprise
rule, this studies the mechanical PEAD anomaly directly — sweeping cap-tier x horizon
(1-60d) x SUE normalization on a train window, pre-registering one config, and scoring it
ONCE on a held-out test window (anti-overfit). Net of realistic tiered trading costs.

```bash
ALPHAVANTAGE_API_KEY=... EDGE_UNIVERSE_FILE=config/edge_universe_smid.toml \
  PEAD_SPLIT=2026-04-01 uv run python -m trading.edge.pead_study
```

Spec: `docs/superpowers/specs/2026-06-16-mechanical-pead-study-design.md`.

## Deploy (Raspberry Pi or VPS)

1. `cp .env.example .env` and fill in secrets.
2. `docker compose build && docker compose run --rm app` to verify.
3. Schedule a host cron pre-market, e.g. weekdays 13:00 UTC:
   `0 13 * * 1-5  cd /path/to/mm && docker compose run --rm app >> run.log 2>&1`
4. `make backup` before moving hosts — the SQLite DB is your whole track record.

IB Gateway has no ARM build: on a Pi, run it on a separate x86 host (or use the Client
Portal Web API) and set `IBKR_HOST`/`IBKR_PORT`. Until then, `BROKER=fake` runs the entire
system end-to-end.
