from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EarningsEvent:
    """One earnings report we may test the model on.

    `report_date` is when results were released (after the close). `decision_date` is
    the trading day we treat as the point-in-time boundary — the model sees only data
    dated on or before it, and a hypothetical position opens at its close.
    `eps_actual`/`eps_consensus` feed the dumb-PEAD benchmark (None if unavailable).
    """
    symbol: str
    report_date: str            # YYYY-MM-DD
    decision_date: str          # YYYY-MM-DD
    eps_actual: float | None = None
    eps_consensus: float | None = None


def select_post_cutoff(events: list[EarningsEvent],
                       earliest_report_date: str) -> list[EarningsEvent]:
    """Keep only events the model is genuinely blind to: report_date on or after
    `earliest_report_date` (set by the caller to the model's knowledge cutoff plus a
    safety buffer). ISO dates compare correctly as strings."""
    return [e for e in events if e.report_date >= earliest_report_date]
