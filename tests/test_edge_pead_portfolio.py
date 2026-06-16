from trading.edge.portfolio import PeadRecord, long_short_net, pnl_series, bucket_returns


def _r(date, tier, signal, realized):
    return PeadRecord(symbol="X", decision_date=date, tier=tier,
                      signal=signal, realized=realized)


def test_long_short_net_after_costs():
    # 5 records, frac 0.2 -> 1 long (top signal), 1 short (bottom signal), large tier.
    recs = [_r("2026-02-01", "large", 5.0, 0.03), _r("2026-02-01", "large", 4.0, 0.02),
            _r("2026-02-01", "large", 0.0, 0.00), _r("2026-02-01", "large", -4.0, -0.01),
            _r("2026-02-01", "large", -5.0, -0.04)]
    # long top (signal 5 -> realized 0.03): 0.03-0.0005; short bottom (signal -5 ->
    # realized -0.04): +0.04-0.0005. net = sum = 0.069.
    assert abs(long_short_net(recs, frac=0.2) - (0.03 - 0.0005 + 0.04 - 0.0005)) < 1e-9


def test_pnl_series_sides_by_signal_and_orders_by_date():
    recs = [_r("2026-03-01", "small", 2.0, 0.05), _r("2026-01-01", "small", -1.0, 0.02)]
    series = pnl_series(recs)
    # ordered by date: Jan first (short, gross +0.02 -> -0.02-0.006), then Mar (long).
    assert series[0][0] == "2026-01-01"
    assert abs(series[0][1] - (-0.02 - 0.006)) < 1e-9
    assert abs(series[1][1] - (0.05 - 0.006)) < 1e-9


def test_bucket_returns_means_by_month():
    series = [("2026-01-05", 0.01), ("2026-01-20", 0.03), ("2026-02-10", -0.02)]
    assert bucket_returns(series) == [0.02, -0.02]   # Jan mean 0.02, Feb -0.02
