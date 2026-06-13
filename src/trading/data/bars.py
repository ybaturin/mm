from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    date: str        # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int


class MarketDataSource(Protocol):
    def history(self, symbol: str, days: int,
                as_of_date: str | None = None) -> list[Bar]: ...
    def latest_price(self, symbol: str, as_of_date: str | None = None) -> float: ...
