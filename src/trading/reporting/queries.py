from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from trading.persistence.accounts import AccountRepository
from trading.persistence.freezes import FreezeStore
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


@dataclass(frozen=True)
class PositionLine:
    agent_id: str
    symbol: str
    quantity: int
    avg_price: float
    current_price: float
    unrealized_pnl: float


@dataclass(frozen=True)
class PositionsReport:
    per_agent: dict[str, list[PositionLine]]
    portfolio_unrealized: float
    portfolio_market_value: float


def positions_report(accounts: AccountRepository, agent_ids: list[str],
                     price_fn: Callable[[str], float]) -> PositionsReport:
    per_agent: dict[str, list[PositionLine]] = {}
    port_unreal = 0.0
    port_mv = 0.0
    for aid in agent_ids:
        state = accounts.get_state(aid)
        lines: list[PositionLine] = []
        if state is not None:
            for p in state.positions:
                price = price_fn(p.symbol)
                unreal = (price - p.avg_price) * p.quantity
                lines.append(PositionLine(aid, p.symbol, p.quantity, p.avg_price,
                                          price, unreal))
                port_unreal += unreal
                port_mv += price * p.quantity
        per_agent[aid] = lines
    return PositionsReport(per_agent, port_unreal, port_mv)


@dataclass(frozen=True)
class StatusReport:
    portfolio_equity: float
    today_pnl: float
    today_pct: float
    open_positions_count: int
    freezes: list[tuple[str, str]]


@dataclass(frozen=True)
class TradeLine:
    ts: str
    agent_id: str
    intent: str
    symbol: str
    quantity: int
    price: float


@dataclass(frozen=True)
class TradesReport:
    rows: list[TradeLine]


def status_report(accounts: AccountRepository, journal: JournalRepository,
                  freezes: FreezeStore, agent_ids: list[str],
                  price_fn: Callable[[str], float]) -> StatusReport:
    total_equity = 0.0
    today_pnl = 0.0
    open_count = 0
    for aid in agent_ids:
        state = accounts.get_state(aid)
        if state is None:
            continue
        prices = {p.symbol: price_fn(p.symbol) for p in state.positions}
        total_equity += state.equity(prices)
        open_count += len(state.positions)
        curve = journal.equity_curve(aid)
        if len(curve) >= 2:
            today_pnl += curve[-1][1] - curve[-2][1]
    base = total_equity - today_pnl
    today_pct = today_pnl / base if base else 0.0
    frozen = [(s, freezes.reason(s) or "") for s in freezes.frozen_scopes()]
    return StatusReport(total_equity, today_pnl, today_pct, open_count, frozen)


def trades_report(journal: JournalRepository, agent_ids: list[str],
                  limit: int = 10) -> TradesReport:
    rows = []
    for aid in agent_ids:
        rows.extend(journal.fills_for(aid))
    rows.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
    lines = [TradeLine(r["ts"], r["agent_id"], r["intent"], r["symbol"],
                       r["quantity"], r["price"]) for r in rows[:limit]]
    return TradesReport(lines)
