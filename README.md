# IBKR Trading Agents

Autonomous, risk-controlled trading agents on Interactive Brokers (paper first).
See `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md` for the full design.

## Status

- Plan 1 of 10: domain models, risk-profile config, deterministic Guardrails Engine. ✓
- Plan 2 of 10: SQLite persistence — ledger, decision journal, fills, equity snapshots. ✓
- Plan 3 of 10: Broker boundary — Protocol, FakeBroker, IBKRBroker (ib-async). ✓
- Plan 4 of 10: Data Collector — MarketDataSource (yfinance), indicators, briefing. ✓
- Plan 5 of 10: Agent Core — Claude turns a briefing into trade proposals. ✓
- Plan 6 of 10: Orchestrator + Simulation — run_cycle wires the whole pipeline; a
  multi-day simulation proves the scheme end-to-end with no live money. ✓
- Plan 7 of 10: Validation Panel — role-diverse Claude validators (skeptic / catalyst /
  devil's advocate) that can veto a proposal after guardrails; subtractive only,
  per-profile veto rule, every veto logged. Optional step in run_cycle. ✓
- Plan 8 of 10: Reporter — Telegram digests, fill notifications, alerts, P&L, and
  inline-button confirmation of large trades (`make_confirm` plugs into run_cycle).
  Pure formatters + FakeNotifier are tested; live Bot API via a smoke script. ✓

Run the whole scheme on synthetic data (deterministic, free, no API key, no IBKR):

    uv run python -m trading.orchestrator.simulate --days 30

Verify Telegram (needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):

    uv run python scripts/smoke_telegram.py

The whole system can run against `FakeBroker` with no live connection. Real paper
trading uses `IBKRBroker`; verify the connection with:

    IBKR_PORT=4002 uv run python scripts/smoke_ibkr.py

(requires a running IB Gateway logged into a paper account).

Tradable universe lives in `config/universe.toml`. Verify live data with:

    uv run python scripts/smoke_yfinance.py AAPL

Try a live proposal (needs ANTHROPIC_API_KEY; uses canned prices, no IBKR):

    uv run python scripts/smoke_agent.py

## Develop

```bash
uv run pytest        # run the test suite
```

Risk limits live in `config/profiles.toml` (no code changes needed to tune them).
