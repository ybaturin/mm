import pytest
from trading.domain import AgentState, Position
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.schema import init_db


@pytest.fixture
def repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return AccountRepository(conn)


def test_save_and_get_state_round_trip(repo):
    state = AgentState(
        agent_id="aggressive", cash=3000.0,
        positions=[
            Position(symbol="AAPL", quantity=10, avg_price=100.0),
            Position(symbol="TSLA", quantity=-5, avg_price=200.0),
        ],
        peak_equity=5000.0, equity_day_start=4800.0,
    )
    repo.save_state(state)

    loaded = repo.get_state("aggressive")
    assert loaded.agent_id == "aggressive"
    assert loaded.cash == 3000.0
    assert loaded.peak_equity == 5000.0
    assert loaded.equity_day_start == 4800.0
    by_symbol = {p.symbol: p for p in loaded.positions}
    assert by_symbol["AAPL"].quantity == 10
    assert by_symbol["TSLA"].quantity == -5
    assert by_symbol["TSLA"].avg_price == 200.0


def test_get_state_unknown_agent_returns_none(repo):
    assert repo.get_state("nobody") is None


def test_save_state_replaces_positions(repo):
    repo.save_state(AgentState(
        agent_id="moderate", cash=1000.0,
        positions=[Position("AAPL", 10, 100.0), Position("MSFT", 5, 300.0)],
        peak_equity=5000.0, equity_day_start=5000.0,
    ))
    # Re-save with AAPL closed (gone) and a new NVDA position
    repo.save_state(AgentState(
        agent_id="moderate", cash=1200.0,
        positions=[Position("MSFT", 5, 300.0), Position("NVDA", 2, 900.0)],
        peak_equity=5000.0, equity_day_start=5000.0,
    ))
    loaded = repo.get_state("moderate")
    assert {p.symbol for p in loaded.positions} == {"MSFT", "NVDA"}
    assert loaded.cash == 1200.0


def test_save_state_with_no_positions(repo):
    repo.save_state(AgentState(
        agent_id="conservative", cash=5000.0, positions=[],
        peak_equity=5000.0, equity_day_start=5000.0,
    ))
    loaded = repo.get_state("conservative")
    assert loaded.positions == []
    assert loaded.cash == 5000.0
