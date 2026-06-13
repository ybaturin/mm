from __future__ import annotations

from trading.broker.types import Fill
from trading.domain import TradeProposal
from trading.guardrails.engine import GuardrailDecision

_INTENT_RU = {
    "open_long": "Купить (лонг)",
    "close_long": "Закрыть лонг",
    "open_short": "Открыть шорт",
    "close_short": "Закрыть шорт",
}
_ACTION_RU = {"BUY": "Покупка", "SELL": "Продажа"}


def intent_label(code: str) -> str:
    """Russian label for an Intent value (e.g. 'open_long' -> 'Купить (лонг)')."""
    return _INTENT_RU.get(code, code)


def format_confirmation(proposal: TradeProposal, decision: GuardrailDecision) -> str:
    notional = decision.quantity * proposal.reference_price
    stop = "—" if proposal.stop_loss_price is None else f"{proposal.stop_loss_price:g}"
    intent = _INTENT_RU.get(proposal.intent.value, proposal.intent.value)
    return (
        f"Подтвердить сделку? [{proposal.agent_id}]\n"
        f"{intent}: {decision.quantity} {proposal.symbol} "
        f"@ ~{proposal.reference_price:g}  (≈${notional:,.0f})\n"
        f"стоп: {stop}\n"
        f"основание: {proposal.rationale}"
    )


def format_fill(agent_id: str, fill: Fill) -> str:
    action = _ACTION_RU.get(fill.action.value, fill.action.value)
    return (f"[{agent_id}] {action}: {fill.quantity} {fill.symbol} "
            f"@ {fill.price:g}")


def format_digest(agent_id: str, date: str, executed: list[str],
                  rejected: int, vetoed: int, declined: int = 0) -> str:
    body = "сделок нет" if not executed else "\n".join(f"  • {line}" for line in executed)
    return (
        f"📊 {agent_id} — {date}\n"
        f"{body}\n"
        f"(отклонено guardrails: {rejected}, вето панели: {vetoed}, отклонено вручную: {declined})"
    )


def format_alert(kind: str, detail: str) -> str:
    return f"⚠️ [{kind}] {detail}"


def format_pnl(agent_id: str, start: float, end: float) -> str:
    pnl = end - start
    pct = (pnl / start) if start else 0.0
    return f"💰 {agent_id}: ${start:,.0f} → ${end:,.2f}  (P&L {pnl:+,.2f}, {pct:+.1%})"
