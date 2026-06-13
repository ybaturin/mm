from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

VALID_VETO_RULES = {"any", "majority"}


@dataclass(frozen=True)
class RiskProfile:
    name: str
    budget: float
    max_position_pct: float
    min_positions: int
    allow_shorts: bool
    stop_loss_pct: float
    max_trades_per_day: int
    daily_loss_limit_pct: float
    max_drawdown_pct: float
    auto_exec_threshold_usd: float
    auto_exec_threshold_pct: float
    veto_rule: str
    mandate: str = ""

    def __post_init__(self) -> None:
        if self.veto_rule not in VALID_VETO_RULES:
            raise ValueError(
                f"veto_rule must be one of {VALID_VETO_RULES}, got {self.veto_rule!r}"
            )


def load_profiles(path: str | Path) -> dict[str, RiskProfile]:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return {name: RiskProfile(name=name, **values) for name, values in raw.items()}
