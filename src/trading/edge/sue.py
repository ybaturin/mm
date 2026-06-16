from __future__ import annotations

import math


def surprise(eps_actual: float | None, eps_consensus: float | None) -> float | None:
    """Raw earnings surprise. None if either input is missing."""
    if eps_actual is None or eps_consensus is None:
        return None
    return eps_actual - eps_consensus


def sue_by_price(surprise_val: float | None, price: float) -> float | None:
    """Surprise scaled by share price — robust, needs no history. None if unusable."""
    if surprise_val is None or price <= 0:
        return None
    return surprise_val / price


def sue_by_sigma(surprise_val: float | None,
                 prior_surprises: list[float]) -> float | None:
    """Classic SUE: surprise over the sample std of prior surprises. Needs >= 4 priors
    and non-zero std, else None."""
    if surprise_val is None or len(prior_surprises) < 4:
        return None
    n = len(prior_surprises)
    mean = sum(prior_surprises) / n
    var = sum((x - mean) ** 2 for x in prior_surprises) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return surprise_val / std


def prior_surprises(series: list[dict], before_date: str,
                    limit: int = 8) -> list[float]:
    """Surprises of rows reported strictly before `before_date`, newest first, capped
    at `limit`. `series` rows have report_date / eps_actual / eps_consensus. Skips rows
    with missing EPS. Point-in-time: never peeks at the event itself or later."""
    rows = [r for r in series if r.get("report_date", "") < before_date]
    rows.sort(key=lambda r: r["report_date"], reverse=True)
    out: list[float] = []
    for r in rows[:limit]:
        s = surprise(r.get("eps_actual"), r.get("eps_consensus"))
        if s is not None:
            out.append(s)
    return out
