import math

import pytest
from trading.broker.fake import FakeBroker
from trading.broker.types import Action
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
    # rising series (price ends well above its SMA20) with enough wiggle that RSI stays
    # below FakeStrategy's overbought threshold -> a clean OPEN_LONG signal.
    # A strictly monotonic rise would peg RSI at 100 and be skipped as overbought.
    closes = [round(100.0 + i + 8.0 * math.sin(i), 2) for i in range(n)]
    return [Bar(f"2026-04-{i+1:02d}", c, c, c, c, 1000) for i, c in enumerate(closes)]


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


def test_run_cycle_places_protective_stop_after_opening(repos):
    accounts, journal = repos
    profile = make_profile()
    broker = FakeBroker(cash=profile.budget)
    source = FakeMarketDataSource({"AAPL": uptrend_bars()})
    broker.set_price("AAPL", source.latest_price("AAPL"))

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              confirm=lambda p, d: True)

    pos = {p.symbol: p for p in broker.positions()}["AAPL"]
    assert len(broker.stop_orders) == 1               # the opening long got a hard stop
    stop = broker.stop_orders[0]
    assert stop["symbol"] == "AAPL"
    assert stop["action"] is Action.SELL              # protective stop on a long sells
    assert stop["quantity"] == pos.quantity           # protects the full filled size
    assert stop["stop_price"] > 0


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
