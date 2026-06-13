# The Three Agents

All three are the **same Claude model** (`claude-opus-4-8`) run once a day, pre-market,
against the **same briefing** (cash, positions, and per-symbol price + SMA20 / SMA50 /
RSI14 / 5-day return). They differ in two ways only: their **hard limits** (numbers) and
their **mandate** (temperament, injected into the system prompt). They are not three
different brains — three configs of one.

Each agent only *proposes* trades. Every proposal then passes through the deterministic
Guardrails Engine and (optionally) the Validation Panel before anything executes.

## How a decision is made

1. Data Collector builds the briefing for the agent.
2. Claude reads it and proposes trades as structured data, guided by the agent's mandate
   and limits. An empty list is a valid answer.
3. Guardrails validate, size, and reject/route each proposal (hard limits).
4. Validation Panel (optional) can veto on judgment grounds.
5. Approved trades execute; everything is recorded.

The decision *principle* is Claude's reasoning over technical indicators within the
agent's mandate — discretionary, not a fixed formula and not a proven edge. This is why
the system runs months on paper and gates go-live on beating SPY.

## Profiles

| | Conservative | Moderate | Aggressive |
|---|---|---|---|
| Budget | $5k | $5k | $5k |
| Max per position | 15% | 25% | 40% |
| Min positions | 8 | 5 | 3 |
| Shorts | no | no | yes (with stop) |
| Stop-loss target | 8% | 10% | 12% |
| Max trades/day | 2 | 4 | 8 |
| Daily-loss kill | −3% | −5% | −8% |
| Drawdown kill | −10% | −15% | −25% |
| Panel veto rule | any | majority | majority |

### Mandates (temperament)

- **Conservative** — Capital preservation first. Trades rarely, only on strong confirmed
  signals; cash is a fine position; avoids concentration.
- **Moderate** — Balanced swing trading over days to weeks; neither timid nor reckless;
  cuts losers at the stop.
- **Aggressive** — Hunts momentum and decisive moves; accepts concentration and shorts on
  clear setups; acts faster — but always with a hard stop.

The operative text lives in `config/profiles.toml` (`mandate`) and is injected by
`src/trading/agent/prompts.py`. Edit it there, not here.
