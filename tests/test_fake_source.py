import pytest
from trading.data.bars import Bar
from trading.data.fake_source import FakeMarketDataSource


def bars(closes):
    return [Bar(date=f"2026-06-{i+1:02d}", open=c, high=c, low=c, close=c, volume=1000)
            for i, c in enumerate(closes)]


def test_history_returns_supplied_bars():
    src = FakeMarketDataSource({"AAPL": bars([100.0, 101.0, 102.0])})
    hist = src.history("AAPL", days=5)
    assert [b.close for b in hist] == [100.0, 101.0, 102.0]


def test_history_respects_days_limit():
    src = FakeMarketDataSource({"AAPL": bars([1, 2, 3, 4, 5])})
    hist = src.history("AAPL", days=2)
    assert [b.close for b in hist] == [4, 5]   # most recent `days` bars


def test_latest_price_is_last_close():
    src = FakeMarketDataSource({"AAPL": bars([100.0, 105.0])})
    assert src.latest_price("AAPL") == 105.0


def test_unknown_symbol_raises():
    src = FakeMarketDataSource({})
    with pytest.raises(KeyError):
        src.history("ZZZ", days=5)
