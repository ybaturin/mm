# IBKR Trading Agents

Autonomous, risk-controlled trading agents on Interactive Brokers (paper first).
See `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md` for the full design.

## Status

- Plan 1 of 9: domain models, risk-profile config, deterministic Guardrails Engine. ✓
- Plan 2 of 9: SQLite persistence — account ledger, decision journal, fills, daily
  equity snapshots, behind a repository layer. ✓

The trade DB defaults to a local SQLite file; the repository layer keeps a future
Postgres swap isolated from the rest of the code.

## Develop

```bash
uv run pytest        # run the test suite
```

Risk limits live in `config/profiles.toml` (no code changes needed to tune them).
