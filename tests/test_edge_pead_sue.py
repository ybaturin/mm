from trading.edge.sue import surprise, sue_by_price, sue_by_sigma, prior_surprises


def test_surprise_is_actual_minus_consensus():
    assert surprise(2.01, 1.94) == 2.01 - 1.94
    assert surprise(None, 1.9) is None
    assert surprise(2.0, None) is None


def test_sue_by_price_scales_by_price():
    assert abs(sue_by_price(0.07, 140.0) - 0.0005) < 1e-12
    assert sue_by_price(0.07, 0.0) is None
    assert sue_by_price(None, 140.0) is None


def test_sue_by_sigma_needs_enough_priors():
    assert sue_by_sigma(0.10, [0.01, -0.02, 0.03, 0.00]) is not None
    assert sue_by_sigma(0.10, [0.01, 0.02]) is None          # < 4 priors
    assert sue_by_sigma(0.10, [0.05, 0.05, 0.05, 0.05]) is None  # zero std


def test_sue_by_sigma_value():
    # priors std (sample) of [0,2,-2,0] = 1.632993...; surprise 1.0 -> ~0.6124
    val = sue_by_sigma(1.0, [0.0, 2.0, -2.0, 0.0])
    assert abs(val - (1.0 / 1.632993161855452)) < 1e-9


def test_prior_surprises_point_in_time_most_recent_first():
    series = [
        {"report_date": "2026-04-30", "eps_actual": 2.0, "eps_consensus": 1.9},
        {"report_date": "2026-01-29", "eps_actual": 2.8, "eps_consensus": 2.7},
        {"report_date": "2025-10-30", "eps_actual": 1.8, "eps_consensus": 1.9},
    ]
    # priors strictly before 2026-04-30, newest first
    out = prior_surprises(series, before_date="2026-04-30", limit=8)
    assert [round(x, 4) for x in out] == [round(2.8 - 2.7, 4), round(1.8 - 1.9, 4)]
