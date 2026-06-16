from __future__ import annotations

from dataclasses import dataclass

from trading.edge.costs import position_pnl


@dataclass(frozen=True)
class PeadRecord:
    """One event's tradeable observation: the SUE signal and the realized
    market-adjusted forward return, tagged with the symbol's cost tier."""
    symbol: str
    decision_date: str       # YYYY-MM-DD
    tier: str                # 'large' | 'mid' | 'small'
    signal: float            # SUE (signed)
    realized: float          # market-adjusted forward return at the chosen horizon


def long_short_net(records: list[PeadRecord], frac: float = 0.2) -> float:
    """Net long-short spread: top-`frac` signals long, bottom-`frac` short, each
    position costed at its tier. 0.0 for an empty set."""
    n = len(records)
    if n == 0:
        return 0.0
    ranked = sorted(records, key=lambda r: r.signal, reverse=True)
    k = max(1, int(n * frac))
    longs = ranked[:k]
    shorts = ranked[-k:]
    long_pnl = sum(position_pnl(r.realized, r.tier, "long") for r in longs) / len(longs)
    short_pnl = sum(position_pnl(r.realized, r.tier, "short") for r in shorts) / len(shorts)
    return long_pnl + short_pnl


def pnl_series(records: list[PeadRecord]) -> list[tuple[str, float]]:
    """Per-event net P&L (long if signal>0, short if <0; signal==0 skipped), ordered by
    decision_date. Each is a single costed position."""
    out: list[tuple[str, float]] = []
    for r in records:
        if r.signal == 0:
            continue
        side = "long" if r.signal > 0 else "short"
        out.append((r.decision_date, position_pnl(r.realized, r.tier, side)))
    out.sort(key=lambda t: t[0])
    return out


def bucket_returns(series: list[tuple[str, float]]) -> list[float]:
    """Mean P&L per calendar month (YYYY-MM), ordered — a return series for Sharpe /
    drawdown via analysis.track_record."""
    buckets: dict[str, list[float]] = {}
    for date, pnl in series:
        buckets.setdefault(date[:7], []).append(pnl)
    return [sum(v) / len(v) for _, v in sorted(buckets.items())]
