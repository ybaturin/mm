from __future__ import annotations

from trading.broker.types import Action, BrokerError, Fill, apply_fill
from trading.domain import Position


class FakeBroker:
    """In-memory broker. Fills at prices set via set_price(). Deterministic.

    The test and development/simulation backbone — satisfies the Broker Protocol.
    """

    def __init__(self, cash: float = 0.0) -> None:
        self._cash = cash
        self._positions: dict[str, Position] = {}
        self._prices: dict[str, float] = {}
        self._connected = False
        self._next_id = 1
        self.stop_orders: list[dict] = []

    # --- connection ---
    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # --- simulation control (not part of the Broker Protocol) ---
    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    def seed(self, cash: float, positions: list[Position]) -> None:
        """Load a known cash + positions snapshot (e.g. from the ledger across runs)."""
        self._cash = cash
        self._positions = {p.symbol: p for p in positions}

    # --- account ---
    def cash(self) -> float:
        return self._cash

    def positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.quantity != 0]

    # --- orders ---
    def place_market_order(self, symbol: str, action: Action, quantity: int) -> Fill:
        if symbol not in self._prices:
            raise BrokerError(f"No simulated price for {symbol}")
        price = self._prices[symbol]
        current = self._positions.get(symbol)
        q0 = current.quantity if current else 0
        avg0 = current.avg_price if current else 0.0
        new_qty, new_avg = apply_fill(q0, avg0, action, quantity, price)
        self._positions[symbol] = Position(symbol, new_qty, new_avg)
        self._cash += (-quantity * price) if action is Action.BUY else (quantity * price)
        return Fill(symbol=symbol, action=action, quantity=quantity, price=price)

    def place_stop_order(
        self, symbol: str, action: Action, quantity: int, stop_price: float
    ) -> str:
        oid = f"stop-{self._next_id}"
        self._next_id += 1
        self.stop_orders.append(
            {"id": oid, "symbol": symbol, "action": action,
             "quantity": quantity, "stop_price": stop_price}
        )
        return oid

    def cancel_all(self) -> None:
        self.stop_orders.clear()
