# Orchestrator & Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire all existing components into one daily cycle and prove the whole scheme works end-to-end with a multi-day simulation you can run on demand — no live money, no API key, no IBKR.

**Architecture:** `run_cycle()` is the keystone: for one agent it builds a briefing, asks a `Strategy` for proposals, runs each through the Guardrails Engine, executes approved ones on a `Broker`, and records everything to the ledger + journal. `Strategy` is a Protocol with two implementations — `AgentCore` (real Claude, plan 5) and a deterministic `FakeStrategy` (a simple momentum rule) used for simulation. `run_simulation()` drives `run_cycle` across N synthetic trading days for all three agents, each on its own `FakeBroker`. Everything is injected, so the integration tests run the full pipeline in-memory. Intent→Action mapping (deferred from plan 3) lives here.

**Tech Stack:** Python 3.12+, stdlib, `pytest`. No new dependencies.

This is plan **6 of 10**. Depends on plans 1 (`GuardrailsEngine`, `Outcome`, `AgentState`, `Intent`), 2 (`AccountRepository`, `JournalRepository`), 3 (`FakeBroker`, `Action`), 4 (`build_briefing`, `FakeMarketDataSource`, `Bar`), 5 (`AgentCore` as a `Strategy`). Spec: `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md`.

---

## Existing interfaces this plan consumes (verified)

```python
# plan 1
class Intent(str, Enum): OPEN_LONG/CLOSE_LONG/OPEN_SHORT/CLOSE_SHORT
class Outcome(str, Enum): APPROVED_AUTO/NEEDS_CONFIRMATION/REJECTED
@dataclass class AgentState: agent_id; cash; positions; peak_equity; equity_day_start
    def equity(self, prices: dict[str, float]) -> float
class GuardrailsEngine:
    def evaluate(self, proposal, state, profile, prices, trades_today) -> GuardrailDecision
@dataclass(frozen=True) class GuardrailDecision: outcome; quantity; reasons
@dataclass(frozen=True) class TradeProposal: agent_id; symbol; intent; quantity; reference_price; stop_loss_price; rationale

# plan 2
class AccountRepository: get_state(agent_id)->AgentState|None; save_state(state)
class JournalRepository:
    record_decision(ts, proposal, decision)->int
    record_fill(ts, agent_id, symbol, intent: Intent, quantity, price, decision_id)->int
    record_equity_snapshot(agent_id, date, equity); equity_curve(agent_id)->list[tuple]

# plan 3
class Action(str, Enum): BUY="BUY"; SELL="SELL"
class FakeBroker:
    __init__(cash); connect(); cash()->float; positions()->list[Position]
    set_price(symbol, price); place_market_order(symbol, action, quantity)->Fill

# plan 4
@dataclass(frozen=True) class Bar: date; open; high; low; close; volume
class FakeMarketDataSource: __init__(data: dict[str,list[Bar]]); history(symbol,days); latest_price(symbol)
def build_briefing(state, universe, source, as_of_date, lookback_days=60) -> Briefing

# plan 5
class AgentCore: def propose(self, briefing, profile) -> list[TradeProposal]
```

## File Structure

```
src/trading/orchestrator/__init__.py
src/trading/orchestrator/actions.py     # action_for(intent) -> Action (pure)
src/trading/orchestrator/strategy.py    # Strategy Protocol + FakeStrategy (deterministic)
src/trading/orchestrator/cycle.py       # run_cycle() — one agent, one day
src/trading/orchestrator/simulate.py    # run_simulation() + synthetic series + __main__ CLI
tests/test_actions.py
tests/test_strategy.py
tests/test_cycle.py
tests/test_simulate.py
```

---

## Task 1: Intent → Action mapping

**Files:**
- Create: `src/trading/orchestrator/__init__.py` (empty)
- Create: `src/trading/orchestrator/actions.py`
- Test: `tests/test_actions.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_actions.py
from trading.broker.types import Action
from trading.domain import Intent
from trading.orchestrator.actions import action_for


def test_open_long_and_close_short_are_buys():
    assert action_for(Intent.OPEN_LONG) is Action.BUY
    assert action_for(Intent.CLOSE_SHORT) is Action.BUY


def test_close_long_and_open_short_are_sells():
    assert action_for(Intent.CLOSE_LONG) is Action.SELL
    assert action_for(Intent.OPEN_SHORT) is Action.SELL
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.orchestrator.actions'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/orchestrator/__init__.py
```

```python
# src/trading/orchestrator/actions.py
from __future__ import annotations

from trading.broker.types import Action
from trading.domain import Intent

_BUYS = {Intent.OPEN_LONG, Intent.CLOSE_SHORT}


def action_for(intent: Intent) -> Action:
    """Map a trade Intent to the broker order side.

    Opening a long or covering a short buys; closing a long or opening a short sells.
    """
    return Action.BUY if intent in _BUYS else Action.SELL
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_actions.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/orchestrator/__init__.py src/trading/orchestrator/actions.py tests/test_actions.py
git commit -m "feat: intent to broker-action mapping"
```

---

## Task 2: Strategy Protocol and deterministic FakeStrategy

**Files:**
- Create: `src/trading/orchestrator/strategy.py`
- Test: `tests/test_strategy.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_strategy.py
from trading.config import RiskProfile
from trading.data.briefing import Briefing, SymbolBrief
from trading.domain import Intent
from trading.orchestrator.strategy import FakeStrategy


def make_profile(**o):
    base = dict(name="moderate", budget=5000.0, max_position_pct=0.25, min_positions=5,
                allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
                daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
                auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority")
    base.update(o)
    return RiskProfile(**base)


def brief(symbol, price, sma20, held_qty=0, rsi=50.0):
    return SymbolBrief(symbol, price, sma20, sma20, rsi, 0.0,
                       held_qty, 100.0 if held_qty else None)


def briefing(symbols):
    return Briefing("moderate", "2026-06-15", 5000.0, 5000.0, symbols)


def test_opens_long_on_uptrend_when_not_held():
    # price above sma20, not held, rsi not overbought -> open_long
    b = briefing([brief("AAPL", price=160.0, sma20=150.0, held_qty=0, rsi=55.0)])
    trades = FakeStrategy().propose(b, make_profile())
    assert len(trades) == 1
    t = trades[0]
    assert t.symbol == "AAPL" and t.intent is Intent.OPEN_LONG
    assert t.quantity > 0
    assert t.reference_price == 160.0
    assert t.stop_loss_price is not None and t.stop_loss_price < 160.0


def test_skips_overbought():
    b = briefing([brief("AAPL", price=160.0, sma20=150.0, held_qty=0, rsi=80.0)])
    assert FakeStrategy().propose(b, make_profile()) == []


def test_closes_long_on_downtrend_when_held():
    b = briefing([brief("AAPL", price=140.0, sma20=150.0, held_qty=5, rsi=40.0)])
    trades = FakeStrategy().propose(b, make_profile())
    assert len(trades) == 1
    assert trades[0].intent is Intent.CLOSE_LONG
    assert trades[0].quantity == 5
    assert trades[0].stop_loss_price is None


def test_respects_max_trades_per_day():
    symbols = [brief(f"S{i}", price=160.0, sma20=150.0, held_qty=0, rsi=55.0) for i in range(10)]
    trades = FakeStrategy().propose(briefing(symbols), make_profile(max_trades_per_day=4))
    assert len(trades) == 4


def test_no_signal_when_flat():
    # price equals sma20 -> no trend signal -> nothing
    b = briefing([brief("AAPL", price=150.0, sma20=150.0, held_qty=0, rsi=55.0)])
    assert FakeStrategy().propose(b, make_profile()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.orchestrator.strategy'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/orchestrator/strategy.py
from __future__ import annotations

import math
from typing import Protocol

from trading.config import RiskProfile
from trading.data.briefing import Briefing
from trading.domain import Intent, TradeProposal


class Strategy(Protocol):
    """Produces trade proposals from a briefing. AgentCore (Claude) and FakeStrategy both satisfy it."""
    def propose(self, briefing: Briefing, profile: RiskProfile) -> list[TradeProposal]: ...


class FakeStrategy:
    """Deterministic momentum rule for simulation and integration tests — no LLM.

    Long-only. Buys an un-held symbol trading above its SMA20 (and not overbought);
    closes a held long that has fallen below its SMA20. Exercises the full pipeline
    (sizing, guardrails, execution, ledger) reproducibly and for free.
    """

    OVERBOUGHT = 70.0

    def propose(self, briefing: Briefing, profile: RiskProfile) -> list[TradeProposal]:
        proposals: list[TradeProposal] = []
        for s in briefing.symbols:
            if s.sma20 is None:
                continue

            if s.held_quantity == 0 and s.price > s.sma20:
                if s.rsi14 is not None and s.rsi14 >= self.OVERBOUGHT:
                    continue
                max_notional = profile.max_position_pct * profile.budget
                quantity = math.floor(max_notional / s.price)
                if quantity <= 0:
                    continue
                proposals.append(TradeProposal(
                    agent_id=briefing.agent_id, symbol=s.symbol, intent=Intent.OPEN_LONG,
                    quantity=quantity, reference_price=s.price,
                    stop_loss_price=round(s.price * (1 - profile.stop_loss_pct), 2),
                    rationale=f"price {s.price} above sma20 {s.sma20}",
                ))
            elif s.held_quantity > 0 and s.price < s.sma20:
                proposals.append(TradeProposal(
                    agent_id=briefing.agent_id, symbol=s.symbol, intent=Intent.CLOSE_LONG,
                    quantity=s.held_quantity, reference_price=s.price,
                    stop_loss_price=None, rationale=f"price {s.price} below sma20 {s.sma20}",
                ))

            if len(proposals) >= profile.max_trades_per_day:
                break
        return proposals[: profile.max_trades_per_day]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_strategy.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/orchestrator/strategy.py tests/test_strategy.py
git commit -m "feat: Strategy protocol and deterministic FakeStrategy"
```

---

## Task 3: run_cycle — one agent, one day

**Files:**
- Create: `src/trading/orchestrator/cycle.py`
- Test: `tests/test_cycle.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cycle.py
import pytest
from trading.broker.fake import FakeBroker
from trading.config import RiskProfile
from trading.data.bars import Bar
from trading.data.fake_source import FakeMarketDataSource
from trading.orchestrator.cycle import run_cycle
from trading.orchestrator.strategy import FakeStrategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db


def make_profile(**o):
    base = dict(name="moderate", budget=5000.0, max_position_pct=0.25, min_positions=5,
                allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
                daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
                auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority")
    base.update(o)
    return RiskProfile(**base)


def uptrend_bars(n=60):
    # strictly rising series -> price ends above its SMA20 -> FakeStrategy opens a long
    return [Bar(f"2026-04-{i+1:02d}", 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1000)
            for i in range(n)]


@pytest.fixture
def repos(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return AccountRepository(conn), JournalRepository(conn)


def test_run_cycle_opens_position_and_records_everything(repos):
    accounts, journal = repos
    profile = make_profile()
    broker = FakeBroker(cash=profile.budget)
    source = FakeMarketDataSource({"AAPL": uptrend_bars()})
    last_price = source.latest_price("AAPL")
    broker.set_price("AAPL", last_price)

    state = run_cycle(
        agent_id="moderate", profile=profile, broker=broker, source=source,
        accounts=accounts, journal=journal, strategy=FakeStrategy(),
        universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
    )

    # a long position was opened and persisted
    held = {p.symbol: p for p in broker.positions()}
    assert "AAPL" in held and held["AAPL"].quantity > 0
    assert state.cash < profile.budget                       # cash spent on the buy

    # ledger + journal recorded it
    assert journal.decisions_for("moderate")                 # at least one decision
    assert journal.fills_for("moderate")                     # at least one fill
    assert journal.equity_curve("moderate") == [("2026-06-15", pytest.approx(state.equity({"AAPL": last_price})))]
    # account state saved
    assert accounts.get_state("moderate").cash == state.cash


def test_run_cycle_respects_position_cap(repos):
    accounts, journal = repos
    profile = make_profile(max_position_pct=0.25, budget=5000.0)
    broker = FakeBroker(cash=profile.budget)
    source = FakeMarketDataSource({"AAPL": uptrend_bars()})
    broker.set_price("AAPL", source.latest_price("AAPL"))

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    pos = {p.symbol: p for p in broker.positions()}["AAPL"]
    notional = pos.quantity * source.latest_price("AAPL")
    assert notional <= 0.25 * profile.budget + 1e-6          # never exceeds the cap


def test_run_cycle_skips_confirmation_when_declined(repos):
    accounts, journal = repos
    profile = make_profile()
    broker = FakeBroker(cash=profile.budget)
    source = FakeMarketDataSource({"AAPL": uptrend_bars()})
    broker.set_price("AAPL", source.latest_price("AAPL"))

    # decline every confirmation -> large trades are not executed
    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              confirm=lambda proposal, decision: False)

    # the ~$1200 buy needed confirmation, which we declined -> no position
    assert broker.positions() == []
    # but the decision was still journaled
    assert journal.decisions_for("moderate")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cycle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.orchestrator.cycle'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/orchestrator/cycle.py
from __future__ import annotations

from typing import Callable

from trading.broker.base import Broker
from trading.config import RiskProfile
from trading.data.bars import MarketDataSource
from trading.data.briefing import build_briefing
from trading.domain import AgentState, GuardrailDecision, Outcome, TradeProposal
from trading.guardrails.engine import GuardrailsEngine
from trading.orchestrator.actions import action_for
from trading.orchestrator.strategy import Strategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.journal import JournalRepository

ConfirmFn = Callable[[TradeProposal, GuardrailDecision], bool]


def _state_from_broker(agent_id: str, broker: Broker, peak_equity: float,
                       equity_day_start: float) -> AgentState:
    return AgentState(
        agent_id=agent_id, cash=broker.cash(), positions=broker.positions(),
        peak_equity=peak_equity, equity_day_start=equity_day_start,
    )


def run_cycle(
    agent_id: str,
    profile: RiskProfile,
    broker: Broker,
    source: MarketDataSource,
    accounts: AccountRepository,
    journal: JournalRepository,
    strategy: Strategy,
    universe: list[str],
    as_of_date: str,
    ts: str,
    confirm: ConfirmFn | None = None,
) -> AgentState:
    """Run one agent's full daily cycle. The keystone that connects every component.

    briefing -> strategy proposes -> guardrails evaluate -> execute approved -> record.
    `confirm` decides NEEDS_CONFIRMATION trades (defaults to auto-approve, as in simulation).
    """
    if confirm is None:
        confirm = lambda proposal, decision: True  # noqa: E731
    engine = GuardrailsEngine()

    held = [p.symbol for p in broker.positions()]
    symbols = sorted(set(universe) | set(held))
    prices = {s: source.latest_price(s) for s in symbols}

    def equity_now() -> float:
        return broker.cash() + sum(p.quantity * prices[p.symbol] for p in broker.positions())

    prev = accounts.get_state(agent_id)
    start_equity = equity_now()
    peak = max(prev.peak_equity, start_equity) if prev else start_equity

    state = _state_from_broker(agent_id, broker, peak, start_equity)
    proposals = strategy.propose(
        build_briefing(state, universe, source, as_of_date), profile)

    trades_today = 0
    for proposal in proposals:
        decision = engine.evaluate(proposal, state, profile, prices, trades_today)
        decision_id = journal.record_decision(ts, proposal, decision)

        if decision.outcome is Outcome.REJECTED:
            continue
        if decision.outcome is Outcome.NEEDS_CONFIRMATION and not confirm(proposal, decision):
            continue

        fill = broker.place_market_order(
            proposal.symbol, action_for(proposal.intent), decision.quantity)
        journal.record_fill(ts, agent_id, proposal.symbol, proposal.intent,
                            fill.quantity, fill.price, decision_id)
        trades_today += 1
        state = _state_from_broker(agent_id, broker, peak, start_equity)

    final_equity = equity_now()
    peak = max(peak, final_equity)
    final_state = _state_from_broker(agent_id, broker, peak, start_equity)
    accounts.save_state(final_state)
    journal.record_equity_snapshot(agent_id, as_of_date, final_equity)
    return final_state
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cycle.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/orchestrator/cycle.py tests/test_cycle.py
git commit -m "feat: run_cycle wires the full daily pipeline for one agent"
```

---

## Task 4: Multi-day simulation

**Files:**
- Create: `src/trading/orchestrator/simulate.py`
- Test: `tests/test_simulate.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_simulate.py
import pytest
from trading.config import RiskProfile
from trading.data.bars import Bar
from trading.orchestrator.simulate import run_simulation, synthetic_series
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db


def make_profiles():
    def p(name, **o):
        base = dict(name=name, budget=5000.0, max_position_pct=0.25, min_positions=5,
                    allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
                    daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
                    auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority")
        base.update(o)
        return RiskProfile(**base)
    return {"conservative": p("conservative", max_position_pct=0.15),
            "moderate": p("moderate"),
            "aggressive": p("aggressive", max_position_pct=0.40)}


@pytest.fixture
def repos(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return AccountRepository(conn), JournalRepository(conn)


def test_synthetic_series_has_enough_bars():
    series = synthetic_series(["AAPL", "MSFT"], total_bars=90)
    assert set(series) == {"AAPL", "MSFT"}
    assert len(series["AAPL"]) == 90
    assert all(isinstance(b, Bar) for b in series["AAPL"])


def test_simulation_records_equity_for_every_day_and_agent(repos):
    accounts, journal = repos
    universe = ["AAPL", "MSFT"]
    series = synthetic_series(universe, total_bars=70)
    profiles = make_profiles()

    results = run_simulation(days=5, profiles=profiles, universe=universe,
                             series=series, accounts=accounts, journal=journal)

    for name in profiles:
        assert len(results[name]) == 5                       # one equity point per day
        assert len(journal.equity_curve(name)) == 5          # persisted too
        # every agent ends with a finite equity and a saved account
        assert accounts.get_state(name) is not None


def test_simulation_never_breaches_position_cap(repos):
    accounts, journal = repos
    universe = ["AAPL", "MSFT"]
    series = synthetic_series(universe, total_bars=70)
    profiles = make_profiles()

    run_simulation(days=5, profiles=profiles, universe=universe,
                   series=series, accounts=accounts, journal=journal)

    # final positions for each agent must respect its per-position cap
    final_prices = {s: series[s][-1].close for s in universe}
    for name, profile in profiles.items():
        state = accounts.get_state(name)
        for p in state.positions:
            notional = abs(p.quantity) * final_prices[p.symbol]
            assert notional <= profile.max_position_pct * profile.budget + 1.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_simulate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.orchestrator.simulate'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/orchestrator/simulate.py
from __future__ import annotations

import math

from trading.broker.fake import FakeBroker
from trading.config import RiskProfile, load_profiles
from trading.data.bars import Bar
from trading.data.briefing import load_universe
from trading.data.fake_source import FakeMarketDataSource
from trading.orchestrator.cycle import run_cycle
from trading.orchestrator.strategy import FakeStrategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db

LOOKBACK = 60


def synthetic_series(symbols: list[str], total_bars: int) -> dict[str, list[Bar]]:
    """Deterministic price paths: a drifting sinusoid per symbol. Reproducible (no RNG)."""
    series: dict[str, list[Bar]] = {}
    for idx, symbol in enumerate(symbols):
        base = 100.0 + 50.0 * idx
        bars = []
        for i in range(total_bars):
            # upward drift plus a slow wave so trends cross the SMA both ways
            price = base * (1.0 + 0.004 * i + 0.05 * math.sin((i + idx * 3) / 6.0))
            price = round(price, 2)
            bars.append(Bar(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                            price, price, price, price, 1_000_000))
        series[symbol] = bars
    return series


def run_simulation(
    days: int,
    profiles: dict[str, RiskProfile],
    universe: list[str],
    series: dict[str, list[Bar]],
    accounts: AccountRepository,
    journal: JournalRepository,
    start_index: int = LOOKBACK,
) -> dict[str, list[tuple[str, float]]]:
    """Run `days` trading days for every profile on its own FakeBroker.

    Returns {agent_id: [(date, equity), ...]}. Each day exposes only bars up to that day
    (point-in-time — no peeking ahead), and fills happen at that day's close.
    """
    brokers = {name: FakeBroker(cash=p.budget) for name, p in profiles.items()}
    results: dict[str, list[tuple[str, float]]] = {name: [] for name in profiles}

    for d in range(days):
        cutoff = start_index + d + 1
        source = FakeMarketDataSource({s: bars[:cutoff] for s, bars in series.items()})
        as_of = series[universe[0]][cutoff - 1].date
        prices = {s: source.latest_price(s) for s in universe}

        for name, profile in profiles.items():
            broker = brokers[name]
            for s in universe:
                broker.set_price(s, prices[s])
            state = run_cycle(
                agent_id=name, profile=profile, broker=broker, source=source,
                accounts=accounts, journal=journal, strategy=FakeStrategy(),
                universe=universe, as_of_date=as_of, ts=f"{as_of}T13:30:00Z",
            )
            results[name].append((as_of, round(state.equity(prices), 2)))

    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Simulate the trading scheme on synthetic data.")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    profiles = load_profiles("config/profiles.toml")
    universe = load_universe("config/universe.toml")
    series = synthetic_series(universe, total_bars=LOOKBACK + args.days + 1)

    conn = connect(":memory:")
    init_db(conn)
    accounts, journal = AccountRepository(conn), JournalRepository(conn)

    results = run_simulation(args.days, profiles, universe, series, accounts, journal)

    print(f"Simulated {args.days} trading days on {len(universe)} symbols (FakeStrategy).\n")
    for name, profile in profiles.items():
        curve = results[name]
        start, end = profile.budget, curve[-1][1]
        pnl = end - start
        print(f"{name:>13}: ${start:,.0f} -> ${end:,.2f}  "
              f"({pnl:+,.2f}, {pnl / start:+.1%})  trades={len(journal.fills_for(name))}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_simulate.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the simulation end-to-end (the scheme check)**

Run: `uv run python -m trading.simulate --days 30`
Expected: prints one line per agent with starting capital, ending equity, P&L, and trade
count — proving the full pipeline (data → briefing → strategy → guardrails → broker →
ledger → equity) runs end-to-end with no errors.

- [ ] **Step 6: Commit**

```bash
git add src/trading/orchestrator/simulate.py tests/test_simulate.py
git commit -m "feat: multi-day simulation runner and CLI"
```

---

## Task 5: README and full suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all tests pass (plans 1–6), exit code 0.

- [ ] **Step 2: Update the Status section of `README.md`**

Replace the `## Status` section with:

```markdown
## Status

- Plan 1 of 10: domain models, risk-profile config, deterministic Guardrails Engine. ✓
- Plan 2 of 10: SQLite persistence — ledger, decision journal, fills, equity snapshots. ✓
- Plan 3 of 10: Broker boundary — Protocol, FakeBroker, IBKRBroker (ib-async). ✓
- Plan 4 of 10: Data Collector — MarketDataSource (yfinance), indicators, briefing. ✓
- Plan 5 of 10: Agent Core — Claude turns a briefing into trade proposals. ✓
- Plan 6 of 10: Orchestrator + Simulation — run_cycle wires the whole pipeline; a
  multi-day simulation proves the scheme end-to-end with no live money. ✓

Run the whole scheme on synthetic data (deterministic, free, no API key, no IBKR):

    uv run python -m trading.simulate --days 30
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: mark orchestrator + simulation plan complete"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §4 the daily cycle, §11 forward simulation):**
- One daily cycle wiring every component → `run_cycle`. ✓
- Intent→Action mapping (deferred from plan 3) → `action_for`. ✓
- A deterministic strategy so the scheme can be exercised without the LLM →
  `FakeStrategy`; `AgentCore` is the real `Strategy` for live use. ✓
- A runnable multi-day simulation proving the scheme end-to-end → `run_simulation` + the
  `python -m trading.simulate` CLI. ✓
- Point-in-time data (no look-ahead) in the sim → each day slices `series[:cutoff]`,
  matching spec §11. ✓
- Confirmation flow modelled (auto-approve in sim; real confirm callback later) →
  `confirm` parameter on `run_cycle`. ✓

**Deferred to later plans (correctly out of scope here):**
- Validation Panel as an extra filter inside the cycle → plan 7; `run_cycle` will gain
  an optional panel step.
- Real Telegram confirmation callback (replacing the sim's auto-approve) → plan 8.
- Watchdog / reconciliation → plan 9.
- One real IBKR account split across three virtual sub-accounts: the simulation gives
  each agent its own `FakeBroker` (clean isolation). Reconciling three virtual
  sub-accounts against ONE real IBKR account is a production concern → plan 9/10.
- cron + docker + ARM build → plan 10.

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `action_for(intent) -> Action`, `Strategy.propose(briefing, profile)
-> list[TradeProposal]`, `run_cycle(agent_id, profile, broker, source, accounts, journal,
strategy, universe, as_of_date, ts, confirm)`, and `run_simulation(days, profiles,
universe, series, accounts, journal, start_index) -> dict` are used identically across
Tasks 1–4 and consume the verified plan-1..5 interfaces (`GuardrailsEngine.evaluate`,
`AccountRepository`/`JournalRepository`, `FakeBroker`, `build_briefing`/`FakeMarketDataSource`). ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-orchestrator-simulation.md`.
