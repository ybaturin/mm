# CLAUDE.md

Guidance for Claude Code working in this repo. Read this first.

## What this is

An automated daily trading system. Claude (the LLM) **proposes** trades as structured
data from a briefing; a deterministic risk engine validates/sizes/rejects, an optional
validation panel can veto, and only survivors execute. Claude never executes anything.

Decision pipeline (per agent, per day):
`build_briefing → strategy.propose (Claude) → guardrails → panel (veto) → execute → journal`

Claude trades only symbols present in the briefing — the fixed watchlist
`config/universe.toml` ∪ currently-held positions. It never opens anything outside that
universe (the prompt enforces it); news and indicators are gathered for that same set.

Key code: `src/trading/agent/` (LLM call + prompts), `src/trading/data/briefing.py`
(everything the model sees), `src/trading/guardrails/`, `src/trading/orchestrator/`
(`cycle.py`, `daily.py`), `src/trading/reporting/` (Telegram), `src/trading/bot.py`
(the long-running command bot / `mm-bot.service` — a getUpdates poller, separate from
`TelegramNotifier`, which only sends and does the one confirmation poll during a run).

## Commands

```bash
uv run pytest -q                                   # full test suite
uv run python -m trading.run [--date YYYY-MM-DD]   # one daily cycle
uv run python -m trading.orchestrator.simulate --days 30   # free deterministic backtest
./scripts/deploy.sh root@95.179.169.77             # test → rsync → re-provision → restart bot + daily timer
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
- **The daily run is scheduled by systemd, not crontab:** `mm-daily.timer` → `mm-daily.service`
  (weekday 13:00 UTC). `provision.sh` installs/enables both units and removes the legacy
  `run-daily.sh` crontab entry, so a stray cron line doesn't double-run. Logs: `journalctl -u
  mm-daily`. `deploy.sh` warns (does not kill) when a run is in flight — it may be awaiting your
  Telegram confirmation.
- **Keep the `httpx` logger at WARNING.** At INFO it logs every request URL, and Telegram URLs
  embed the bot token — that leaks the secret into the journal. The bot sets this in `main()`;
  bot logs are `journalctl -u mm-bot`.
- To eyeball message formatting, run the real system against the test bot on the VPS — it shows
  every message with real formatting and working buttons. (There is no preview script.)
- **Track-record DBs are per-mode and must never commingle:** `data/trading-fake.db` /
  `-paper.db` / `-live.db`, chosen by `resolve_db_path()` from `BROKER`/`IBKR_PORT`. The command
  bot resolves the same path — keep them aligned. Don't delete the active DB: it is the forward
  track record the go-live gate reads. Switching to real money starts a clean ledger.
- **One IBKR account = one agent.** Each agent keeps its own budget/positions; two agents sharing
  one account commingle, and the next run's reconcile sees the mismatch and freezes them. For
  IBKR give each agent its own account (`IBKR_ACCOUNTS`, aligned to profile order) or run a
  single agent.
- **Reading the journal:** every run on a given day is stamped with the same `ts`
  (`<date>T13:30:00Z`), so same-day runs are indistinguishable by timestamp — order by row `id`.
  A decision's `outcome` is NOT updated to "approved" on confirmation: it stays
  `needs_confirmation` (only a reject sets `declined`). So a `needs_confirmation` row WITH a
  matching fill means it was confirmed and executed.

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
- **Live integrations are not unit-tested.** IBKR, Claude, Telegram, and yfinance are verified
  via `scripts/smoke_*.py`; unit tests cover pure logic and the `Fake*` doubles (`FakeBroker`,
  `FakeMarketDataSource`, `FakeNotifier`).
- **No hidden clock.** Core functions take an explicit `as_of_date`/`ts`; only `main()`/glue
  reads the system clock — keeps tests deterministic and historical replays point-in-time.
- **`simulate` runs `FakeStrategy`, not Claude.** LLM decisions can't be backtested
  (training-cutoff/search look-ahead), so the model is validated only by forward (paper) runs;
  `simulate.py` proves the plumbing, not the strategy's edge.

## More docs

- `docs/DEPLOY.md` — server, deploy, cron, secrets, state, go-live gate.
- `docs/agents.md` — agent/profile design notes.
