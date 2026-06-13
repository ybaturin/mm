from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from trading.domain import Intent, TradeProposal


class ProposedTrade(BaseModel):
    """One trade Claude proposes. The fixed shape Claude is constrained to return.

    There is deliberately no field for withdrawals, options, leverage, or free-form
    actions — the model can only express a stock/ETF buy or sell within this schema.
    """
    symbol: str
    intent: Literal["open_long", "close_long", "open_short", "close_short"]
    quantity: int
    reference_price: float          # the price Claude believes it is acting on
    stop_loss_price: float | None
    rationale: str


class ProposalBatch(BaseModel):
    trades: list[ProposedTrade]


def to_domain_proposals(batch: ProposalBatch, agent_id: str) -> list[TradeProposal]:
    """Pure mapping from the LLM schema to domain TradeProposals, stamping the agent_id."""
    return [
        TradeProposal(
            agent_id=agent_id,
            symbol=t.symbol,
            intent=Intent(t.intent),
            quantity=t.quantity,
            reference_price=t.reference_price,
            stop_loss_price=t.stop_loss_price,
            rationale=t.rationale,
        )
        for t in batch.trades
    ]
