import pytest
from trading.analysis.memory import build_memory
from trading.domain import Intent, Outcome, Position, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db


@pytest.fixture
def journal(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


def _open_and_close(journal, symbol, entry, exit_price, rationale):
    prop = TradeProposal("moderate", symbol, Intent.OPEN_LONG, 10, entry, entry * 0.9, rationale)
    did = journal.record_decision("2026-06-01T13:30:00Z", prop,
                                  GuardrailDecision(Outcome.APPROVED_AUTO, 10, []))
    journal.record_fill("2026-06-01T13:30:00Z", "moderate", symbol, Intent.OPEN_LONG,
                        10, entry, did)
    journal.record_fill("2026-06-05T13:30:00Z", "moderate", symbol, Intent.CLOSE_LONG,
                        10, exit_price, None)


def test_build_memory_empty_on_cold_start(journal):
    mem = build_memory(journal, "moderate", positions=[], prices={})
    assert mem.open_positions == []
    assert mem.recent_closed == []
    assert mem.stats is None


def test_build_memory_reports_closed_trades_and_stats(journal):
    _open_and_close(journal, "AAPL", 100.0, 110.0, "winner")   # +100
    _open_and_close(journal, "MSFT", 200.0, 180.0, "loser")    # -200
    mem = build_memory(journal, "moderate", positions=[], prices={})
    assert mem.stats.closed_trades == 2
    assert mem.stats.win_rate == 0.5
    assert mem.stats.total_realized_pnl == -100.0
    assert {t.symbol for t in mem.recent_closed} == {"AAPL", "MSFT"}


def test_build_memory_open_position_carries_rationale_and_unrealized(journal):
    prop = TradeProposal("moderate", "NVDA", Intent.OPEN_LONG, 2, 800.0, 720.0, "breakout")
    did = journal.record_decision("2026-06-01T13:30:00Z", prop,
                                  GuardrailDecision(Outcome.APPROVED_AUTO, 2, []))
    journal.record_fill("2026-06-01T13:30:00Z", "moderate", "NVDA", Intent.OPEN_LONG,
                        2, 800.0, did)
    positions = [Position("NVDA", 2, 800.0)]
    mem = build_memory(journal, "moderate", positions, prices={"NVDA": 900.0})
    assert len(mem.open_positions) == 1
    op = mem.open_positions[0]
    assert op.symbol == "NVDA"
    assert op.rationale == "breakout"
    assert op.unrealized_pct == pytest.approx(0.125)   # (900-800)/800
