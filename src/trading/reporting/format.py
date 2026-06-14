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


def html_escape(s: str) -> str:
    """Neutralize the three characters Telegram's HTML parse_mode treats as markup."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def pnl_color(value: float) -> str:
    """Green dot for non-negative money, red for negative."""
    return "🟢" if value >= 0 else "🔴"


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    """Russian plural: forms = (one, few, many). E.g. (1,'день') (2,'дня') (5,'дней')."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def human_horizon(days: int) -> str:
    """Render a horizon in days as a human phrase: '3 дня', '~1 неделя', '~2 недели'."""
    if days < 6:
        return f"{days} {_plural(days, ('день', 'дня', 'дней'))}"
    if days <= 9:
        return "~1 неделя"
    if days <= 24:
        w = round(days / 7)
        return f"~{w} {_plural(w, ('неделя', 'недели', 'недель'))}"
    m = round(days / 30)
    return f"~{m} {_plural(m, ('месяц', 'месяца', 'месяцев'))}"


def human_days_left(days: int) -> str:
    """Render days remaining to a horizon. Non-positive means due/overdue."""
    if days < 0:
        return "просрочено"
    if days == 0:
        return "сегодня"
    return f"~{days} дн."


def mono_table(rows: list[list[str]], aligns: str) -> str:
    """Build a width-aligned monospace table wrapped in <pre>. `aligns` is one char per
    column: 'l' left, 'r' right. Cells are HTML-escaped; no emoji inside (breaks width)."""
    if not rows:
        return "<pre></pre>"
    cells = [[html_escape(c) for c in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(cells[0]))]
    out_lines = []
    for row in cells:
        parts = []
        for i, cell in enumerate(row):
            pad = widths[i] - len(cell)
            parts.append(cell + " " * pad if aligns[i] == "l" else " " * pad + cell)
        out_lines.append(" ".join(parts).rstrip())
    # Re-pad to equal visible width so the block reads as a clean rectangle.
    width = max(len(l) for l in out_lines)
    body = "\n".join(l.ljust(width) for l in out_lines)
    return f"<pre>{body}</pre>"


def format_confirmation(proposal: TradeProposal, decision: GuardrailDecision) -> str:
    notional = decision.quantity * proposal.reference_price
    stop = "—" if proposal.stop_loss_price is None else f"${proposal.stop_loss_price:,.2f}"
    intent = _INTENT_RU.get(proposal.intent.value, proposal.intent.value)
    return (
        f"❓ Подтвердить сделку? · {proposal.agent_id}\n"
        f"{intent}: {decision.quantity} × {proposal.symbol} "
        f"@ ~${proposal.reference_price:,.2f}  (≈ ${notional:,.0f})\n"
        f"стоп: {stop}\n"
        f"основание: {proposal.rationale}"
    )


def format_fill(agent_id: str, fill: Fill) -> str:
    action = _ACTION_RU.get(fill.action.value, fill.action.value)
    notional = fill.quantity * fill.price
    return (f"✅ Сделка исполнена · {agent_id}\n"
            f"{action} {fill.quantity} × {fill.symbol} @ ${fill.price:,.2f}  "
            f"(≈ ${notional:,.0f})")


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
