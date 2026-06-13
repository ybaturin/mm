import pytest
from trading.broker.types import Action, Fill, apply_fill


def test_action_values_match_ibkr():
    assert Action.BUY.value == "BUY"
    assert Action.SELL.value == "SELL"


def test_fill_is_frozen():
    f = Fill(symbol="AAPL", action=Action.BUY, quantity=10, price=101.0)
    assert f.quantity == 10
    with pytest.raises(Exception):
        f.price = 5.0


def test_apply_fill_opens_long():
    assert apply_fill(0, 0.0, Action.BUY, 10, 100.0) == (10, 100.0)


def test_apply_fill_adds_to_long_weighted_average():
    # 10 @ 100 then +10 @ 120 -> 20 @ 110
    assert apply_fill(10, 100.0, Action.BUY, 10, 120.0) == (20, 110.0)


def test_apply_fill_partial_close_keeps_average():
    assert apply_fill(10, 100.0, Action.SELL, 4, 130.0) == (6, 100.0)


def test_apply_fill_full_close_resets():
    assert apply_fill(10, 100.0, Action.SELL, 10, 130.0) == (0, 0.0)


def test_apply_fill_opens_short():
    assert apply_fill(0, 0.0, Action.SELL, 5, 200.0) == (-5, 200.0)


def test_apply_fill_adds_to_short_weighted_average():
    # -5 @ 200 then sell 5 more @ 180 -> -10 @ 190
    assert apply_fill(-5, 200.0, Action.SELL, 5, 180.0) == (-10, 190.0)


def test_apply_fill_flips_through_zero_uses_fill_price():
    # long 5 @ 100, sell 8 @ 90 -> short 3, avg = fill price 90
    assert apply_fill(5, 100.0, Action.SELL, 8, 90.0) == (-3, 90.0)
