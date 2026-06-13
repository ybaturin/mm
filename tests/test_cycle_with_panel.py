import math

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
from trading.validation.panel import PanelResult, RoleVerdict


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


class BlockingPanel:
    def review(self, proposal, briefing, veto_rule):
        return PanelResult(blocked=True, verdicts=[RoleVerdict("risk_skeptic", True, "no")])


class AllowingPanel:
    def review(self, proposal, briefing, veto_rule):
        return PanelResult(blocked=False, verdicts=[RoleVerdict("risk_skeptic", False, "")])


@pytest.fixture
def repos(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return AccountRepository(conn), JournalRepository(conn)


def setup(profile):
    broker = FakeBroker(cash=profile.budget)
    source = FakeMarketDataSource({"AAPL": uptrend_bars()})
    broker.set_price("AAPL", source.latest_price("AAPL"))
    return broker, source


def test_blocking_panel_prevents_execution_and_records_veto(repos):
    accounts, journal = repos
    profile = make_profile()
    broker, source = setup(profile)

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              confirm=lambda p, d: True, panel=BlockingPanel())

    assert broker.positions() == []                 # nothing executed
    assert journal.vetoes_for("moderate")           # veto recorded
    assert journal.fills_for("moderate") == []


def test_allowing_panel_lets_execution_through(repos):
    accounts, journal = repos
    profile = make_profile()
    broker, source = setup(profile)

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              confirm=lambda p, d: True, panel=AllowingPanel())

    assert broker.positions()                        # executed
    assert journal.vetoes_for("moderate") == []


def test_no_panel_executes_when_confirmed(repos):
    accounts, journal = repos
    profile = make_profile()
    broker, source = setup(profile)

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              confirm=lambda p, d: True)

    assert broker.positions()                        # panel optional; executes once approved
