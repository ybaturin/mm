import pytest
from trading.data.indicators import pct_change, rsi, sma


def test_sma_full_window():
    assert sma([1, 2, 3, 4, 5], 5) == 3.0


def test_sma_uses_most_recent_window():
    assert sma([1, 2, 3, 4, 5], 3) == 4.0   # mean of 3,4,5


def test_sma_insufficient_data_returns_none():
    assert sma([1, 2], 5) is None


def test_pct_change_over_n_days():
    assert pct_change([100.0, 110.0], 1) == pytest.approx(0.10)
    assert pct_change([100.0, 105.0, 110.0], 2) == pytest.approx(0.10)


def test_pct_change_insufficient_data_returns_none():
    assert pct_change([100.0], 1) is None


def test_rsi_all_gains_is_100():
    closes = list(range(1, 16))            # strictly increasing, 15 values
    assert rsi(closes, period=14) == 100.0


def test_rsi_all_losses_is_0():
    closes = list(range(15, 0, -1))        # strictly decreasing
    assert rsi(closes, period=14) == 0.0


def test_rsi_balanced_is_50():
    # 7 up moves of +1 then 7 down moves of -1 -> avg gain == avg loss -> RSI 50
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 16, 15, 14, 13, 12, 11, 10]
    assert rsi(closes, period=14) == pytest.approx(50.0)


def test_rsi_insufficient_data_returns_none():
    assert rsi([1, 2, 3], period=14) is None
