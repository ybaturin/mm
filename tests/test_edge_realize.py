from trading.data.bars import Bar
from trading.edge.realize import forward_return, market_adjusted_return


def _bar(date, close):
    return Bar(date=date, open=close, high=close, low=close, close=close, volume=0)


def test_forward_return_picks_entry_and_exit_n_days_later():
    bars = [_bar("2026-02-23", 100.0), _bar("2026-02-24", 101.0),
            _bar("2026-02-25", 102.0), _bar("2026-02-26", 103.0)]
    # entry = first bar on/after decision_date (100), exit = 2 trading days later (102).
    assert forward_return(bars, "2026-02-23", horizon_days=2) == (102.0 / 100.0 - 1.0)


def test_forward_return_none_when_not_enough_forward_bars():
    bars = [_bar("2026-02-23", 100.0), _bar("2026-02-24", 101.0)]
    assert forward_return(bars, "2026-02-23", horizon_days=5) is None


def test_market_adjusted_subtracts_spy():
    stock = [_bar("2026-02-23", 100.0), _bar("2026-02-24", 110.0)]   # +10%
    spy = [_bar("2026-02-23", 100.0), _bar("2026-02-24", 104.0)]     # +4%
    adj = market_adjusted_return(stock, spy, "2026-02-23", horizon_days=1)
    assert abs(adj - 0.06) < 1e-9
