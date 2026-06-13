import pytest
from trading.analysis.track_record import (
    daily_returns,
    evaluate_go_live,
    max_drawdown,
    sharpe,
    total_return,
)


def test_daily_returns_basic():
    assert daily_returns([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])


def test_sharpe_is_zero_for_flat_curve():
    assert sharpe([0.0, 0.0, 0.0]) == 0.0


def test_sharpe_positive_for_steady_gains():
    # consistent positive returns -> positive Sharpe
    assert sharpe([0.01, 0.012, 0.009, 0.011]) > 0


def test_max_drawdown_peak_to_trough():
    # 100 -> 120 -> 90 : worst drawdown is (120-90)/120 = 0.25
    assert max_drawdown([100.0, 120.0, 90.0, 95.0]) == pytest.approx(0.25)


def test_total_return():
    assert total_return([100.0, 130.0]) == pytest.approx(0.30)


def _curve(start, step, n):
    return [start + step * i for i in range(n)]


def _from_returns(start, pattern, n):
    """Compound a repeating daily-return pattern into an n-point equity curve."""
    curve = [start]
    for i in range(n - 1):
        curve.append(curve[-1] * (1.0 + pattern[i % len(pattern)]))
    return curve


STEADY = [0.010, 0.011, 0.009]          # high mean, low volatility -> high Sharpe
CHOPPY = [0.020, -0.018, 0.004]         # lower mean, high volatility -> low Sharpe


def test_go_live_cleared_when_beats_spy_within_drawdown_and_enough_days():
    agent = _from_returns(5000.0, STEADY, 130)   # steady gains, tiny drawdown
    spy = _from_returns(5000.0, CHOPPY, 130)
    result = evaluate_go_live(agent, spy, max_drawdown_pct=0.15, min_days=126)
    assert result.cleared is True
    assert result.reasons == []


def test_go_live_blocked_when_too_few_days():
    agent = _from_returns(5000.0, STEADY, 30)
    spy = _from_returns(5000.0, CHOPPY, 30)
    result = evaluate_go_live(agent, spy, max_drawdown_pct=0.15, min_days=126)
    assert result.cleared is False
    assert any("track record" in r.lower() or "days" in r.lower() for r in result.reasons)


def test_go_live_blocked_when_sharpe_does_not_beat_spy():
    agent = _from_returns(5000.0, CHOPPY, 130)   # weaker risk-adjusted than spy
    spy = _from_returns(5000.0, STEADY, 130)
    result = evaluate_go_live(agent, spy, max_drawdown_pct=0.15, min_days=126)
    assert result.cleared is False
    assert any("spy" in r.lower() for r in result.reasons)


def test_go_live_blocked_when_drawdown_exceeds_limit():
    # big mid-period crash blows the drawdown limit despite ending high
    agent = [5000.0] * 60 + [3000.0] + [6000.0] * 69    # 130 points, ~40% drawdown
    spy = _curve(5000.0, 1.0, 130)
    result = evaluate_go_live(agent, spy, max_drawdown_pct=0.15, min_days=126)
    assert result.cleared is False
    assert any("drawdown" in r.lower() for r in result.reasons)
