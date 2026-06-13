from __future__ import annotations

from trading.data.bars import Bar


class FakeMarketDataSource:
    """In-memory market data for tests and simulation. Satisfies MarketDataSource."""

    def __init__(self, data: dict[str, list[Bar]]) -> None:
        self._data = data

    def history(self, symbol: str, days: int,
                as_of_date: str | None = None) -> list[Bar]:
        if symbol not in self._data:
            raise KeyError(symbol)
        bars = self._data[symbol]
        if as_of_date is not None:
            bars = [b for b in bars if b.date <= as_of_date]
        return bars[-days:]

    def latest_price(self, symbol: str, as_of_date: str | None = None) -> float:
        bars = self.history(symbol, days=1, as_of_date=as_of_date)
        if not bars:
            raise KeyError(symbol)
        return bars[-1].close
