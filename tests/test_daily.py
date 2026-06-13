import math

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
                veto_rule="majority")
    base.update(o)
    return RiskProfile(**base)


def uptrend(n=60):
    # Rising series with enough wiggle that RSI stays below FakeStrategy's overbought
    # threshold -> a clean OPEN_LONG signal. A strictly monotonic rise pegs RSI at 100.
    closes = [round(100.0 + i + 8.0 * math.sin(i), 2) for i in range(n)]
    return [Bar(f"2026-04-{i+1:02d}", c, c, c, c, 1000) for i, c in enumerate(closes)]


class AllowingPanel:
    def review(self, proposal, briefing, veto_rule):
        return PanelResult(blocked=False, verdicts=[RoleVerdict("risk_skeptic", False, "")])


class NoopStrategy:
    def propose(self, briefing, profile):
        return []


class FlakyStrategy:
    """Raises for one specific agent, behaves normally for the rest."""
    def __init__(self):
        self._ok = FakeStrategy()

    def propose(self, briefing, profile):
        if briefing.agent_id == "boom":
            raise RuntimeError("claude exploded")
        return self._ok.propose(briefing, profile)


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


def test_run_daily_sends_per_trade_fill_and_pnl(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend()})
    brokers = fresh_brokers(profiles, source, universe)
    notifier = FakeNotifier(confirm_result=True)

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=FakeStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    assert any("Покупка" in m for m in notifier.messages)   # a per-trade fill notification
    assert any("P&L" in m for m in notifier.messages)       # a per-agent P&L line


def test_run_daily_sends_aggregate_pnl_total(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate"), "aggressive": profile("aggressive")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend()})
    brokers = fresh_brokers(profiles, source, universe)
    notifier = FakeNotifier(confirm_result=True)

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=FakeStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    assert any("ИТОГО" in m for m in notifier.messages)     # portfolio-wide P&L total


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


def test_run_daily_prices_held_symbol_outside_universe(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend(),
                                   "OLD": [Bar("2026-06-15", 100.0, 100.0, 100.0, 100.0, 1000)]})
    broker = FakeBroker(cash=5000.0)
    broker.set_price("AAPL", source.latest_price("AAPL"))
    broker.set_price("OLD", 100.0)
    broker.place_market_order("OLD", Action.BUY, 10)        # held, but dropped from universe
    brokers = {"moderate": broker}
    notifier = FakeNotifier(confirm_result=True)

    # The watchdog must not KeyError on a holding that is no longer in the universe.
    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=NoopStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    assert any("moderate" in m for m in notifier.messages)  # completed, digest sent
    assert {p.symbol for p in broker.positions()} == {"OLD"}


def test_run_daily_freezes_on_drawdown_without_flatten(env):
    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate")}        # max_drawdown_pct = 0.15
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": [Bar("2026-06-15", 81.0, 81.0, 81.0, 81.0, 1000)]})
    broker = FakeBroker(cash=5000.0)
    broker.set_price("AAPL", 100.0)
    broker.place_market_order("AAPL", Action.BUY, 40)       # 40 @ 100 -> cash 1000
    broker.set_price("AAPL", 81.0)
    brokers = {"moderate": broker}
    from trading.domain import AgentState, Position
    accounts.save_state(AgentState("moderate", cash=1000.0,
                                   positions=[Position("AAPL", 40, 100.0)],
                                   peak_equity=5000.0, equity_day_start=5000.0))
    notifier = FakeNotifier()

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=NoopStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    # NAV = 1000 + 40*81 = 4240. Drawdown vs peak 5000 = 15.2% > 15% -> suspend.
    # But 4240 > watchdog floor 0.8*5000 = 4000 -> do NOT flatten.
    assert freezes.is_frozen("moderate") is True
    assert any("drawdown" in m.lower() for m in notifier.messages)
    assert {p.symbol for p in broker.positions()} == {"AAPL"}   # still held, not flattened


def test_run_daily_isolates_agent_failure_and_alerts(env):
    accounts, journal, freezes = env
    profiles = {"boom": profile("boom"), "moderate": profile("moderate")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend()})
    brokers = fresh_brokers(profiles, source, universe)
    notifier = FakeNotifier(confirm_result=True)

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=FlakyStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    # The failing agent is frozen + alerted; the healthy agent still trades and reports.
    assert freezes.is_frozen("boom") is True
    assert any("boom" in m and "error" in m.lower() for m in notifier.messages)
    assert brokers["moderate"].positions()
    assert any("moderate" in m for m in notifier.messages)


def test_run_daily_global_fuse_halts_everyone_on_portfolio_breach(env):
    accounts, journal, freezes = env
    profiles = {"healthy": profile("healthy"), "loser": profile("loser")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": [Bar("2026-06-15", 40.0, 40.0, 40.0, 40.0, 1000)]})

    healthy = FakeBroker(cash=4000.0)                   # NAV 4000, above its own floor
    healthy.set_price("AAPL", 40.0)
    loser = FakeBroker(cash=5000.0)
    loser.set_price("AAPL", 100.0)
    loser.place_market_order("AAPL", Action.BUY, 20)    # cash 3000, 20 sh
    loser.set_price("AAPL", 40.0)                        # NAV 3800
    brokers = {"healthy": healthy, "loser": loser}
    notifier = FakeNotifier()

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=NoopStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    # combined NAV 4000 + 3800 = 7800 < 0.8 * 10000 = 8000 -> GLOBAL halt for everyone
    assert freezes.is_frozen(GLOBAL) is True
    assert any("global" in m.lower() for m in notifier.messages)


def test_run_daily_acquires_and_releases_run_lock(env):
    """run_daily holds the lock for the run and releases it in finally."""
    from trading.persistence.runlock import RunLock

    accounts, journal, freezes = env
    profiles = {"moderate": profile("moderate")}
    universe = ["AAPL"]
    source = FakeMarketDataSource({"AAPL": uptrend()})
    brokers = fresh_brokers(profiles, source, universe)
    notifier = FakeNotifier(confirm_result=True)

    seen = {}

    class _Spy(RunLock):
        def acquire(self, *a, **k):
            k.setdefault("now_iso", "2026-06-15T13:30:00Z")
            super().acquire(*a, **k)
            seen["active_during"] = self.is_active(now_iso="2026-06-15T13:30:10Z")

    spy = _Spy(accounts.conn)

    run_daily(profiles=profiles, brokers=brokers, source=source, strategy=FakeStrategy(),
              panel=AllowingPanel(), notifier=notifier, accounts=accounts, journal=journal,
              freezes=freezes, universe=universe, as_of_date="2026-06-15",
              ts="2026-06-15T13:30:00Z", run_lock=spy)

    assert seen["active_during"] is True
    assert RunLock(accounts.conn).is_active(now_iso="2026-06-15T13:30:10Z") is False


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
