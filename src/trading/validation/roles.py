from __future__ import annotations

from dataclasses import dataclass

from trading.data.briefing import Briefing
from trading.domain import TradeProposal


@dataclass(frozen=True)
class Role:
    key: str
    title: str
    instruction: str


# Distinct angles of attack — diversity is the point; identical validators make
# correlated mistakes and add no value.
ROLES: list[Role] = [
    Role("risk_skeptic", "Risk Skeptic",
         "Assess the downside. What if the thesis is wrong? Is the stop-loss placed "
         "sensibly? Is the position too large for this risk profile? Veto only if the "
         "downside is concretely poorly controlled."),
    Role("catalyst_checker", "Catalyst Checker",
         "Check for a known near-term event that makes this trade ill-timed today — "
         "earnings, ex-dividend, a scheduled macro print, a known overhang. Veto only if "
         "a specific, foreseeable catalyst makes acting today reckless."),
    Role("devils_advocate", "Devil's Advocate",
         "Argue the opposite case as strongly as you can. Try to refute the rationale. "
         "Veto only if the case against this trade is concretely stronger than the case for it."),
]


def build_validator_system(role: Role) -> str:
    return (
        f"You are the '{role.title}' on a trade-review panel for an automated trading system.\n"
        f"{role.instruction}\n\n"
        f"You can ONLY veto (block) or allow a trade — you cannot resize it, add trades, or "
        f"relax any limit. The trade has already passed hard risk limits; your job is the "
        f"judgment call those limits cannot make.\n"
        f"Veto only with a concrete, specific reason. If you have no specific concern, allow it "
        f"(veto=false). Do not veto on vague unease."
    )


def build_validator_user(proposal: TradeProposal, briefing: Briefing) -> str:
    brief = next((s for s in briefing.symbols if s.symbol == proposal.symbol), None)
    context = (
        f"price={brief.price} sma20={brief.sma20} sma50={brief.sma50} "
        f"rsi14={brief.rsi14} return_5d={brief.return_5d} held={brief.held_quantity}"
        if brief else "no market context available"
    )
    return (
        f"Date: {briefing.as_of_date}\n"
        f"Proposed trade by the '{proposal.agent_id}' agent:\n"
        f"  {proposal.intent.value} {proposal.quantity} {proposal.symbol} "
        f"@ ~{proposal.reference_price}, stop={proposal.stop_loss_price}\n"
        f"  Rationale: {proposal.rationale}\n\n"
        f"Market context for {proposal.symbol}: {context}\n\n"
        f"Return your verdict."
    )
