import pytest
from trading.broker.fake import FakeBroker
from trading.broker.types import Action, BrokerError


def test_connect_and_disconnect_toggle_state():
    b = FakeBroker(cash=5000.0)
    assert b.is_connected() is False
    b.connect()
    assert b.is_connected() is True
    b.disconnect()
    assert b.is_connected() is False


def test_cash_reports_balance():
    assert FakeBroker(cash=5000.0).cash() == 5000.0


def test_buy_reduces_cash_and_opens_long():
    b = FakeBroker(cash=5000.0)
    b.set_price("AAPL", 100.0)
    fill = b.place_market_order("AAPL", Action.BUY, 10)
    assert fill.symbol == "AAPL" and fill.quantity == 10 and fill.price == 100.0
    assert b.cash() == 4000.0
    pos = {p.symbol: p for p in b.positions()}["AAPL"]
    assert pos.quantity == 10 and pos.avg_price == 100.0


def test_sell_to_open_short_adds_proceeds():
    b = FakeBroker(cash=5000.0)
    b.set_price("TSLA", 200.0)
    b.place_market_order("TSLA", Action.SELL, 5)
    assert b.cash() == 6000.0  # 5000 + 5*200 proceeds
    pos = {p.symbol: p for p in b.positions()}["TSLA"]
    assert pos.quantity == -5 and pos.avg_price == 200.0


def test_full_close_removes_position_from_listing():
    b = FakeBroker(cash=5000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 10)
    b.set_price("AAPL", 110.0)
    b.place_market_order("AAPL", Action.SELL, 10)
    assert b.positions() == []                 # zero-qty positions are not listed
    assert b.cash() == pytest.approx(5100.0)   # -1000 +1100


def test_market_order_unknown_price_raises():
    b = FakeBroker(cash=5000.0)
    with pytest.raises(BrokerError):
        b.place_market_order("AAPL", Action.BUY, 1)


def test_stop_order_is_recorded_and_returns_id():
    b = FakeBroker(cash=5000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 10)
    oid = b.place_stop_order("AAPL", Action.SELL, 10, stop_price=92.0)
    assert isinstance(oid, str) and oid
    assert b.stop_orders[0]["symbol"] == "AAPL"
    assert b.stop_orders[0]["stop_price"] == 92.0
    assert b.stop_orders[0]["action"] is Action.SELL
