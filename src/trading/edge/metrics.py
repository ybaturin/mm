from __future__ import annotations

import math


def _ranks(xs: list[float]) -> list[float]:
    """Average ranks (1-based), tied values share the mean of their positions."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # average of 1-based positions i+1..j+1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    return cov / math.sqrt(vx * vy)


def information_coefficient(signals: list[float], realized: list[float]) -> float:
    """Spearman rank correlation between predicted signal and realized return.
    0.0 when fewer than 2 points or a degenerate (constant) series."""
    if len(signals) < 2 or len(signals) != len(realized):
        return 0.0
    return _pearson(_ranks(signals), _ranks(realized))


def long_short_spread(signals: list[float], realized: list[float],
                      cost_bps: float = 10.0, frac: float = 0.2) -> float:
    """Mean realized return of the top-`frac` signals (long) minus the bottom-`frac`
    (short), net of trading costs. This is the "how much money" metric.

    Costs: `cost_bps` is one-way per leg in basis points. The portfolio holds a long
    and a short, each a round trip (entry + exit), so total cost = 4 * cost_bps/10_000.
    """
    n = len(signals)
    if n == 0 or n != len(realized):
        return 0.0
    pairs = sorted(zip(signals, realized), key=lambda p: p[0], reverse=True)
    k = max(1, int(n * frac))
    longs = [r for _, r in pairs[:k]]
    shorts = [r for _, r in pairs[-k:]]
    gross = sum(longs) / len(longs) - sum(shorts) / len(shorts)
    cost = 4.0 * (cost_bps / 10_000.0)
    return gross - cost


def _hit(signal: float, realized: float) -> bool:
    return (signal > 0 and realized > 0) or (signal < 0 and realized < 0)


def hit_rate(signals: list[float], realized: list[float]) -> float:
    """Fraction of non-zero signals whose direction matched the realized sign.
    0.0 when there are no directional signals."""
    pairs = [(s, r) for s, r in zip(signals, realized) if s != 0]
    if not pairs:
        return 0.0
    return sum(1 for s, r in pairs if _hit(s, r)) / len(pairs)


def t_statistic(values: list[float]) -> float:
    """One-sample t-stat of the mean against zero. 0.0 for fewer than 2 points or no
    variation."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var == 0:
        return 0.0
    return mean / math.sqrt(var / n)


def calibration(confidences: list[float], signals: list[float],
                realized: list[float],
                edges: list[float]) -> list[tuple[float, float, float, int]]:
    """Hit rate per confidence bucket. `edges` are bucket boundaries (e.g. [0,0.5,1]).
    Each bucket [low, high) — the last is closed on the right. Returns
    (low, high, hit_rate, count) per bucket. A monotonic rise = the model is calibrated.
    """
    out: list[tuple[float, float, float, int]] = []
    for b in range(len(edges) - 1):
        low, high = edges[b], edges[b + 1]
        last = b == len(edges) - 2
        idx = []
        for i, c in enumerate(confidences):
            in_bucket = (low <= c <= high) if last else (low <= c < high)
            if in_bucket:
                idx.append(i)
        if not idx:
            out.append((low, high, 0.0, 0))
            continue
        hits = sum(1 for i in idx if _hit(signals[i], realized[i]))
        out.append((low, high, hits / len(idx), len(idx)))
    return out
