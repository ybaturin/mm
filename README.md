# IBKR Trading Agents

Autonomous, risk-controlled trading agents on Interactive Brokers (paper first).
See `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md` for the full design.

## Status

- Plan 1 of 9: domain models, risk-profile config, deterministic Guardrails Engine. ✓
- Plan 2 of 9: SQLite persistence — ledger, decision journal, fills, equity snapshots. ✓
- Plan 3 of 9: Broker boundary — Protocol, FakeBroker, IBKRBroker (ib-async). ✓
- Plan 4 of 9: Data Collector — MarketDataSource (yfinance), pure indicators, and the
  `build_briefing()` snapshot fed to the agent. ✓

The whole system can run against `FakeBroker` with no live connection. Real paper
trading uses `IBKRBroker`; verify the connection with:

    IBKR_PORT=4002 uv run python scripts/smoke_ibkr.py

(requires a running IB Gateway logged into a paper account).

Tradable universe lives in `config/universe.toml`. Verify live data with:

    uv run python scripts/smoke_yfinance.py AAPL

## Develop

```bash
uv run pytest        # run the test suite
```

Risk limits live in `config/profiles.toml` (no code changes needed to tune them).
