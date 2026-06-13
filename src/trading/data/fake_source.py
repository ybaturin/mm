from __future__ import annotations

from trading.data.bars import Bar


class FakeMarketDataSource:
    """In-memory market data for tests and simulation. Satisfies MarketDataSource."""

    def __init__(self, data: dict[str, list[Bar]]) -> None:
        self._data = data

    def history(self, symbol: str, days: int) -> list[Bar]:
        if symbol not in self._data:
            raise KeyError(symbol)
        return self._data[symbol][-days:]

    def latest_price(self, symbol: str) -> float:
        if symbol not in self._data or not self._data[symbol]:
            raise KeyError(symbol)
        return self._data[symbol][-1].close
