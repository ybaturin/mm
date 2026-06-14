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


class _StaticStrategy:
    """Returns a fixed proposal list, ignoring the briefing — for deterministic wiring tests."""

    def __init__(self, proposals):
        self._proposals = proposals

    def propose(self, briefing, profile):
        return self._proposals


def flat_bars(n=60, price=100.0):
    return [Bar(f"2026-05-{i+1:02d}", price, price, price, price, 1000) for i in range(n)]


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
        confirm=lambda p, d: True,
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


def test_run_cycle_does_not_auto_approve_large_trades_by_default(repos):
    accounts, journal = repos
    profile = make_profile()
    broker = FakeBroker(cash=profile.budget)
    source = FakeMarketDataSource({"AAPL": uptrend_bars()})
    broker.set_price("AAPL", source.latest_price("AAPL"))

    # No confirm passed: a NEEDS_CONFIRMATION trade must fail safe (not execute),
    # never silently auto-approve (review finding #16).
    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    assert broker.positions() == []                  # not executed without explicit approval
    assert journal.decisions_for("moderate")         # but the decision was still recorded


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
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              confirm=lambda p, d: True)

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


def test_run_cycle_passes_news_into_briefing(tmp_path):
    """A captured-briefing strategy proves news reaches the prompt-facing briefing."""
    from trading.data.news import FakeNews, Headline

    captured = {}

    class CapturingStrategy:
        def propose(self, briefing, profile):
            captured["briefing"] = briefing
            return []

    from trading.config import load_profiles

    bars = [Bar(f"2026-06-{i+1:02d}", 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1000)
            for i in range(60)]
    source = FakeMarketDataSource({"AAPL": bars})
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    accounts, journal = AccountRepository(conn), JournalRepository(conn)
    profile = load_profiles("config/profiles.toml")["moderate"]
    broker = FakeBroker(cash=profile.budget)
    broker.set_price("AAPL", 159.0)
    news = FakeNews({"AAPL": [Headline("AAPL", "News!", "Reuters", "2026-06-13")]})

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=CapturingStrategy(),
              universe=["AAPL"], as_of_date="2026-12-31", ts="2026-12-31T13:30:00Z",
              confirm=lambda p, d: True, news_source=news)

    assert captured["briefing"].news["AAPL"][0].title == "News!"
    assert captured["briefing"].memory is not None     # journal always wired now


def test_cycle_writes_thesis_on_open_and_emits_retro_on_close(repos):
    from trading.domain import Intent, TradeProposal
    from trading.persistence.theses import ThesisStore
    from trading.reporting.notifier import FakeNotifier

    accounts, journal = repos
    theses = ThesisStore(accounts.conn)
    profile = make_profile()
    broker = FakeBroker(cash=profile.budget)
    source = FakeMarketDataSource({"AAPL": flat_bars()})
    broker.set_price("AAPL", 100.0)

    # Day 1: open AAPL with a forecast.
    open_proposal = TradeProposal("moderate", "AAPL", Intent.OPEN_LONG, 5,
                                  reference_price=100.0, stop_loss_price=95.0,
                                  rationale="rebound", target_price=120.0, horizon_days=10)
    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=_StaticStrategy([open_proposal]),
              universe=["AAPL"], as_of_date="2026-06-14", ts="2026-06-14T13:00:00Z",
              confirm=lambda p, d: True, theses=theses)
    row = theses.get("moderate", "AAPL")
    assert row is not None and row["target_price"] == 120.0
    held_qty = {p.symbol: p for p in broker.positions()}["AAPL"].quantity

    # Day 2: fully close at a loss — retro pushed, thesis cleared.
    broker.set_price("AAPL", 97.6)
    notifier = FakeNotifier()
    close_proposal = TradeProposal("moderate", "AAPL", Intent.CLOSE_LONG, held_qty,
                                   reference_price=97.6, stop_loss_price=None,
                                   rationale="take profit")
    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=_StaticStrategy([close_proposal]),
              universe=["AAPL"], as_of_date="2026-06-16", ts="2026-06-16T13:00:00Z",
              confirm=lambda p, d: True, notifier=notifier, theses=theses)

    assert theses.get("moderate", "AAPL") is None
    assert any("Закрыта позиция" in m for m in notifier.messages)
