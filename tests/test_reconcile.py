from trading.broker.fake import FakeBroker
from trading.broker.types import Action
from trading.domain import AgentState, Position
from trading.safety.reconcile import reconcile


def broker_with(cash, holdings):
    """holdings: list of (symbol, qty, price) — built by buying at `price`."""
    b = FakeBroker(cash=cash)
    for symbol, qty, price in holdings:
        b.set_price(symbol, price)
        b.place_market_order(symbol, Action.BUY, qty)
    return b


def test_matching_state_reconciles_ok():
    b = FakeBroker(cash=5000.0)
    state = AgentState("moderate", cash=5000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    result = reconcile(state, b)
    assert result.ok is True
    assert result.discrepancies == []


def test_cash_mismatch_is_flagged():
    b = FakeBroker(cash=4000.0)
    state = AgentState("moderate", cash=5000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    result = reconcile(state, b)
    assert result.ok is False
    assert any("cash" in d.lower() for d in result.discrepancies)


def test_unknown_position_is_flagged():
    # broker holds AAPL the ledger doesn't know about
    b = FakeBroker(cash=4000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 10)
    state = AgentState("moderate", cash=3000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    result = reconcile(state, b)
    assert result.ok is False
    assert any("AAPL" in d for d in result.discrepancies)


def test_quantity_mismatch_is_flagged():
    b = FakeBroker(cash=4000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 10)
    state = AgentState("moderate", cash=4000.0,
                       positions=[Position("AAPL", 7, 100.0)],   # ledger says 7, broker 10
                       peak_equity=5000.0, equity_day_start=5000.0)
    result = reconcile(state, b)
    assert result.ok is False
    assert any("AAPL" in d for d in result.discrepancies)


def test_cash_within_tolerance_is_ok():
    b = FakeBroker(cash=5000.005)
    state = AgentState("moderate", cash=5000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    assert reconcile(state, b, tolerance=0.01).ok is True
