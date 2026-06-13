from types import SimpleNamespace

import pytest
from trading.broker.ibkr import (
    IBKRBroker,
    cash_from_account_values,
    fill_from_trade,
    position_from_ib,
)
from trading.broker.types import Action, BrokerError


def test_position_from_ib_maps_fields():
    ib_pos = SimpleNamespace(
        contract=SimpleNamespace(symbol="AAPL"), position=10.0, avgCost=100.0
    )
    pos = position_from_ib(ib_pos)
    assert pos.symbol == "AAPL" and pos.quantity == 10 and pos.avg_price == 100.0


def test_position_from_ib_handles_short():
    ib_pos = SimpleNamespace(
        contract=SimpleNamespace(symbol="TSLA"), position=-5.0, avgCost=200.0
    )
    assert position_from_ib(ib_pos).quantity == -5


def test_cash_from_account_values_picks_total_cash_usd():
    values = [
        SimpleNamespace(tag="TotalCashValue", value="3210.55", currency="USD"),
        SimpleNamespace(tag="TotalCashValue", value="999.0", currency="EUR"),
        SimpleNamespace(tag="NetLiquidation", value="5000.0", currency="USD"),
    ]
    assert cash_from_account_values(values) == pytest.approx(3210.55)


def test_cash_from_account_values_missing_returns_zero():
    assert cash_from_account_values([]) == 0.0


def test_fill_from_trade_averages_executions():
    trade = SimpleNamespace(fills=[
        SimpleNamespace(execution=SimpleNamespace(shares=6, price=100.0)),
        SimpleNamespace(execution=SimpleNamespace(shares=4, price=105.0)),
    ])
    fill = fill_from_trade(trade, "AAPL", Action.BUY)
    assert fill.quantity == 10
    assert fill.price == pytest.approx(102.0)   # (600+420)/10


def test_fill_from_trade_no_fills_raises():
    trade = SimpleNamespace(fills=[])
    with pytest.raises(BrokerError):
        fill_from_trade(trade, "AAPL", Action.BUY)


def test_ibkr_broker_reads_positions_via_injected_ib():
    fake_ib = SimpleNamespace(
        positions=lambda: [
            SimpleNamespace(contract=SimpleNamespace(symbol="AAPL"), position=3.0, avgCost=90.0)
        ]
    )
    broker = IBKRBroker(ib=fake_ib)
    positions = broker.positions()
    assert len(positions) == 1 and positions[0].symbol == "AAPL"


def test_ibkr_broker_reads_cash_via_injected_ib():
    fake_ib = SimpleNamespace(
        accountValues=lambda: [
            SimpleNamespace(tag="TotalCashValue", value="4200.0", currency="USD")
        ]
    )
    assert IBKRBroker(ib=fake_ib).cash() == pytest.approx(4200.0)


def test_ibkr_broker_scopes_positions_to_its_account():
    captured = {}

    def positions(account=""):
        captured["account"] = account
        return []

    fake_ib = SimpleNamespace(positions=positions)
    IBKRBroker(ib=fake_ib, account="DU123").positions()
    assert captured["account"] == "DU123"          # query scoped to this agent's account


def test_ibkr_broker_tags_orders_with_its_account():
    placed = {}

    class _Trade:
        order = SimpleNamespace(orderId=1)
        fills = [SimpleNamespace(execution=SimpleNamespace(shares=1, price=100.0))]

        def isDone(self):
            return True

    def place(contract, order):
        placed["account"] = order.account
        return _Trade()

    fake_ib = SimpleNamespace(qualifyContracts=lambda c: None, placeOrder=place,
                              waitOnUpdate=lambda timeout=1: None)
    IBKRBroker(ib=fake_ib, account="DU123").place_market_order("AAPL", Action.BUY, 1)
    assert placed["account"] == "DU123"            # order routed to this agent's account


def test_place_market_order_times_out_when_never_filled():
    class _NeverDone:
        order = SimpleNamespace(orderId=7)

        def isDone(self):
            return False

    fake_ib = SimpleNamespace(
        qualifyContracts=lambda c: None,
        placeOrder=lambda c, o: _NeverDone(),
        waitOnUpdate=lambda timeout=1: None,
    )
    broker = IBKRBroker(ib=fake_ib, order_timeout=0.0)
    with pytest.raises(BrokerError):                 # must not hang forever
        broker.place_market_order("AAPL", Action.BUY, 1)
