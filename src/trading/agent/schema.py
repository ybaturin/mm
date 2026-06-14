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
    target_price: float | None = None
    horizon_days: int | None = None


class ProposalBatch(BaseModel):
    trades: list[ProposedTrade]


def _coherent_forecast(intent: Intent, reference_price: float,
                       target_price: float | None, horizon_days: int | None) -> bool:
    """A forecast is usable only on an opening trade, with both fields set and the
    target on the correct side: above entry for a long, below for a short."""
    if not intent.is_opening or target_price is None or horizon_days is None:
        return False
    if horizon_days <= 0:
        return False
    return (target_price < reference_price if intent.is_short_side
            else target_price > reference_price)


def to_domain_proposals(batch: ProposalBatch, agent_id: str) -> list[TradeProposal]:
    """Pure mapping from the LLM schema to domain TradeProposals, stamping the agent_id.
    Incoherent forecasts (missing, wrong side, non-opening) are dropped to None."""
    out = []
    for t in batch.trades:
        intent = Intent(t.intent)
        keep = _coherent_forecast(intent, t.reference_price, t.target_price, t.horizon_days)
        out.append(TradeProposal(
            agent_id=agent_id,
            symbol=t.symbol,
            intent=intent,
            quantity=t.quantity,
            reference_price=t.reference_price,
            stop_loss_price=t.stop_loss_price,
            rationale=t.rationale,
            target_price=t.target_price if keep else None,
            horizon_days=t.horizon_days if keep else None,
        ))
    return out
