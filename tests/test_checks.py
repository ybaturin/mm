import pytest
from trading.domain import Intent, Position
from trading.guardrails.checks import (
    reference_price_ok,
    stop_loss_ok,
    capped_quantity,
    has_sufficient_cash,
    owns_enough_to_close,
    daily_loss_breached,
    drawdown_breached,
)


# --- reference price sanity ---
def test_reference_price_ok_within_tolerance():
    assert reference_price_ok(ref=100.0, market=103.0, tolerance=0.05) is True


def test_reference_price_rejected_when_stale():
    assert reference_price_ok(ref=50.0, market=190.0, tolerance=0.05) is False


# --- stop loss validity ---
def test_stop_required_for_opening_long_below_market():
    assert stop_loss_ok(Intent.OPEN_LONG, stop=90.0, market=100.0) is True
    assert stop_loss_ok(Intent.OPEN_LONG, stop=110.0, market=100.0) is False
    assert stop_loss_ok(Intent.OPEN_LONG, stop=None, market=100.0) is False


def test_stop_required_for_opening_short_above_market():
    assert stop_loss_ok(Intent.OPEN_SHORT, stop=110.0, market=100.0) is True
    assert stop_loss_ok(Intent.OPEN_SHORT, stop=90.0, market=100.0) is False
    assert stop_loss_ok(Intent.OPEN_SHORT, stop=None, market=100.0) is False


def test_stop_not_required_for_closing():
    assert stop_loss_ok(Intent.CLOSE_LONG, stop=None, market=100.0) is True
    assert stop_loss_ok(Intent.CLOSE_SHORT, stop=None, market=100.0) is True


# --- position sizing cap (trims quantity to fit max_position_pct of budget) ---
def test_capped_quantity_trims_to_fit():
    # cap = 0.40 * 5000 = 2000 USD; at price 100 -> max 20 shares
    assert capped_quantity(qty=50, price=100.0, max_position_pct=0.40, budget=5000.0) == 20


def test_capped_quantity_leaves_small_order_untouched():
    assert capped_quantity(qty=5, price=100.0, max_position_pct=0.40, budget=5000.0) == 5


# --- cash / holdings sufficiency ---
def test_has_sufficient_cash():
    assert has_sufficient_cash(cash=1000.0, qty=5, price=100.0) is True
    assert has_sufficient_cash(cash=400.0, qty=5, price=100.0) is False


def test_owns_enough_to_close_long():
    pos = Position(symbol="AAPL", quantity=10, avg_price=100.0)
    assert owns_enough_to_close(pos, Intent.CLOSE_LONG, qty=10) is True
    assert owns_enough_to_close(pos, Intent.CLOSE_LONG, qty=11) is False


def test_owns_enough_to_close_short():
    pos = Position(symbol="TSLA", quantity=-8, avg_price=200.0)
    assert owns_enough_to_close(pos, Intent.CLOSE_SHORT, qty=8) is True
    assert owns_enough_to_close(pos, Intent.CLOSE_SHORT, qty=9) is False


def test_owns_enough_to_close_no_position():
    assert owns_enough_to_close(None, Intent.CLOSE_LONG, qty=1) is False


# --- kill switches ---
def test_daily_loss_breached():
    # budget 5000, limit 5% -> 250 loss triggers
    assert daily_loss_breached(equity_now=4740.0, equity_day_start=5000.0,
                               budget=5000.0, limit_pct=0.05) is True
    assert daily_loss_breached(equity_now=4800.0, equity_day_start=5000.0,
                               budget=5000.0, limit_pct=0.05) is False


def test_drawdown_breached():
    # peak 6000, max dd 15% -> equity below 5100 triggers
    assert drawdown_breached(equity_now=5000.0, peak_equity=6000.0, max_drawdown_pct=0.15) is True
    assert drawdown_breached(equity_now=5200.0, peak_equity=6000.0, max_drawdown_pct=0.15) is False
