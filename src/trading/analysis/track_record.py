"""Track-record metrics and the go-live gate.

Pure functions over an equity curve (a list of equity values, oldest first). The gate
decides whether an agent has earned real money: a long-enough forward record that beats
SPY on risk-adjusted return while keeping drawdown within the profile limit. Spec §12.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

TRADING_DAYS = 252


def daily_returns(curve: list[float]) -> list[float]:
    """Period-over-period fractional returns. Skips a step if the prior value is <= 0."""
    out: list[float] = []
    for prev, cur in zip(curve, curve[1:]):
        if prev <= 0:
            continue
        out.append(cur / prev - 1.0)
    return out


def sharpe(returns: list[float], periods_per_year: int = TRADING_DAYS) -> float:
    """Annualized Sharpe (risk-free 0). 0.0 when there is no variation or too few points."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def max_drawdown(curve: list[float]) -> float:
    """Largest peak-to-trough decline as a fraction of the peak (0.0..1.0)."""
    peak = float("-inf")
    worst = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def total_return(curve: list[float]) -> float:
    if len(curve) < 2 or curve[0] <= 0:
        return 0.0
    return curve[-1] / curve[0] - 1.0


@dataclass(frozen=True)
class GoLiveResult:
    cleared: bool
    agent_sharpe: float
    spy_sharpe: float
    agent_max_drawdown: float
    reasons: list[str] = field(default_factory=list)


def evaluate_go_live(agent_curve: list[float], spy_curve: list[float],
                     max_drawdown_pct: float, min_days: int = 126) -> GoLiveResult:
    """Gate for flipping an agent to real money. Cleared only if ALL hold:
    enough forward days, Sharpe strictly beats SPY, drawdown within the profile limit."""
    reasons: list[str] = []
    if len(agent_curve) < min_days:
        reasons.append(
            f"only {len(agent_curve)} days of track record, need {min_days}")

    a_sharpe = sharpe(daily_returns(agent_curve))
    s_sharpe = sharpe(daily_returns(spy_curve))
    if a_sharpe <= s_sharpe:
        reasons.append(
            f"Sharpe {a_sharpe:.2f} does not beat SPY {s_sharpe:.2f}")

    a_dd = max_drawdown(agent_curve)
    if a_dd > max_drawdown_pct:
        reasons.append(
            f"max drawdown {a_dd:.1%} exceeds limit {max_drawdown_pct:.1%}")

    return GoLiveResult(cleared=not reasons, agent_sharpe=a_sharpe, spy_sharpe=s_sharpe,
                        agent_max_drawdown=a_dd, reasons=reasons)
