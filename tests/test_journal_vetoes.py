import pytest
from trading.domain import Intent, TradeProposal
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.validation.panel import RoleVerdict


@pytest.fixture
def repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


def test_vetoes_table_exists(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "vetoes" in names


def test_record_and_read_veto(repo):
    proposal = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
                             quantity=5, reference_price=160.0, stop_loss_price=145.0, rationale="x")
    verdicts = [
        RoleVerdict("risk_skeptic", True, "stop too wide"),
        RoleVerdict("catalyst_checker", True, "earnings tomorrow"),
        RoleVerdict("devils_advocate", False, ""),
    ]
    repo.record_veto("2026-06-15T13:00:00Z", "moderate", proposal, quantity=5, verdicts=verdicts)

    rows = repo.vetoes_for("moderate")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["quantity"] == 5
    assert "earnings tomorrow" in rows[0]["verdicts"]      # JSON text contains the reasons
