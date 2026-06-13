from pathlib import Path

import pytest
from trading.config import RiskProfile, load_profiles

CONFIG = Path(__file__).resolve().parents[1] / "config" / "profiles.toml"


def test_loads_two_profiles():
    profiles = load_profiles(CONFIG)
    assert set(profiles) == {"moderate", "aggressive"}


def test_aggressive_values_match_config():
    p = load_profiles(CONFIG)["aggressive"]
    assert p.name == "aggressive"
    assert p.budget == 2400.0
    assert p.max_position_pct == 0.40
    assert p.allow_shorts is True
    assert p.stop_loss_pct == 0.12
    assert p.max_trades_per_day == 8
    assert p.daily_loss_limit_pct == 0.08
    assert p.max_drawdown_pct == 0.25
    assert p.veto_rule == "majority"


def test_moderate_disallows_shorts():
    p = load_profiles(CONFIG)["moderate"]
    assert p.allow_shorts is False
    assert p.budget == 2400.0


def test_each_profile_has_a_mandate():
    profiles = load_profiles(CONFIG)
    for name, p in profiles.items():
        assert p.mandate, f"{name} is missing a mandate"
    mandates = {p.mandate for p in profiles.values()}
    assert len(mandates) == len(profiles)


def test_mandate_defaults_to_empty_for_manual_construction():
    p = RiskProfile(
        name="x", budget=1.0, max_position_pct=0.1, min_positions=1,
        allow_shorts=False, stop_loss_pct=0.1, max_trades_per_day=1,
        daily_loss_limit_pct=0.1, max_drawdown_pct=0.1,
        auto_exec_threshold_usd=1.0, auto_exec_threshold_pct=0.1, veto_rule="any",
    )
    assert p.mandate == ""


def test_invalid_veto_rule_rejected():
    with pytest.raises(ValueError):
        RiskProfile(
            name="bad", budget=1.0, max_position_pct=0.1, min_positions=1,
            allow_shorts=False, stop_loss_pct=0.1, max_trades_per_day=1,
            daily_loss_limit_pct=0.1, max_drawdown_pct=0.1,
            auto_exec_threshold_usd=1.0, auto_exec_threshold_pct=0.1,
            veto_rule="sometimes",
        )
