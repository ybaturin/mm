from __future__ import annotations

import sqlite3

from trading.edge.benchmarks import COIN_FLIP_HIT_RATE
from trading.edge.metrics import (calibration, hit_rate, information_coefficient,
                                   long_short_spread, t_statistic)
from trading.edge.schema import signal_value


def build_report(rows: list[sqlite3.Row]) -> str:
    """Human-readable edge report from scored, blind prediction rows. Pure: takes
    already-realized rows, computes every metric, compares to benchmarks."""
    n = len(rows)
    lines = ["=== LLM EDGE MEASURER REPORT ===", f"Sample size: {n}"]
    if n < 2:
        lines.append("Result: insufficient data — need more events to conclude.")
        return "\n".join(lines)

    signals = [signal_value(r["direction"], r["magnitude_pct"]) for r in rows]
    realized = [r["realized_return"] for r in rows]

    # Dumb-PEAD baseline signal from EPS surprise on the same rows.
    pead = []
    for r in rows:
        a, c = r["eps_actual"], r["eps_consensus"]
        pead.append(0.0 if a is None or c is None else (1.0 if a > c else -1.0 if a < c else 0.0))

    ic = information_coefficient(signals, realized)
    pead_ic = information_coefficient(pead, realized)
    ls = long_short_spread(signals, realized)
    hr = hit_rate(signals, realized)
    long_returns = [s if (sig := signals[i]) >= 0 else -s
                    for i, s in enumerate(realized)]  # directional P&L per call
    tstat = t_statistic(long_returns)
    cal = calibration([r["confidence"] for r in rows], signals, realized,
                      edges=[0.0, 0.5, 0.75, 1.0])

    lines += [
        f"Information coefficient (LLM): {ic:+.3f}   vs dumb PEAD: {pead_ic:+.3f}",
        f"Long-short spread (after costs): {ls:+.4f}",
        f"Hit rate: {hr:.1%}   vs coin flip: {COIN_FLIP_HIT_RATE:.1%}",
        f"Directional t-statistic: {tstat:+.2f}",
        "Calibration by confidence (low, high, hit-rate, n):",
    ]
    for low, high, rate, count in cal:
        lines.append(f"  [{low:.2f}, {high:.2f}]: {rate:.1%} ({count})")
    lines.append("")
    verdict = ("LLM beats dumb PEAD" if ic > pead_ic and ls > 0
               else "LLM does NOT clear dumb PEAD — deep reading unjustified")
    lines.append(f"Verdict signal: {verdict}")
    lines.append("Caveat: small post-cutoff sample; confirm any edge on a forward window.")
    return "\n".join(lines)
