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
    def history(self, symbol: str, days: int) -> list[Bar]: ...
    def latest_price(self, symbol: str) -> float: ...
