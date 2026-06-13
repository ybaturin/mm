from __future__ import annotations

from trading.broker.types import Fill
from trading.domain import TradeProposal
from trading.guardrails.engine import GuardrailDecision


def format_confirmation(proposal: TradeProposal, decision: GuardrailDecision) -> str:
    notional = decision.quantity * proposal.reference_price
    stop = "—" if proposal.stop_loss_price is None else f"{proposal.stop_loss_price:g}"
    return (
        f"Confirm trade? [{proposal.agent_id}]\n"
        f"{proposal.intent.value} {decision.quantity} {proposal.symbol} "
        f"@ ~{proposal.reference_price:g}  (≈${notional:,.0f})\n"
        f"stop: {stop}\n"
        f"why: {proposal.rationale}"
    )


def format_fill(agent_id: str, fill: Fill) -> str:
    return (f"[{agent_id}] {fill.action.value} {fill.quantity} {fill.symbol} "
            f"@ {fill.price:g}")


def format_digest(agent_id: str, date: str, executed: list[str],
                  rejected: int, vetoed: int) -> str:
    if not executed:
        body = "no trades today"
    else:
        body = "\n".join(f"  • {line}" for line in executed)
    return (
        f"📊 {agent_id} — {date}\n"
        f"{body}\n"
        f"(rejected: {rejected}, vetoed: {vetoed})"
    )


def format_alert(kind: str, detail: str) -> str:
    return f"⚠️ [{kind}] {detail}"


def format_pnl(agent_id: str, start: float, end: float) -> str:
    pnl = end - start
    pct = (pnl / start) if start else 0.0
    return f"💰 {agent_id}: ${start:,.0f} → ${end:,.2f}  ({pnl:+,.2f}, {pct:+.1%})"
