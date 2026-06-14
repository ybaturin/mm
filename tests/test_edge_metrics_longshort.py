from trading.edge.metrics import long_short_spread


def test_gross_spread_top_minus_bottom():
    # 5 items, frac 0.2 -> 1 long (top signal) and 1 short (bottom signal).
    signals = [5.0, 4.0, 0.0, -4.0, -5.0]
    realized = [0.03, 0.02, 0.00, -0.01, -0.04]
    # long = realized of signal 5.0 (0.03); short = realized of signal -5.0 (-0.04).
    # gross = 0.03 - (-0.04) = 0.07; costs 0 bps -> 0.07.
    spread = long_short_spread(signals, realized, cost_bps=0.0, frac=0.2)
    assert abs(spread - 0.07) < 1e-9


def test_costs_reduce_spread():
    signals = [5.0, -5.0]
    realized = [0.03, -0.04]
    gross = long_short_spread(signals, realized, cost_bps=0.0, frac=0.5)
    net = long_short_spread(signals, realized, cost_bps=10.0, frac=0.5)
    assert net < gross
    # 10 bps = 0.001 per side; long+short, round trip each -> 4 * 0.001 = 0.004.
    assert abs((gross - net) - 0.004) < 1e-9


def test_empty_returns_zero():
    assert long_short_spread([], [], cost_bps=10.0, frac=0.2) == 0.0
