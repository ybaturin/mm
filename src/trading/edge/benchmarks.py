from __future__ import annotations

from trading.edge.events import EarningsEvent

COIN_FLIP_HIT_RATE = 0.5


def dumb_pead_signals(events: list[EarningsEvent]) -> list[float]:
    """The mechanical PEAD baseline: +1 if EPS beat consensus, -1 if missed, 0 if in
    line or data missing. If deep reading can't beat this, reading transcripts is
    pointless. Aligned 1:1 with `events`."""
    out: list[float] = []
    for e in events:
        if e.eps_actual is None or e.eps_consensus is None:
            out.append(0.0)
        elif e.eps_actual > e.eps_consensus:
            out.append(1.0)
        elif e.eps_actual < e.eps_consensus:
            out.append(-1.0)
        else:
            out.append(0.0)
    return out
