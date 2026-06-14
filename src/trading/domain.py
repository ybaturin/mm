from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    """Direction of a proposed trade. Opening vs closing matters for guardrails."""
    OPEN_LONG = "open_long"
    CLOSE_LONG = "close_long"
    OPEN_SHORT = "open_short"
    CLOSE_SHORT = "close_short"

    @property
    def is_opening(self) -> bool:
        return self in (Intent.OPEN_LONG, Intent.OPEN_SHORT)

    @property
    def is_short_side(self) -> bool:
        return self in (Intent.OPEN_SHORT, Intent.CLOSE_SHORT)


class Outcome(str, Enum):
    """Result of evaluating a proposal through the guardrails."""
    APPROVED_AUTO = "approved_auto"
    NEEDS_CONFIRMATION = "needs_confirmation"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TradeProposal:
    """One trade an agent wants to make. Produced by the Agent Core, never by guardrails."""
    agent_id: str
    symbol: str
    intent: Intent
    quantity: int                  # always > 0; direction is carried by `intent`
    reference_price: float         # price the decision maker believed at proposal time
    stop_loss_price: float | None
    rationale: str
    target_price: float | None = None     # forecast: where the agent expects price to go
    horizon_days: int | None = None       # forecast: by when, in calendar days


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int                  # signed: positive = long, negative = short
    avg_price: float

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0


@dataclass
class AgentState:
    """Snapshot of one virtual sub-account at decision time."""
    agent_id: str
    cash: float
    positions: list[Position] = field(default_factory=list)
    peak_equity: float = 0.0
    equity_day_start: float = 0.0

    def position_for(self, symbol: str) -> Position | None:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None

    def equity(self, prices: dict[str, float]) -> float:
        """Cash plus signed market value of all positions.

        Short proceeds are assumed already reflected in `cash`, so a short's
        signed value (negative) yields correct mark-to-market P&L.
        """
        total = self.cash
        for p in self.positions:
            total += p.quantity * prices[p.symbol]
        return total
