from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class EdgePrediction(BaseModel):
    """The strict shape the model must return for one earnings event.

    The horizon is fixed by configuration (not chosen by the model), so it is not a
    field here — it is stamped by the runner. `magnitude_pct` is the expected absolute
    move vs the market, in percent (always >= 0); sign comes from `direction`.
    """
    direction: Literal["up", "down", "neutral"]
    magnitude_pct: float
    confidence: float
    rationale: str


class MemoryProbe(BaseModel):
    """Did the model already know how this stock moved after this report? If so the
    event is not out-of-sample and must be dropped."""
    knows_outcome: bool
    evidence: str


def signal_value(direction: str, magnitude_pct: float) -> float:
    """Signed expected move used as the ranking signal in metrics. Neutral -> 0."""
    if direction == "up":
        return magnitude_pct
    if direction == "down":
        return -magnitude_pct
    return 0.0
