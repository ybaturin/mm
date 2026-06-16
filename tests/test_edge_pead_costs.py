from trading.edge.costs import ROUND_TRIP_BPS, position_pnl


def test_round_trip_costs_rise_with_illiquidity():
    assert ROUND_TRIP_BPS["large"] < ROUND_TRIP_BPS["mid"] < ROUND_TRIP_BPS["small"]


def test_long_pnl_is_gross_minus_cost():
    # large tier 5 bps = 0.0005; long on +2% gross -> 0.02 - 0.0005
    assert abs(position_pnl(0.02, "large", "long") - (0.02 - 0.0005)) < 1e-12


def test_short_pnl_inverts_gross_then_pays_cost():
    # small tier 60 bps = 0.006; short on +2% gross -> -0.02 - 0.006
    assert abs(position_pnl(0.02, "small", "short") - (-0.02 - 0.006)) < 1e-12


def test_short_profits_when_price_falls():
    # short on -3% gross, large -> +0.03 - 0.0005
    assert abs(position_pnl(-0.03, "large", "short") - (0.03 - 0.0005)) < 1e-12
