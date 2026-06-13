import pytest
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.reporting.queries import pnl_report


@pytest.fixture
def journal(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


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
