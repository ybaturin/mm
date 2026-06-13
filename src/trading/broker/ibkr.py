from __future__ import annotations

from trading.broker.types import Action, BrokerError, Fill
from trading.domain import Position

# Defaults for IB Gateway running in PAPER mode (live paper port is 4002).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002
DEFAULT_CLIENT_ID = 1


def position_from_ib(ib_pos) -> Position:
    """Translate an ib-async Position into our domain Position (signed quantity)."""
    return Position(
        symbol=ib_pos.contract.symbol,
        quantity=int(ib_pos.position),
        avg_price=float(ib_pos.avgCost),
    )


def cash_from_account_values(values, currency: str = "USD") -> float:
    """Find TotalCashValue for the given currency in ib-async accountValues()."""
    for v in values:
        if v.tag == "TotalCashValue" and v.currency == currency:
            return float(v.value)
    return 0.0


def fill_from_trade(trade, symbol: str, action: Action) -> Fill:
    """Collapse an ib-async Trade's executions into one average Fill."""
    total_shares = sum(f.execution.shares for f in trade.fills)
    if total_shares == 0:
        raise BrokerError(f"Order for {symbol} produced no fills")
    notional = sum(f.execution.shares * f.execution.price for f in trade.fills)
    return Fill(symbol=symbol, action=action,
                quantity=int(total_shares), price=notional / total_shares)


class IBKRBroker:
    """Broker backed by Interactive Brokers via ib-async. Satisfies the Broker Protocol.

    Network calls are thin; the translation logic above is pure and unit-tested. A live
    connection is verified by scripts/smoke_ibkr.py against the paper Gateway.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 client_id: int = DEFAULT_CLIENT_ID, ib=None,
                 order_timeout: float = 60.0) -> None:
        if ib is None:
            from ib_async import IB
            ib = IB()
        self.ib = ib
        self.host = host
        self.port = port
        self.client_id = client_id
        self.order_timeout = order_timeout

    def connect(self) -> None:
        self.ib.connect(self.host, self.port, clientId=self.client_id)

    def disconnect(self) -> None:
        self.ib.disconnect()

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def cash(self) -> float:
        return cash_from_account_values(self.ib.accountValues())

    def positions(self) -> list[Position]:
        return [position_from_ib(p) for p in self.ib.positions()]

    def place_market_order(self, symbol: str, action: Action, quantity: int) -> Fill:
        import time

        from ib_async import MarketOrder, Stock
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        trade = self.ib.placeOrder(contract, MarketOrder(action.value, quantity))
        # Bounded wait: never block the whole daily run forever on a stalled order.
        deadline = time.monotonic() + self.order_timeout
        while not trade.isDone():
            if time.monotonic() >= deadline:
                raise BrokerError(
                    f"Order for {symbol} not done within {self.order_timeout}s")
            self.ib.waitOnUpdate(timeout=1)
        return fill_from_trade(trade, symbol, action)

    def place_stop_order(self, symbol: str, action: Action, quantity: int,
                         stop_price: float) -> str:
        from ib_async import StopOrder, Stock
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        trade = self.ib.placeOrder(contract, StopOrder(action.value, quantity, stop_price))
        return str(trade.order.orderId)
