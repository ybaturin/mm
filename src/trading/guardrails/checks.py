from __future__ import annotations

import math

from trading.domain import Intent, Position


def reference_price_ok(ref: float, market: float, tolerance: float) -> bool:
    """The decision maker's assumed price must be close to the real market price.

    Catches a stale/hallucinated LLM price before it sizes a trade wrongly.
    """
    if market <= 0:
        return False
    return abs(ref - market) / market <= tolerance


def stop_loss_ok(intent: Intent, stop: float | None, market: float) -> bool:
    """Opening trades require a stop on the correct side of the market.

    Long: stop below market. Short: stop above market (unbounded loss otherwise).
    Closing trades do not require a stop.
    """
    if not intent.is_opening:
        return True
    if stop is None:
        return False
    if intent == Intent.OPEN_LONG:
        return stop < market
    if intent == Intent.OPEN_SHORT:
        return stop > market
    return False


def stop_loss_within_limit(intent: Intent, stop: float | None, market: float,
                           max_loss_pct: float, tolerance: float = 0.005) -> bool:
    """The stop must not permit a loss larger than the profile's stop_loss_pct.

    A tighter (more protective) stop is fine; a looser one is rejected. The small
    tolerance absorbs rounding in a proposed stop price. Side/presence is checked
    separately by stop_loss_ok; here a None or wrong-side stop is treated as OK.
    """
    if not intent.is_opening or stop is None or market <= 0:
        return True
    if intent == Intent.OPEN_LONG:
        loss_pct = (market - stop) / market
    elif intent == Intent.OPEN_SHORT:
        loss_pct = (stop - market) / market
    else:
        return True
    return loss_pct <= max_loss_pct + tolerance


def capped_quantity(qty: int, price: float, max_position_pct: float, budget: float) -> int:
    """Trim share count so notional does not exceed max_position_pct of budget.

    Returns the largest allowed quantity (may be 0 if even one share is too big).
    """
    max_notional = max_position_pct * budget
    max_shares = math.floor(max_notional / price)
    return min(qty, max_shares)


def has_sufficient_cash(cash: float, qty: int, price: float) -> bool:
    return cash >= qty * price


def owns_enough_to_close(position: Position | None, intent: Intent, qty: int) -> bool:
    """A close must not exceed the held quantity on the matching side."""
    if position is None:
        return False
    if intent == Intent.CLOSE_LONG:
        return position.quantity >= qty
    if intent == Intent.CLOSE_SHORT:
        return -position.quantity >= qty
    return False


def daily_loss_breached(equity_now: float, equity_day_start: float,
                        budget: float, limit_pct: float) -> bool:
    loss = equity_day_start - equity_now
    return loss >= limit_pct * budget


def drawdown_breached(equity_now: float, peak_equity: float, max_drawdown_pct: float) -> bool:
    if peak_equity <= 0:
        return False
    drawdown = (peak_equity - equity_now) / peak_equity
    return drawdown >= max_drawdown_pct
