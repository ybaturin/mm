import pytest
from trading.domain import Intent, Outcome, TradeProposal, Position, AgentState


def test_intent_is_string_enum():
    assert Intent.OPEN_SHORT.value == "open_short"
    assert Intent("close_long") is Intent.CLOSE_LONG


def test_trade_proposal_is_frozen():
    p = TradeProposal(
        agent_id="moderate",
        symbol="AAPL",
        intent=Intent.OPEN_LONG,
        quantity=10,
        reference_price=190.0,
        stop_loss_price=175.0,
        rationale="momentum",
    )
    assert p.symbol == "AAPL"
    with pytest.raises(Exception):
        p.quantity = 5  # frozen dataclass


def test_position_signed_quantity():
    long = Position(symbol="AAPL", quantity=10, avg_price=100.0)
    short = Position(symbol="TSLA", quantity=-4, avg_price=200.0)
    assert long.is_long
    assert short.is_short
    assert not short.is_long


def test_agent_state_equity_long_and_short():
    state = AgentState(
        agent_id="aggressive",
        cash=3000.0,
        positions=[
            Position(symbol="AAPL", quantity=10, avg_price=100.0),   # long
            Position(symbol="TSLA", quantity=-5, avg_price=200.0),   # short
        ],
        peak_equity=5000.0,
        equity_day_start=5000.0,
    )
    prices = {"AAPL": 110.0, "TSLA": 190.0}
    # equity = cash + 10*110 + (-5)*190 = 3000 + 1100 - 950 = 3150
    assert state.equity(prices) == pytest.approx(3150.0)
