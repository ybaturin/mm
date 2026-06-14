from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

_OPENING = {"open_long": "long", "open_short": "short"}
_CLOSING = {"close_long": "long", "close_short": "short"}


@dataclass(frozen=True)
class RoundTrip:
    """One closed trade: an opening fill matched (FIFO) against a closing fill."""
    symbol: str
    quantity: int
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    realized_pnl: float
    realized_pct: float
    rationale: str


def compute_round_trips(fills, rationale_by_decision) -> list[RoundTrip]:
    """Reconstruct closed round-trips from fills in chronological order.

    `fills`: iterable of mappings with keys ts, symbol, intent, quantity, price,
    decision_id — the shape JournalRepository.fills_for returns (already time-ordered).
    `rationale_by_decision`: {decision_id: rationale} for opening fills.
    Long and short are matched independently per symbol; partial closes use FIFO.
    """
    lots: dict[tuple[str, str], deque] = defaultdict(deque)
    out: list[RoundTrip] = []

    for f in fills:
        intent = f["intent"]
        symbol = f["symbol"]
        date = f["ts"][:10]

        if intent in _OPENING:
            rationale = rationale_by_decision.get(f["decision_id"], "")
            lots[(symbol, _OPENING[intent])].append(
                [f["quantity"], f["price"], date, rationale])
            continue

        if intent not in _CLOSING:
            continue

        side = _CLOSING[intent]
        queue = lots[(symbol, side)]
        remaining = f["quantity"]
        exit_price = f["price"]
        while remaining > 0 and queue:
            lot = queue[0]
            matched = min(remaining, lot[0])
            entry_price = lot[1]
            pnl = ((exit_price - entry_price) if side == "long"
                   else (entry_price - exit_price)) * matched
            pct = pnl / (entry_price * matched) if entry_price else 0.0
            out.append(RoundTrip(
                symbol=symbol, quantity=matched, entry_date=lot[2],
                entry_price=entry_price, exit_date=date, exit_price=exit_price,
                realized_pnl=round(pnl, 2), realized_pct=round(pct, 4),
                rationale=lot[3]))
            lot[0] -= matched
            remaining -= matched
            if lot[0] == 0:
                queue.popleft()

    return out
