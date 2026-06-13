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


# Imported at the bottom to keep the top of the file clean; queries.py does not import
# format.py, so there is no import cycle.
from trading.reporting.queries import (  # noqa: E402
    PnlReport, PositionsReport, StatusReport, TradesReport,
)

_PERIOD_RU = {"day": "сегодня", "week": "неделю", "month": "месяц", "all": "всё время"}


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _delta(pnl: float, pct: float) -> str:
    return f"({pnl:+,.2f}, {pct:+.1%})"


def format_pnl_report(rep: PnlReport) -> str:
    head = (f"💰 P&L за {_PERIOD_RU.get(rep.period, rep.period)}\n"
            f"Портфель: {_money(rep.portfolio_start)} → {_money(rep.portfolio_end)}  "
            f"{_delta(rep.portfolio_pnl, rep.portfolio_pct)}")
    if not rep.per_agent:
        return head + "\nнет данных"
    lines = [f"  • {l.agent_id}: {_money(l.start_equity)} → {_money(l.end_equity)} "
             f"{_delta(l.pnl, l.pct)}" for l in rep.per_agent]
    return head + "\n" + "\n".join(lines)


def format_positions(rep: PositionsReport) -> str:
    head = (f"📦 Активные позиции\n"
            f"Портфель: рыночная стоимость {_money(rep.portfolio_market_value)}, "
            f"нереализ. P&L {rep.portfolio_unrealized:+,.2f}")
    blocks = []
    for agent_id, lines in rep.per_agent.items():
        if not lines:
            blocks.append(f"{agent_id}: позиций нет")
            continue
        rows = []
        for l in lines:
            side = "LONG" if l.quantity > 0 else "SHORT"
            rows.append(f"  • {side} {abs(l.quantity)} {l.symbol} "
                        f"@ {_money(l.avg_price)} → {_money(l.current_price)}  "
                        f"(P&L {l.unrealized_pnl:+,.2f})")
        blocks.append(f"{agent_id}:\n" + "\n".join(rows))
    return head + "\n" + "\n".join(blocks)


def format_status(rep: StatusReport) -> str:
    frozen = ("нет" if not rep.freezes
              else "; ".join(f"{scope} — {reason}" for scope, reason in rep.freezes))
    return (f"📋 Статус\n"
            f"Портфель: {_money(rep.portfolio_equity)}  "
            f"(сегодня {rep.today_pnl:+,.2f}, {rep.today_pct:+.1%})\n"
            f"Открытых позиций: {rep.open_positions_count}\n"
            f"Заморозки: {frozen}")


def format_trades(rep: TradesReport) -> str:
    if not rep.rows:
        return "🧾 Последние сделки\nсделок нет"
    lines = [f"  • {r.ts[:10]} {r.agent_id} {intent_label(r.intent)} "
             f"{r.quantity} {r.symbol} @ {_money(r.price)}" for r in rep.rows]
    return "🧾 Последние сделки\n" + "\n".join(lines)
