from __future__ import annotations

from datetime import date

from trading.broker.types import Fill
from trading.domain import Intent, TradeProposal
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
    """Green dot for a gain, red for a loss, neutral white for a flat (rounds-to-zero) result."""
    if round(value) == 0:
        return "⚪"
    return "🟢" if value > 0 else "🔴"


def money_signed(value: float) -> str:
    """Signed whole-dollar amount, but no sign for a flat result: '+800$', '-420$', '0$'."""
    return "0$" if round(value) == 0 else f"{value:+,.0f}$"


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    """Russian plural: forms = (one, few, many). E.g. (1,'день') (2,'дня') (5,'дней')."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def human_horizon(days: int) -> str:
    """Render a horizon in days as a human phrase, in the accusative case — it is always
    read after 'за' ('за 3 дня', 'за ~1 неделю', 'за ~2 недели')."""
    if days < 6:
        return f"{days} {_plural(days, ('день', 'дня', 'дней'))}"
    if days <= 9:
        return "~1 неделю"
    if days <= 24:
        w = round(days / 7)
        return f"~{w} {_plural(w, ('неделю', 'недели', 'недель'))}"
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
    qty = decision.quantity
    ref = proposal.reference_price
    notional = qty * ref
    verb = "Купить" if proposal.intent in (Intent.OPEN_LONG, Intent.CLOSE_SHORT) else "Продать"
    head = f"❓ <b>{verb} {html_escape(proposal.symbol)}?</b> — {html_escape(proposal.agent_id)}"

    what = (f"<b>Что:</b> {intent_label(proposal.intent.value).lower()} {qty} × "
            f"{html_escape(proposal.symbol)} по ~${ref:,.2f}  (≈ ${notional:,.0f})")
    why = f"<b>Зачем:</b> {html_escape(proposal.rationale)}"
    lines = [head, "", what, why]

    if proposal.target_price is not None and proposal.horizon_days is not None:
        tgt = proposal.target_price
        # Profit if the forecast lands: longs gain as price rises, shorts as it falls.
        if proposal.intent.is_short_side:
            profit = (ref - tgt) * qty
            pct = (ref - tgt) / ref if ref else 0.0
        else:
            profit = (tgt - ref) * qty
            pct = (tgt - ref) / ref if ref else 0.0
        lines.append(f"<b>Цель:</b> {pnl_color(profit)} ${tgt:,.2f} за "
                     f"{human_horizon(proposal.horizon_days)}")
        lines.append(f"        ожидаемая прибыль {pct:+.1%}  (≈ {profit:+,.0f}$)")

    if proposal.stop_loss_price is not None:
        stop = proposal.stop_loss_price
        if proposal.intent.is_short_side:
            loss = (ref - stop) * qty
            stop_pct = (ref - stop) / ref if ref else 0.0
        else:
            loss = (stop - ref) * qty
            stop_pct = (stop - ref) / ref if ref else 0.0
        lines.append(f"<b>Риск:</b> 🔴 стоп ${stop:,.2f}  ({stop_pct:+.1%}, ≈ {loss:+,.0f}$)")

    return "\n".join(lines)


def format_fill(agent_id: str, fill: Fill) -> str:
    action = _ACTION_RU.get(fill.action.value, fill.action.value)
    notional = fill.quantity * fill.price
    return (f"✅ <b>{html_escape(agent_id)}</b>: {action.lower()} {fill.quantity} × "
            f"{html_escape(fill.symbol)} @ {fill.price:,.2f} (≈ {notional:,.0f}$)")


def format_digest(agent_id: str, date: str, executed: list[str],
                  rejected: int, vetoed: int, declined: int = 0) -> str:
    body = "сделок нет" if not executed else " · ".join(executed)
    skipped = f"  (откл. {rejected} · вето {vetoed} · вручную {declined})" \
        if (rejected or vetoed or declined) else ""
    return f"📊 <b>{html_escape(agent_id)}</b> {date}: {body}{skipped}"


def format_alert(kind: str, detail: str) -> str:
    return f"⚠️ [{kind}] {detail}"


def format_pnl(agent_id: str, start: float, end: float) -> str:
    pnl = end - start
    pct = (pnl / start) if start else 0.0
    return (f"💰 <b>{html_escape(agent_id)}</b>: {end:,.0f}$ · "
            f"{pnl_color(pnl)} {money_signed(pnl)} ({pct:+.1%})")


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


def _group_header(agent_id: str) -> str:
    """Bold, normal-text group header (color/emoji allowed here, unlike inside <pre>)."""
    return f"━ <b>{html_escape(agent_id.upper())}</b> " + "━" * 18


_SIGNED_BUYS = {"open_long", "close_short"}


def format_pnl_report(rep: PnlReport) -> str:
    bench = ""
    if rep.benchmark_pct is not None:
        verdict = "обыгрываем" if rep.portfolio_pct >= rep.benchmark_pct else "отстаём"
        bench = f" · SPY {rep.benchmark_pct:+.1%} ({verdict})"
    head = (f"💰 <b>P&amp;L за {_PERIOD_RU.get(rep.period, rep.period)}</b>\n"
            f"Портфель: {pnl_color(rep.portfolio_pnl)} {money_signed(rep.portfolio_pnl)} "
            f"({rep.portfolio_pct:+.1%}){bench}")
    if not rep.per_agent:
        return head + "\nнет данных"
    lines = [head]
    for l in rep.per_agent:
        lines.append(f"{pnl_color(l.pnl)} <b>{html_escape(l.agent_id)}</b> "
                     f"{money_signed(l.pnl)} ({l.pct:+.1%})")
    return "\n".join(lines)


def format_positions(rep: PositionsReport) -> str:
    total = rep.portfolio_market_value + rep.portfolio_cash
    pnl_tail = (f" · P&amp;L {pnl_color(rep.portfolio_unrealized)} "
                f"{money_signed(rep.portfolio_unrealized)}") if round(rep.portfolio_unrealized) else ""
    blocks = [f"📦 <b>Позиции</b> · вложено {rep.portfolio_market_value:,.0f}$ "
              f"из {total:,.0f}${pnl_tail}"]
    for agent_id, lines in rep.per_agent.items():
        invested = sum(l.current_price * l.quantity for l in lines)
        free = rep.per_agent_cash.get(agent_id, 0.0)
        blocks.append("")
        blocks.append(f"<b>{html_escape(agent_id.upper())}</b> · вложено {invested:,.0f}$ "
                      f"из {invested + free:,.0f}$")
        if not lines:
            blocks.append("позиций нет")
            continue
        # Aligned monospace table. A header row goes first so Telegram's </> copy button
        # overlaps the labels, not the data. Direction is the sign of the qty column.
        # NB: no '&' in any cell — html_escape turns it into '&amp;', whose 5-char length
        # would mis-size the column (Telegram renders it as one char), so columns drift.
        table = [["тикер", "кол", "цена", "цель", "PnL", "дн"]]
        for l in lines:
            tgt = f"{l.target_price:,.0f}" if l.target_price is not None else "—"
            if l.days_left is None:
                dn = "—"
            elif l.days_left < 0:
                dn = "проср"
            else:
                dn = str(l.days_left)
            table.append([l.symbol, f"{l.quantity:+d}", f"{l.current_price:,.2f}",
                          tgt, money_signed(l.unrealized_pnl), dn])
        blocks.append(mono_table(table, aligns="lrrrrr"))
    return "\n".join(blocks) + "\n\n+ лонг · − шорт · цель — целевая цена · дн — дней до срока"


def format_status(rep: StatusReport) -> str:
    frozen = ("нет" if not rep.freezes
              else "; ".join(f"{scope} — {reason}" for scope, reason in rep.freezes))
    return (f"📋 <b>Статус</b> · {rep.portfolio_equity:,.0f}$ "
            f"(сегодня {pnl_color(rep.today_pnl)} {money_signed(rep.today_pnl)}, {rep.today_pct:+.1%})\n"
            f"Позиций: {rep.open_positions_count} · заморозки: {frozen}")


def format_trades(rep: TradesReport) -> str:
    if not rep.rows:
        return "🧾 <b>Последние сделки</b>\nсделок нет"
    by_agent: dict[str, list] = {}
    for r in rep.rows:
        by_agent.setdefault(r.agent_id, []).append(r)
    blocks = ["🧾 <b>Последние сделки</b>"]
    for aid, rows in by_agent.items():
        # A header row goes first so Telegram's code-block </> copy button overlaps the
        # labels, not the data on the first trade row. The PnL column carries the realized
        # result on closing (sell) rows; opening (buy) rows show a dash.
        table_rows = [["дата", "к-во", "тикер", "цена", "PnL"]]
        for r in rows:
            pnl = "—" if r.realized_pnl is None else money_signed(r.realized_pnl)
            table_rows.append([f"{r.ts[8:10]}.{r.ts[5:7]}",            # DD.MM from ISO ts
                               (f"+{r.quantity}" if r.intent in _SIGNED_BUYS else f"−{r.quantity}"),
                               r.symbol, f"{r.price:,.2f}", pnl])
        blocks.append(_group_header(aid))
        blocks.append(mono_table(table_rows, aligns="lllrr"))
    return "\n".join(blocks) + "\n\n+N — купил, −N — продал · PnL по закрытым"


def format_retro(agent_id: str, symbol: str, quantity: int, entry_price: float,
                 exit_price: float, target_price: float, horizon_days: int,
                 opened_on: str, closed_on: str, is_short: bool) -> str:
    """Pushed when a forecasted position fully closes: forecast vs actual."""
    if is_short:
        realized = (entry_price - exit_price) * quantity
        actual_pct = (entry_price - exit_price) / entry_price if entry_price else 0.0
        expected_pct = (entry_price - target_price) / entry_price if entry_price else 0.0
        path = ((entry_price - exit_price) / (entry_price - target_price)
                if entry_price != target_price else 0.0)
    else:
        realized = (exit_price - entry_price) * quantity
        actual_pct = (exit_price - entry_price) / entry_price if entry_price else 0.0
        expected_pct = (target_price - entry_price) / entry_price if entry_price else 0.0
        path = ((exit_price - entry_price) / (target_price - entry_price)
                if target_price != entry_price else 0.0)
    used = (date.fromisoformat(closed_on) - date.fromisoformat(opened_on)).days
    return (
        f"🏁 <b>Закрыта позиция</b> · {html_escape(agent_id)}\n"
        f"{html_escape(symbol)} ×{quantity} — итог {pnl_color(realized)} "
        f"{money_signed(realized)} ({actual_pct:+.1%})\n\n"
        f"<b>Прогноз был:</b>  {expected_pct:+.1%} за {human_horizon(horizon_days)}\n"
        f"<b>По факту:</b>     {actual_pct:+.1%}, дошли на {max(0.0, path):.0%} пути\n"
        f"<b>Срок:</b>         закрыто на {used}-й день из ~{horizon_days}"
    )
