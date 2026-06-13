from __future__ import annotations

import math
from typing import Protocol

from trading.config import RiskProfile
from trading.data.briefing import Briefing
from trading.domain import Intent, TradeProposal


class Strategy(Protocol):
    """Produces trade proposals from a briefing. AgentCore (Claude) and FakeStrategy both satisfy it."""
    def propose(self, briefing: Briefing, profile: RiskProfile) -> list[TradeProposal]: ...


class FakeStrategy:
    """Deterministic momentum rule for simulation and integration tests — no LLM.

    Long-only. Buys an un-held symbol trading above its SMA20 (and not overbought);
    closes a held long that has fallen below its SMA20. Exercises the full pipeline
    (sizing, guardrails, execution, ledger) reproducibly and for free.
    """

    OVERBOUGHT = 70.0

    def propose(self, briefing: Briefing, profile: RiskProfile) -> list[TradeProposal]:
        proposals: list[TradeProposal] = []
        for s in briefing.symbols:
            if s.sma20 is None:
                continue

            if s.held_quantity == 0 and s.price > s.sma20:
                if s.rsi14 is not None and s.rsi14 >= self.OVERBOUGHT:
                    continue
                max_notional = profile.max_position_pct * profile.budget
                quantity = math.floor(max_notional / s.price)
                if quantity <= 0:
                    continue
                proposals.append(TradeProposal(
                    agent_id=briefing.agent_id, symbol=s.symbol, intent=Intent.OPEN_LONG,
                    quantity=quantity, reference_price=s.price,
                    stop_loss_price=round(s.price * (1 - profile.stop_loss_pct), 2),
                    rationale=f"price {s.price} above sma20 {s.sma20}",
                ))
            elif s.held_quantity > 0 and s.price < s.sma20:
                proposals.append(TradeProposal(
                    agent_id=briefing.agent_id, symbol=s.symbol, intent=Intent.CLOSE_LONG,
                    quantity=s.held_quantity, reference_price=s.price,
                    stop_loss_price=None, rationale=f"price {s.price} below sma20 {s.sma20}",
                ))

            if len(proposals) >= profile.max_trades_per_day:
                break
        return proposals[: profile.max_trades_per_day]
