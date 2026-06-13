from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from trading.persistence.journal import JournalRepository

_LOOKBACK_DAYS = {"day": 1, "week": 7, "month": 30}


@dataclass(frozen=True)
class PnlLine:
    agent_id: str
    start_equity: float
    end_equity: float
    pnl: float
    pct: float


@dataclass(frozen=True)
class PnlReport:
    period: str
    per_agent: list[PnlLine]
    portfolio_start: float
    portfolio_end: float
    portfolio_pnl: float
    portfolio_pct: float


def _baseline_equity(curve: list[tuple[str, float]], period: str) -> float:
    """Equity at the period's start: the snapshot on-or-before (last_date - N days),
    or the earliest snapshot when none qualifies / period == 'all'."""
    if period == "all":
        return curve[0][1]
    cutoff = date.fromisoformat(curve[-1][0]) - timedelta(days=_LOOKBACK_DAYS[period])
    baseline = curve[0][1]
    for d, e in curve:
        if date.fromisoformat(d) <= cutoff:
            baseline = e
        else:
            break
    return baseline


def pnl_report(journal: JournalRepository, agent_ids: list[str], period: str) -> PnlReport:
    per_agent: list[PnlLine] = []
    p_start = p_end = 0.0
    for aid in agent_ids:
        curve = journal.equity_curve(aid)
        if not curve:
            continue
        start_eq = _baseline_equity(curve, period)
        end_eq = curve[-1][1]
        pnl = end_eq - start_eq
        pct = pnl / start_eq if start_eq else 0.0
        per_agent.append(PnlLine(aid, start_eq, end_eq, pnl, pct))
        p_start += start_eq
        p_end += end_eq
    p_pnl = p_end - p_start
    p_pct = p_pnl / p_start if p_start else 0.0
    return PnlReport(period, per_agent, p_start, p_end, p_pnl, p_pct)
