# Daily Orchestrator & Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The final wiring. `run_daily()` runs the full pre-market cycle for every agent — skip-if-frozen → reconcile → cycle (with the real Validation Panel and Telegram confirmation) → watchdog → digest. Then a `build_components()` factory assembles the real pieces from env, a `python -m trading.run` entry point runs one day, and Docker + cron + a backup target make it deployable (Raspberry Pi or VPS).

**Architecture:** `run_daily()` is fully injected and integration-tested end-to-end on fakes (freeze, reconcile, watchdog, digest, confirmation, panel all exercised) — it is the valuable, tested core. `build_components()` and `main()` are thin glue that pick real implementations from env (`BROKER=fake|ibkr`); like the other smoke paths they're verified by running, not unit tests. `BROKER=fake` is a first-class mode: real Claude + real Telegram on FakeBrokers seeded with live yfinance prices — the whole experience on your phone, with zero IBKR and zero money.

**Tech Stack:** Python 3.12+, Docker (multi-arch / ARM-friendly), cron, stdlib, `pytest`.

This is plan **10 of 10**. Depends on every prior plan. Spec §4, §8.

---

## Existing interfaces this plan consumes (verified)

```python
# run_cycle(agent_id, profile, broker, source, accounts, journal, strategy, universe, as_of_date, ts, confirm=None, panel=None)  # plans 6,7
# FreezeStore(conn): is_frozen/freeze/reason; GLOBAL                                                                              # plan 9
# reconcile(ledger, broker)->ReconResult(ok, discrepancies)                                                                       # plan 9
# Watchdog(starting_nav, floor_fraction).check(broker, prices)->WatchdogResult(breached, nav, floor); flatten(broker, prices)     # plan 9
# make_confirm(notifier)->ConfirmFn; FakeNotifier; format_alert/format_digest                                                     # plan 8
# AgentCore (Strategy), ValidationPanel; FakeStrategy                                                                             # plans 5,6,7
# IBKRBroker / FakeBroker; YFinanceSource; AccountRepository/JournalRepository; load_profiles/load_universe; connect/init_db
```

## File Structure

```
src/trading/orchestrator/daily.py    # run_daily() + summary helper (tested)
src/trading/run.py                   # build_components() + main() — `python -m trading.run`
tests/test_daily.py
Dockerfile
docker-compose.yml
.env.example
Makefile
```

---

## Task 1: run_daily — the full daily loop

**Files:**
- Create: `src/trading/orchestrator/daily.py`
- Test: `tests/test_daily.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_daily.py
import pytest
from trading.broker.fake import FakeBroker
from trading.broker.types import Action
from trading.config import RiskProfile
from trading.data.bars import Bar
from trading.data.fake_source import FakeMarketDataSource
from trading.domain import TradeProposal
from trading.orchestrator.daily import run_daily
from trading.orchestrator.strategy import FakeStrategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.freezes import GLOBAL, FreezeStore
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.reporting.notifier import FakeNotifier
from trading.validation.panel import PanelResult, RoleVerdict


def profile(name, **o):
    base = dict(name=name, budget=5000.0, max_position_pct=0.25, min_positions=5,
                allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
                daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
                auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25,
                veto_rule="majority", mandate="test")
    base.update(o)
    return RiskProfile(**base)


def uptrend(n=60):
    return [Bar(f"2026-04-{i+1:02d}", 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1000)
            for i in range(n)]


class AllowingPanel:
    def review(self, proposal, briefing, veto_rule):
        return PanelResult(blocked=False, verdicts=[RoleVerdict("risk_skeptic", False, "")])


class NoopStrategy:
    def propose(self, briefing, profile):
        return []


@pytest.fixture
def env(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return (AccountRepository(conn), JournalRepository(conn), FreezeStore(conn))


def fresh_brokers(names, source, universe):
    brokers = {}
    for name in names:
        b = FakeBroker(cash=5000.0)
        for s in universe:
            b.set_price(s, source.latest_price(s))
        brokers[name] = b
    return brokers


def test_run_daily_executes_and_sends_a_digest_per_agent(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate"), "aggressive": profile("aggressive", max_position_pct=0.40)}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend()})
    brokers = fresh_brokers(profiles, source, universe)
    notifier = FakeNotifier(confirm_result=True)

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=FakeStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    # each agent traded and got a digest
    assert any("moderate" in m for m in notifier.messages)
    assert any("aggressive" in m for m in notifier.messages)
    assert brokers["moderate"].positions()


def test_run_daily_skips_frozen_agent(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend()})
    brokers = fresh_brokers(profiles, source, universe)
    freezes.freeze("moderate", "manual halt", "2026-06-14T00:00:00Z")
    notifier = FakeNotifier()

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=FakeStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    assert brokers["moderate"].positions() == []           # never ran
    assert any("skipped" in m.lower() for m in notifier.messages)


def test_run_daily_global_freeze_skips_everyone(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate"), "aggressive": profile("aggressive")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend()})
    brokers = fresh_brokers(profiles, source, universe)
    freezes.freeze(GLOBAL, "kill switch", "2026-06-14T00:00:00Z")
    notifier = FakeNotifier()

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=FakeStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    assert all(b.positions() == [] for b in brokers.values())


def test_run_daily_watchdog_flattens_and_freezes_on_breach(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate")}
    universe = ["AAPL"]
    # source price has collapsed to 40
    source = FakeMarketDataSource({"AAPL": [Bar("2026-06-15", 40.0, 40.0, 40.0, 40.0, 1000)]})
    # broker pre-holds a losing position: 20 @ 100 bought earlier, cash 3000
    broker = FakeBroker(cash=5000.0)
    broker.set_price("AAPL", 100.0)
    broker.place_market_order("AAPL", Action.BUY, 20)
    broker.set_price("AAPL", 40.0)                          # now worth far less
    brokers = {"moderate": broker}
    notifier = FakeNotifier()

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=NoopStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              floor_fraction=0.8)

    # NAV = 3000 + 20*40 = 3800 < 0.8*5000 = 4000 -> flatten + freeze + alert
    assert broker.positions() == []
    assert freezes.is_frozen("moderate") is True
    assert any("watchdog" in m.lower() for m in notifier.messages)


def test_run_daily_freezes_on_reconciliation_mismatch(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend()})
    broker = FakeBroker(cash=5000.0)
    broker.set_price("AAPL", source.latest_price("AAPL"))
    brokers = {"moderate": broker}
    # ledger claims a position the broker doesn't have -> reconcile fails before the cycle
    from trading.domain import AgentState, Position
    accounts.save_state(AgentState("moderate", cash=5000.0,
                                   positions=[Position("AAPL", 99, 100.0)],
                                   peak_equity=5000.0, equity_day_start=5000.0))
    notifier = FakeNotifier()

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=FakeStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    assert freezes.is_frozen("moderate") is True
    assert any("reconciliation" in m.lower() for m in notifier.messages)
    assert broker.positions() == []                        # cycle never ran
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_daily.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.orchestrator.daily'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/orchestrator/daily.py
from __future__ import annotations

from trading.config import RiskProfile
from trading.data.bars import MarketDataSource
from trading.domain import AgentState
from trading.orchestrator.cycle import run_cycle
from trading.orchestrator.strategy import Strategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.freezes import GLOBAL, FreezeStore
from trading.persistence.journal import JournalRepository
from trading.reporting.format import format_alert, format_digest
from trading.reporting.notifier import Notifier, make_confirm
from trading.safety.reconcile import reconcile
from trading.safety.watchdog import Watchdog, flatten


def _summary(journal: JournalRepository, agent_id: str, date: str):
    fills = [r for r in journal.fills_for(agent_id) if r["ts"].startswith(date)]
    executed = [f'{r["intent"]} {r["quantity"]} {r["symbol"]} @ {r["price"]:g}' for r in fills]
    rejected = sum(1 for r in journal.decisions_for(agent_id)
                   if r["ts"].startswith(date) and r["outcome"] == "rejected")
    vetoed = sum(1 for r in journal.vetoes_for(agent_id) if r["ts"].startswith(date))
    return executed, rejected, vetoed


def run_daily(
    profiles: dict[str, RiskProfile],
    brokers: dict[str, object],
    source: MarketDataSource,
    strategy: Strategy,
    panel,
    notifier: Notifier,
    accounts: AccountRepository,
    journal: JournalRepository,
    freezes: FreezeStore,
    universe: list[str],
    as_of_date: str,
    ts: str,
    floor_fraction: float = 0.8,
) -> None:
    """Run the full pre-market cycle for every agent. The production keystone.

    Per agent: skip if frozen -> reconcile ledger vs broker -> run_cycle (panel +
    Telegram confirm) -> watchdog (flatten + freeze on breach) -> digest.
    """
    confirm = make_confirm(notifier)
    prices = {s: source.latest_price(s) for s in universe}

    for name, profile in profiles.items():
        if freezes.is_frozen(GLOBAL):
            notifier.notify(format_alert("frozen", f"GLOBAL halt active — {name} skipped"))
            continue
        if freezes.is_frozen(name):
            notifier.notify(format_alert("frozen", f"{name} skipped: {freezes.reason(name)}"))
            continue

        broker = brokers[name]

        prev = accounts.get_state(name)
        if prev is not None:
            rec = reconcile(prev, broker)
            if not rec.ok:
                detail = "; ".join(rec.discrepancies)
                freezes.freeze(name, detail, ts)
                notifier.notify(format_alert("reconciliation", f"{name}: {detail}"))
                continue

        run_cycle(
            agent_id=name, profile=profile, broker=broker, source=source,
            accounts=accounts, journal=journal, strategy=strategy, universe=universe,
            as_of_date=as_of_date, ts=ts, confirm=confirm, panel=panel,
        )

        result = Watchdog(profile.budget, floor_fraction).check(broker, prices)
        if result.breached:
            flatten(broker, prices)
            post = accounts.get_state(name)
            accounts.save_state(AgentState(
                name, broker.cash(), broker.positions(),
                post.peak_equity, post.equity_day_start))
            freezes.freeze(name, f"NAV {result.nav:.0f} < floor {result.floor:.0f}", ts)
            notifier.notify(format_alert(
                "watchdog",
                f"{name}: NAV {result.nav:.0f} < floor {result.floor:.0f} — flattened & frozen"))

        executed, rejected, vetoed = _summary(journal, name, as_of_date)
        notifier.notify(format_digest(name, as_of_date, executed, rejected, vetoed))
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_daily.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/orchestrator/daily.py tests/test_daily.py
git commit -m "feat: run_daily wires the full production daily loop"
```

---

## Task 2: Component factory and entry point

**Files:**
- Create: `src/trading/run.py`
- Test: covered by Task 1 (run_daily); `build_components`/`main` are glue verified by running.

- [ ] **Step 1: Write `src/trading/run.py`**

```python
# src/trading/run.py
"""Assemble real components from the environment and run one daily cycle.

Env:
  DB_PATH                 default data/trading.db
  BROKER                  fake (default) | ibkr
  FLOOR_FRACTION          default 0.8
  ANTHROPIC_API_KEY       required unless STRATEGY=fake
  STRATEGY                claude (default) | fake
  PANEL                   on (default) | off
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   required unless NOTIFIER=fake
  NOTIFIER                telegram (default) | fake
  IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID_BASE   (BROKER=ibkr)

BROKER=fake is a real dry-run: live Claude + live Telegram on simulated fills priced
from real yfinance data. No IBKR, no money.

Run:  uv run python -m trading.run [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import os
from datetime import date as date_cls

from trading.config import load_profiles
from trading.data.briefing import load_universe
from trading.data.yfinance_source import YFinanceSource
from trading.orchestrator.daily import run_daily
from trading.orchestrator.strategy import FakeStrategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.freezes import FreezeStore
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.reporting.notifier import FakeNotifier


def _broker_for(profile, index: int):
    if os.environ.get("BROKER", "fake") == "ibkr":
        from trading.broker.ibkr import IBKRBroker
        base = int(os.environ.get("IBKR_CLIENT_ID_BASE", "1"))
        broker = IBKRBroker(
            host=os.environ.get("IBKR_HOST", "127.0.0.1"),
            port=int(os.environ.get("IBKR_PORT", "4002")),
            client_id=base + index,
        )
        broker.connect()
        return broker
    from trading.broker.fake import FakeBroker
    return FakeBroker(cash=profile.budget)


def build_components():
    profiles = load_profiles("config/profiles.toml")
    universe = load_universe("config/universe.toml")
    source = YFinanceSource()

    db_path = os.environ.get("DB_PATH", "data/trading.db")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = connect(db_path)
    init_db(conn)
    accounts, journal, freezes = (AccountRepository(conn), JournalRepository(conn),
                                  FreezeStore(conn))

    brokers = {name: _broker_for(p, i) for i, (name, p) in enumerate(profiles.items())}
    # FakeBrokers need a fill price; seed from live data.
    for name, broker in brokers.items():
        if hasattr(broker, "set_price"):
            for s in universe:
                broker.set_price(s, source.latest_price(s))

    if os.environ.get("STRATEGY", "claude") == "fake":
        strategy = FakeStrategy()
    else:
        from trading.agent.core import AgentCore
        strategy = AgentCore()

    panel = None
    if os.environ.get("PANEL", "on") == "on":
        from trading.validation.panel import ValidationPanel
        panel = ValidationPanel()

    if os.environ.get("NOTIFIER", "telegram") == "fake":
        notifier = FakeNotifier()
    else:
        from trading.reporting.telegram import TelegramNotifier
        notifier = TelegramNotifier()

    return dict(profiles=profiles, brokers=brokers, source=source, strategy=strategy,
                panel=panel, notifier=notifier, accounts=accounts, journal=journal,
                freezes=freezes, universe=universe,
                floor_fraction=float(os.environ.get("FLOOR_FRACTION", "0.8")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one daily trading cycle.")
    parser.add_argument("--date", default=date_cls.today().isoformat())
    args = parser.parse_args()

    components = build_components()
    run_daily(as_of_date=args.date, ts=f"{args.date}T13:30:00Z", **components)
    print(f"Daily run complete for {args.date}.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports and runs in the fully-fake mode**

Run:
```bash
NOTIFIER=fake STRATEGY=fake PANEL=off BROKER=fake DB_PATH=/tmp/run_smoke.db \
  uv run python -m trading.run --date 2026-06-15
```
Expected: prints `Daily run complete for 2026-06-15.` with no errors (real yfinance data,
fake everything else — a free, no-key smoke of the whole wiring).

- [ ] **Step 3: Commit**

```bash
git add src/trading/run.py
git commit -m "feat: component factory and `python -m trading.run` entry point"
```

---

## Task 3: Deployment — Docker, compose, cron, backup

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `Makefile`

- [ ] **Step 1: Write `Dockerfile` (ARM-friendly)**

```dockerfile
# Dockerfile — runs on arm64 (Raspberry Pi) and amd64 alike
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/
COPY config/ ./config/

ENV DB_PATH=/data/trading.db
VOLUME ["/data"]

# One-shot daily run; the scheduler (cron/compose) invokes this.
CMD ["uv", "run", "python", "-m", "trading.run"]
```

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
# docker-compose.yml
# `app` + `db volume`. IB Gateway is NOT here: it has no ARM build, so on a Pi run it on a
# separate x86 host (or use the Client Portal Web API) and point IBKR_HOST/PORT at it.
services:
  app:
    build: .
    env_file: .env
    volumes:
      - trading-data:/data
    # Run once and exit; a host cron triggers it pre-market (see Makefile / README).
    # For an always-on container instead, replace with a scheduler entrypoint.
    restart: "no"

volumes:
  trading-data:
```

- [ ] **Step 3: Write `.env.example`**

```bash
# .env.example — copy to .env and fill in. NEVER commit .env (it is gitignored).

# Mode
BROKER=fake            # fake | ibkr
STRATEGY=claude        # claude | fake
PANEL=on               # on | off
NOTIFIER=telegram      # telegram | fake
FLOOR_FRACTION=0.8

# Secrets
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# IBKR (only when BROKER=ibkr) — Gateway on this host or a reachable x86 box
IBKR_HOST=127.0.0.1
IBKR_PORT=4002         # paper Gateway; 4001 = live
IBKR_CLIENT_ID_BASE=1
```

- [ ] **Step 4: Write `Makefile`**

```makefile
# Makefile — common operations
.PHONY: test sim run backup restore up

test:
	uv run pytest -q

sim:
	uv run python -m trading.orchestrator.simulate --days 30

run:
	uv run python -m trading.run

# Portable track record: the SQLite file IS the state. Back it up before moving hosts.
backup:
	@cp $${DB_PATH:-data/trading.db} backup-$$(date +%Y%m%d-%H%M%S).db && echo "backed up"

restore:
	@test -n "$(FROM)" || (echo "usage: make restore FROM=backup-XXXX.db" && exit 1)
	@cp "$(FROM)" $${DB_PATH:-data/trading.db} && echo "restored from $(FROM)"

up:
	docker compose run --rm app
```

- [ ] **Step 5: Verify the test suite still passes and compose config is valid**

Run: `uv run pytest -q`
Expected: all tests pass.

Run: `docker compose config >/dev/null && echo "compose ok"`
Expected: prints `compose ok` (validates the YAML; does not build).

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example Makefile
git commit -m "feat: Docker (ARM-friendly), compose, .env.example, Makefile"
```

---

## Task 4: Final README and full suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all tests pass, exit code 0.

- [ ] **Step 2: Rewrite `README.md` to reflect a complete system**

```markdown
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

## Deploy (Raspberry Pi or VPS)

1. `cp .env.example .env` and fill in secrets.
2. `docker compose build && docker compose run --rm app` to verify.
3. Schedule a host cron pre-market, e.g. weekdays 13:00 UTC:
   `0 13 * * 1-5  cd /path/to/mm && docker compose run --rm app >> run.log 2>&1`
4. `make backup` before moving hosts — the SQLite DB is your whole track record.

IB Gateway has no ARM build: on a Pi, run it on a separate x86 host (or use the Client
Portal Web API) and set `IBKR_HOST`/`IBKR_PORT`. Until then, `BROKER=fake` runs the entire
system end-to-end.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: complete README — all 10 plans done"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §4 daily cycle, §8 deploy):**
- Full daily cycle per agent, with freeze-skip, reconcile, panel, Telegram confirmation,
  watchdog, and digest → `run_daily`, integration-tested on fakes. ✓
- Real components assembled from env, with a free `BROKER=fake` dry-run → `build_components`,
  `python -m trading.run`. ✓
- Deployment: ARM-friendly Docker, compose with a DB volume, `.env` secrets, cron, portable
  backup → `Dockerfile`/`docker-compose.yml`/`.env.example`/`Makefile`. ✓
- IB Gateway / ARM caveat surfaced, broker pluggable via `BROKER` + `IBKR_*` env → §8. ✓

**Known limitation, documented (not a silent gap):**
- One real IBKR account split across three virtual sub-accounts is NOT solved: each agent
  gets its own `Broker` (its own account/client_id). For real trading, fund a separate
  account per agent, or run a single agent. Reconciling three virtual sub-accounts against
  one combined IBKR account is future work. `run.py` and the spec both say so.
- `flatten` closes positions but does not cancel resting stop orders — add `cancel_all` to
  the `Broker` Protocol + `IBKRBroker` when finalizing the live IBKR connection (the
  deferred §8 choice). Closing positions is the core protection.

**Glue honesty:** `build_components`/`main` make real network connections and are not unit-
tested — verified by the `BROKER=fake STRATEGY=fake NOTIFIER=fake` smoke run. The daily
logic that matters (`run_daily`) is fully integration-tested on fakes, including the freeze,
reconcile, watchdog-flatten, and digest paths.

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `run_daily(profiles, brokers, source, strategy, panel, notifier,
accounts, journal, freezes, universe, as_of_date, ts, floor_fraction)` consumes the verified
plan-1..9 interfaces; `build_components()` returns exactly the kwargs `run_daily` expects
(minus `as_of_date`/`ts`, supplied by `main`). ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-daily-run-deploy.md`.

**This is the final plan.** After it: `make sim` proves the scheme; `BROKER=fake
STRATEGY=claude NOTIFIER=telegram` runs the real daily experience on your phone with no
money; and when you pick an IBKR connection (deferred §8 decision), `BROKER=ibkr` against a
paper account begins the 6-month forward track record that gates go-live.
```
