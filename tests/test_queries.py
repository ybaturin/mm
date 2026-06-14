import pytest
from trading.domain import AgentState, Intent, Position
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.freezes import FreezeStore
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.reporting.queries import (
    days_left, path_to_target, pnl_report, positions_report, status_report,
    trades_report,
)


@pytest.fixture
def journal(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


@pytest.fixture
def accounts(tmp_path):
    conn = connect(str(tmp_path / "acc.db"))
    init_db(conn)
    return AccountRepository(conn)


# --- pnl_report ---

def test_pnl_week_uses_snapshot_on_or_before_cutoff(journal):
    # momentum: 8 days before the end equity=10000, at the end 10800.
    journal.record_equity_snapshot("momentum", "2026-06-05", 10000.0)
    journal.record_equity_snapshot("momentum", "2026-06-13", 10800.0)
    rep = pnl_report(journal, ["momentum"], "week")
    line = rep.per_agent[0]
    assert line.agent_id == "momentum"
    assert line.start_equity == 10000.0
    assert line.end_equity == 10800.0
    assert line.pnl == pytest.approx(800.0)
    assert line.pct == pytest.approx(0.08)
    assert rep.portfolio_pnl == pytest.approx(800.0)


def test_pnl_all_uses_first_snapshot(journal):
    journal.record_equity_snapshot("v", "2026-06-01", 5000.0)
    journal.record_equity_snapshot("v", "2026-06-10", 5100.0)
    journal.record_equity_snapshot("v", "2026-06-13", 5300.0)
    rep = pnl_report(journal, ["v"], "all")
    assert rep.per_agent[0].start_equity == 5000.0
    assert rep.per_agent[0].end_equity == 5300.0


def test_pnl_portfolio_sums_agents(journal):
    journal.record_equity_snapshot("a", "2026-06-05", 10000.0)
    journal.record_equity_snapshot("a", "2026-06-13", 11000.0)
    journal.record_equity_snapshot("b", "2026-06-05", 20000.0)
    journal.record_equity_snapshot("b", "2026-06-13", 19000.0)
    rep = pnl_report(journal, ["a", "b"], "week")
    assert rep.portfolio_start == pytest.approx(30000.0)
    assert rep.portfolio_end == pytest.approx(30000.0)
    assert rep.portfolio_pnl == pytest.approx(0.0)


def test_pnl_skips_agents_without_snapshots(journal):
    journal.record_equity_snapshot("a", "2026-06-13", 10000.0)
    rep = pnl_report(journal, ["a", "ghost"], "week")
    assert [l.agent_id for l in rep.per_agent] == ["a"]


# --- positions_report ---

def test_positions_report_values_long_and_short(accounts):
    accounts.save_state(AgentState(
        "momentum", cash=1000.0,
        positions=[Position("AAPL", 10, 200.0), Position("TSLA", -5, 250.0)]))
    prices = {"AAPL": 210.0, "TSLA": 240.0}
    rep = positions_report(accounts, ["momentum"], lambda s: prices[s])
    lines = rep.per_agent["momentum"]
    aapl = next(l for l in lines if l.symbol == "AAPL")
    tsla = next(l for l in lines if l.symbol == "TSLA")
    assert aapl.unrealized_pnl == pytest.approx(100.0)    # (210-200)*10
    assert tsla.unrealized_pnl == pytest.approx(50.0)     # (240-250)*-5
    assert rep.portfolio_unrealized == pytest.approx(150.0)


def test_positions_report_includes_cash(accounts):
    accounts.save_state(AgentState(
        "m", cash=1500.0, positions=[Position("AAPL", 10, 200.0)]))
    rep = positions_report(accounts, ["m"], lambda s: 210.0)
    assert rep.portfolio_cash == 1500.0
    assert rep.per_agent_cash["m"] == 1500.0
    assert rep.portfolio_market_value == 2100.0    # 10 * 210


def test_positions_report_empty_agent(accounts):
    accounts.save_state(AgentState("flat", cash=5000.0, positions=[]))
    rep = positions_report(accounts, ["flat"], lambda s: 1.0)
    assert rep.per_agent["flat"] == []
    assert rep.portfolio_unrealized == 0.0


# --- status_report / trades_report ---

def test_status_report_aggregates(tmp_path):
    conn = connect(str(tmp_path / "s.db"))
    init_db(conn)
    acc = AccountRepository(conn)
    jr = JournalRepository(conn)
    fr = FreezeStore(conn)
    acc.save_state(AgentState("a", cash=1000.0, positions=[Position("AAPL", 10, 100.0)]))
    jr.record_equity_snapshot("a", "2026-06-12", 1900.0)
    jr.record_equity_snapshot("a", "2026-06-13", 2000.0)   # +100 today
    fr.freeze("a", "manual hold", "2026-06-13T13:00:00Z")
    rep = status_report(acc, jr, fr, ["a"], lambda s: 100.0)
    assert rep.portfolio_equity == pytest.approx(2000.0)   # 1000 cash + 10*100
    assert rep.today_pnl == pytest.approx(100.0)
    assert rep.open_positions_count == 1
    assert rep.freezes == [("a", "manual hold")]


def test_trades_report_sorts_desc_and_limits(tmp_path):
    conn = connect(str(tmp_path / "tr.db"))
    init_db(conn)
    jr = JournalRepository(conn)
    jr.record_fill("2026-06-11T13:30:00Z", "a", "AAPL", Intent.OPEN_LONG, 5, 100.0, None)
    jr.record_fill("2026-06-13T13:30:00Z", "b", "TSLA", Intent.OPEN_SHORT, 3, 250.0, None)
    jr.record_fill("2026-06-12T13:30:00Z", "a", "MSFT", Intent.OPEN_LONG, 2, 400.0, None)
    rep = trades_report(jr, ["a", "b"], limit=2)
    assert [r.symbol for r in rep.rows] == ["TSLA", "MSFT"]   # most recent first


# --- forecast progress helpers + benchmark ---

def test_path_to_target_long():
    # entry 100, target 120, current 110 -> halfway.
    assert path_to_target(100.0, 110.0, 120.0) == 0.5


def test_path_to_target_short():
    # short: entry 200, target 180, current 190 -> halfway.
    assert path_to_target(200.0, 190.0, 180.0) == 0.5


def test_path_to_target_handles_degenerate_target():
    assert path_to_target(100.0, 110.0, 100.0) == 0.0


def test_days_left_counts_down():
    assert days_left("2026-06-14", horizon_days=14, today="2026-06-19") == 9


def test_pnl_report_includes_benchmark_when_fn_given(journal):
    journal.record_equity_snapshot("aggressive", "2026-06-07", 10000.0)
    journal.record_equity_snapshot("aggressive", "2026-06-14", 10800.0)
    rep = pnl_report(journal, ["aggressive"], "week",
                     benchmark_fn=lambda start, end: 0.02)
    assert abs(rep.benchmark_pct - 0.02) < 1e-9


def test_positions_report_enriches_with_thesis(tmp_path):
    from trading.persistence.theses import ThesisStore

    conn = connect(str(tmp_path / "p.db"))
    init_db(conn)
    acc = AccountRepository(conn)
    acc.save_state(AgentState("aggressive", cash=0.0,
                              positions=[Position("IWM", 3, 292.95)]))
    theses = ThesisStore(conn)
    theses.upsert("aggressive", "IWM", entry_price=292.95, target_price=315.0,
                  horizon_days=10, opened_on="2026-06-14", rationale="x")
    rep = positions_report(acc, ["aggressive"], lambda s: 298.10,
                           theses=theses, today="2026-06-16")
    line = rep.per_agent["aggressive"][0]
    assert line.target_price == 315.0
    assert 0.0 < line.path_pct < 1.0
    assert line.days_left == 8
