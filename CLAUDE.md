# CLAUDE.md

Guidance for Claude Code working in this repo. Read this first.

## What this is

An automated daily trading system. Claude (the LLM) **proposes** trades as structured
data from a briefing; a deterministic risk engine validates/sizes/rejects, an optional
validation panel can veto, and only survivors execute. Claude never executes anything.

Decision pipeline (per agent, per day):
`build_briefing → strategy.propose (Claude) → guardrails → panel (veto) → execute → journal`

Key code: `src/trading/agent/` (LLM call + prompts), `src/trading/data/briefing.py`
(everything the model sees), `src/trading/guardrails/`, `src/trading/orchestrator/`
(`cycle.py`, `daily.py`), `src/trading/reporting/` (Telegram).

## Commands

```bash
uv run pytest -q                                   # full test suite
uv run python -m trading.run [--date YYYY-MM-DD]   # one daily cycle
uv run python -m trading.orchestrator.simulate --days 30   # free deterministic backtest
./scripts/deploy.sh root@95.179.169.77             # test → rsync → re-provision → restart bot
```

## Operational knowledge (hard-won — don't relearn the hard way)

- **Production runs belong on the VPS, not your laptop.** On the host the daily run and the
  command bot (`mm-bot.service`) share one DB, and `run_lock` pauses the bot for the duration
  of a run — so only one process polls Telegram `getUpdates` at a time.
- **Never run a local dry-run against the same Telegram bot token while the VPS bot is live.**
  Telegram delivers each update to exactly ONE `getUpdates` consumer. With two pollers (your
  local run + the VPS `mm-bot`), the VPS bot grabs the confirmation **callback** and your
  local run never sees the tap: the ✅/❌ buttons appear and send fine (`sendMessage ok=True`),
  but **tapping does nothing** and the run hangs until timeout. The local `run_lock` lives in
  the local DB, so the VPS bot doesn't know a local run is happening. For local interactive
  testing use a **separate bot token**, or stop `mm-bot.service` on the VPS first.
- Confirmation buttons themselves work — the above is a poller-contention issue, not a bug in
  `TelegramNotifier.request_confirmation`.
- To eyeball message formatting, run the real system against the test bot on the VPS — it shows
  every message with real formatting and working buttons. (There is no preview script.)

## Briefing enrichment (self-aware briefing)

`build_briefing` can attach two extra blocks the model reasons over:
- **Memory** (`analysis/memory.py`, `analysis/round_trips.py`): the agent's own open positions
  with original rationale + unrealized P&L, recent closed round-trips with realized P&L, and
  win-rate. Point-in-time — safe in backtests.
- **News** (`data/news.py`): recent per-symbol headlines via `yfinance` (`NEWS=yfinance|fake`,
  graceful degradation). **Off in the backtest** — `yfinance .news` has no point-in-time
  access, so using it during a historical replay would be look-ahead.

Spec/plan: `docs/superpowers/specs/2026-06-14-self-aware-briefing-design.md`,
`docs/superpowers/plans/2026-06-14-self-aware-briefing.md`.

## Conventions

- **Commit directly to `main`** — do not create feature branches (current preference).
  Don't push to `origin` or deploy without being asked.
- **All in-code comments and docstrings in English** (even though we converse in Russian).
  Rationale fields the model writes are Russian (the owner reads Russian).
- Env config: see `.env.example` (mode flags, `NEWS`, secrets). The app does NOT auto-load
  `.env`; source it: `set -a; . ./.env; set +a`.

## More docs

- `docs/DEPLOY.md` — server, deploy, cron, secrets, state, go-live gate.
- `docs/agents.md` — agent/profile design notes.
