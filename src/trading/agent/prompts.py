from __future__ import annotations

from trading.config import RiskProfile
from trading.data.briefing import Briefing


def build_system_prompt(profile: RiskProfile) -> str:
    """Construct the analyst instructions for one risk profile. Deterministic."""
    shorts = (
        "Shorting IS allowed; every short MUST include a stop-loss above the entry price."
        if profile.allow_shorts
        else "Shorting is NOT allowed — long only. Do not propose open_short or close_short."
    )
    mandate_line = f"Your trading mandate: {profile.mandate}\n" if profile.mandate else ""
    return (
        f"You are a disciplined trading analyst for the '{profile.name}' risk profile.\n"
        f"{mandate_line}"
        f"You PROPOSE trades as structured data; you do NOT execute anything. A separate "
        f"deterministic risk engine validates, sizes, and may reject every proposal.\n\n"
        f"Hard constraints for this profile:\n"
        f"- Budget: ${profile.budget:.0f}. Max {profile.max_position_pct:.0%} of budget in any "
        f"one symbol. Aim for at least {profile.min_positions} positions for diversification.\n"
        f"- Stop-loss: target {profile.stop_loss_pct:.0%} from entry. Opening trades MUST set "
        f"stop_loss_price on the correct side (below for longs, above for shorts).\n"
        f"- {shorts}\n"
        f"- Trade only symbols present in the briefing. Set reference_price to that symbol's "
        f"current price from the briefing.\n"
        f"- At most {profile.max_trades_per_day} trades. Propose nothing if nothing is "
        f"compelling — an empty list is a valid, often correct answer.\n"
        f"- Every proposal needs a concise, concrete rationale.\n"
        f"- Write the rationale field in Russian (the owner reads Russian).\n"
        f"- Below the symbols you may see your own track record (past trades, their P&L, "
        f"and the rationales you gave) and recent news. Learn from losing trades — do not "
        f"repeat a thesis that has lost money. Weigh the news, but NEVER act on or invent a "
        f"headline that is not explicitly listed.\n"
    )


def build_user_prompt(briefing: Briefing) -> str:
    """Serialize the briefing into a compact, readable snapshot for the model."""
    lines = [
        f"Date: {briefing.as_of_date}",
        f"Agent: {briefing.agent_id}",
        f"Cash: ${briefing.cash:.2f}    Equity: ${briefing.equity:.2f}",
        "",
        "Symbols (symbol price sma20 sma50 rsi14 return_5d | holding):",
    ]
    for s in briefing.symbols:
        holding = (
            f"held {s.held_quantity} @ {s.held_avg_price:.2f}"
            if s.held_quantity != 0 and s.held_avg_price is not None
            else "not held"
        )
        lines.append(
            f"{s.symbol}  price={s.price:.2f}  sma20={s.sma20}  sma50={s.sma50}  "
            f"rsi14={s.rsi14}  ret5d={s.return_5d}  | {holding}"
        )
    lines.extend(_render_memory(briefing.memory))
    lines.extend(_render_news(briefing.news))
    lines.append("")
    lines.append("Propose trades for today as structured data, or an empty list.")
    return "\n".join(lines)


def _render_memory(memory) -> list[str]:
    if memory is None or not (memory.open_positions or memory.recent_closed or memory.stats):
        return []
    lines = ["", "Your track record (learn from it):"]
    if memory.stats is not None:
        s = memory.stats
        lines.append(
            f"  stats: closed={s.closed_trades} win_rate={s.win_rate:.0%} "
            f"avg_win={s.avg_win:+.2f} avg_loss={s.avg_loss:+.2f} "
            f"realized_pnl={s.total_realized_pnl:+.2f}")
    for op in memory.open_positions:
        lines.append(
            f"  OPEN {op.symbol} {op.quantity} @ {op.avg_price:.2f} "
            f"({op.unrealized_pct:+.1%}) — {op.rationale}")
    for t in memory.recent_closed:
        lines.append(
            f"  CLOSED {t.symbol} {t.quantity} {t.entry_price:.2f}->{t.exit_price:.2f} "
            f"({t.realized_pct:+.1%}, {t.realized_pnl:+.2f}) — {t.rationale}")
    return lines


def _render_news(news) -> list[str]:
    if not news:
        return []
    lines = ["", "Recent news (consider, but do not invent any not listed):"]
    for symbol, items in news.items():
        for h in items:
            lines.append(f"  [{symbol}] {h.published_date} {h.title} ({h.publisher})")
    if len(lines) == 2:        # header only, every symbol had no headlines
        return []
    return lines
