from __future__ import annotations

from dataclasses import dataclass

from trading.broker.base import Broker
from trading.broker.types import Action, Fill


def nav(broker: Broker, prices: dict[str, float]) -> float:
    """Net liquidation value: cash plus signed mark-to-market of all positions."""
    return broker.cash() + sum(p.quantity * prices[p.symbol] for p in broker.positions())


@dataclass(frozen=True)
class WatchdogResult:
    breached: bool
    nav: float
    floor: float


class Watchdog:
    """Independent NAV-floor monitor. A breach triggers a global stop (flatten + freeze)."""

    def __init__(self, starting_nav: float, floor_fraction: float = 0.8) -> None:
        self.starting_nav = starting_nav
        self.floor_fraction = floor_fraction

    def check(self, broker: Broker, prices: dict[str, float]) -> WatchdogResult:
        current = nav(broker, prices)
        floor = self.starting_nav * self.floor_fraction
        return WatchdogResult(breached=current < floor, nav=current, floor=floor)


def flatten(broker: Broker, prices: dict[str, float]) -> list[Fill]:
    """Close every open position with market orders. Uses only the Broker Protocol."""
    fills: list[Fill] = []
    for p in list(broker.positions()):
        action = Action.SELL if p.quantity > 0 else Action.BUY
        fills.append(broker.place_market_order(p.symbol, action, abs(p.quantity)))
    return fills
