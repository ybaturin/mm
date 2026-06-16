from trading.data.bars import Bar
from trading.edge.realize import market_adjusted_multi


def _bar(date, close):
    return Bar(date=date, open=close, high=close, low=close, close=close, volume=0)


def test_returns_per_horizon_market_adjusted():
    stock = [_bar(f"2026-02-{20+i:02d}", 100.0 + i) for i in range(8)]   # +1/day
    spy = [_bar(f"2026-02-{20+i:02d}", 100.0) for i in range(8)]          # flat
    out = market_adjusted_multi(stock, spy, "2026-02-22", horizons=[1, 5])
    # entry at 2026-02-22 (close 102, index 2). h1 -> 103/102-1; h5 -> 107/102-1.
    assert abs(out[1] - (103.0 / 102.0 - 1.0)) < 1e-9
    assert abs(out[5] - (107.0 / 102.0 - 1.0)) < 1e-9


def test_missing_horizon_is_none():
    stock = [_bar("2026-02-20", 100.0), _bar("2026-02-21", 101.0)]
    spy = [_bar("2026-02-20", 100.0), _bar("2026-02-21", 100.0)]
    out = market_adjusted_multi(stock, spy, "2026-02-20", horizons=[1, 20])
    assert out[1] is not None
    assert out[20] is None
