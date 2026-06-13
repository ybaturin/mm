from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    """Order side. Values match the strings IBKR expects."""
    BUY = "BUY"
    SELL = "SELL"


class BrokerError(Exception):
    """Raised when a broker operation cannot complete (no fill, not connected, etc.)."""


@dataclass(frozen=True)
class Fill:
    symbol: str
    action: Action
    quantity: int        # shares filled (unsigned)
    price: float         # average fill price


def apply_fill(
    qty: int, avg: float, action: Action, fill_qty: int, price: float
) -> tuple[int, float]:
    """Return (new_signed_quantity, new_avg_price) after applying a fill.

    Weighted-average cost basis. Mirrors how a real account tracks a position so the
    FakeBroker is a faithful stand-in:
      - opening / increasing magnitude in the same direction -> weighted average
      - reducing without crossing zero -> average unchanged
      - exact close -> (0, 0)
      - flipping through zero -> remainder opens at the fill price
    """
    delta = fill_qty if action is Action.BUY else -fill_qty
    new_qty = qty + delta

    same_direction = qty == 0 or (qty > 0) == (delta > 0)
    if same_direction:
        total_cost = abs(qty) * avg + abs(delta) * price
        return new_qty, total_cost / abs(new_qty)

    # opposite direction: reduce, close, or flip
    if abs(delta) < abs(qty):
        return new_qty, avg
    if abs(delta) == abs(qty):
        return 0, 0.0
    return new_qty, price
