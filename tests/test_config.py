from pathlib import Path

import pytest
from trading.config import RiskProfile, load_profiles

CONFIG = Path(__file__).resolve().parents[1] / "config" / "profiles.toml"


def test_loads_three_profiles():
    profiles = load_profiles(CONFIG)
    assert set(profiles) == {"conservative", "moderate", "aggressive"}


def test_aggressive_values_match_spec():
    p = load_profiles(CONFIG)["aggressive"]
    assert p.name == "aggressive"
    assert p.budget == 5000.0
    assert p.max_position_pct == 0.40
    assert p.allow_shorts is True
    assert p.stop_loss_pct == 0.12
    assert p.max_trades_per_day == 8
    assert p.daily_loss_limit_pct == 0.08
    assert p.max_drawdown_pct == 0.25
    assert p.veto_rule == "majority"


def test_conservative_disallows_shorts_and_uses_any_veto():
    p = load_profiles(CONFIG)["conservative"]
    assert p.allow_shorts is False
    assert p.veto_rule == "any"


def test_invalid_veto_rule_rejected():
    with pytest.raises(ValueError):
        RiskProfile(
            name="bad", budget=1.0, max_position_pct=0.1, min_positions=1,
            allow_shorts=False, stop_loss_pct=0.1, max_trades_per_day=1,
            daily_loss_limit_pct=0.1, max_drawdown_pct=0.1,
            auto_exec_threshold_usd=1.0, auto_exec_threshold_pct=0.1,
            veto_rule="sometimes",
        )
