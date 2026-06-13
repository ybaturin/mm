import pytest
from trading.domain import Intent, Outcome, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db


@pytest.fixture
def repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


def proposal(symbol="AAPL", qty=10, intent=Intent.OPEN_LONG):
    return TradeProposal(
        agent_id="moderate", symbol=symbol, intent=intent, quantity=qty,
        reference_price=100.0, stop_loss_price=90.0, rationale="momentum",
    )


def test_record_decision_returns_id_and_persists(repo):
    decision = GuardrailDecision(outcome=Outcome.NEEDS_CONFIRMATION, quantity=8, reasons=[])
    did = repo.record_decision("2026-06-15T13:00:00Z", proposal(), decision)
    assert isinstance(did, int) and did > 0

    rows = repo.decisions_for("moderate")
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "AAPL"
    assert r["intent"] == "open_long"
    assert r["proposed_qty"] == 10
    assert r["final_qty"] == 8
    assert r["outcome"] == "needs_confirmation"


def test_record_decision_stores_reasons_as_json(repo):
    decision = GuardrailDecision(
        outcome=Outcome.REJECTED, quantity=0,
        reasons=["Insufficient cash for this buy", "Daily trade limit reached"],
    )
    repo.record_decision("2026-06-15T13:00:00Z", proposal(), decision)
    reasons = repo.reasons_for_latest("moderate")
    assert reasons == ["Insufficient cash for this buy", "Daily trade limit reached"]


def test_decisions_for_filters_by_agent_and_orders_by_time(repo):
    repo.record_decision("2026-06-15T13:00:00Z", proposal(symbol="AAPL"),
                         GuardrailDecision(Outcome.APPROVED_AUTO, 3, []))
    repo.record_decision("2026-06-16T13:00:00Z", proposal(symbol="MSFT"),
                         GuardrailDecision(Outcome.APPROVED_AUTO, 2, []))
    other = TradeProposal("aggressive", "NVDA", Intent.OPEN_LONG, 1, 900.0, 800.0, "x")
    repo.record_decision("2026-06-16T13:00:00Z", other,
                         GuardrailDecision(Outcome.APPROVED_AUTO, 1, []))

    rows = repo.decisions_for("moderate")
    assert [r["symbol"] for r in rows] == ["AAPL", "MSFT"]
