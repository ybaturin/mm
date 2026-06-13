# IBKR Trading Agents

Autonomous, risk-controlled trading agents on Interactive Brokers (paper first).
See `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md` for the full design.

## Status

Plan 1 of 9 complete: domain models, risk-profile config, and the deterministic
**Guardrails Engine** — the safety core that validates every trade proposal and
returns reject / auto-execute / needs-confirmation.

## Develop

```bash
uv run pytest        # run the test suite
```

Risk limits live in `config/profiles.toml` (no code changes needed to tune them).
