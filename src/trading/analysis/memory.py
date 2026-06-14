from __future__ import annotations

from trading.analysis.round_trips import compute_round_trips
from trading.data.briefing import Memory, OpenPositionMemory, SelfStats

_OPENING_INTENTS = ("open_long", "open_short")


def build_memory(journal, agent_id, positions, prices, recent_limit: int = 12) -> Memory:
    """Assemble the agent's self-memory from the journal.

    Returns empty/None fields on a cold start (no history) — identical to today's behavior.
    """
    decisions = journal.decisions_for(agent_id)
    rationale_by_decision = {d["id"]: d["rationale"] for d in decisions}
    trips = compute_round_trips(journal.fills_for(agent_id), rationale_by_decision)

    open_positions = [
        OpenPositionMemory(
            symbol=p.symbol, quantity=p.quantity, avg_price=p.avg_price,
            rationale=_latest_open_rationale(decisions, p.symbol),
            unrealized_pct=_unrealized_pct(p, prices.get(p.symbol, p.avg_price)),
        )
        for p in positions
    ]
    return Memory(open_positions=open_positions,
                  recent_closed=trips[-recent_limit:],
                  stats=_stats(trips))


def _latest_open_rationale(decisions, symbol: str) -> str:
    for d in reversed(decisions):
        if d["symbol"] == symbol and d["intent"] in _OPENING_INTENTS:
            return d["rationale"]
    return ""


def _unrealized_pct(position, price: float) -> float:
    if position.avg_price == 0:
        return 0.0
    pct = (price - position.avg_price) / position.avg_price
    return -pct if position.quantity < 0 else pct      # short profits when price falls


def _stats(trips) -> SelfStats | None:
    if not trips:
        return None
    wins = [t for t in trips if t.realized_pnl > 0]
    losses = [t for t in trips if t.realized_pnl < 0]
    return SelfStats(
        closed_trades=len(trips),
        win_rate=len(wins) / len(trips),
        avg_win=round(sum(t.realized_pnl for t in wins) / len(wins), 2) if wins else 0.0,
        avg_loss=round(sum(t.realized_pnl for t in losses) / len(losses), 2) if losses else 0.0,
        total_realized_pnl=round(sum(t.realized_pnl for t in trips), 2),
    )
