from trading.edge.metrics import hit_rate, t_statistic, calibration


def test_hit_rate_ignores_zero_signal():
    signals = [2.0, -2.0, 0.0, 3.0]
    realized = [0.01, 0.01, -0.5, 0.02]   # zero-signal row excluded entirely
    # up&+ = hit, down&+ = miss, (skip), up&+ = hit -> 2/3.
    assert abs(hit_rate(signals, realized) - 2.0 / 3.0) < 1e-9


def test_t_statistic_positive_for_consistently_positive():
    assert t_statistic([0.01, 0.012, 0.011, 0.009]) > 2.0


def test_t_statistic_zero_for_too_few():
    assert t_statistic([0.01]) == 0.0


def test_calibration_buckets_by_confidence():
    confidences = [0.2, 0.4, 0.8, 0.9]
    signals = [1.0, 1.0, 1.0, 1.0]
    realized = [-0.01, -0.01, 0.01, 0.01]   # low-conf wrong, high-conf right
    buckets = calibration(confidences, signals, realized, edges=[0.0, 0.5, 1.0])
    # [0.0,0.5): 2 items, 0 hits; [0.5,1.0]: 2 items, 2 hits.
    assert buckets[0] == (0.0, 0.5, 0.0, 2)
    assert buckets[1] == (0.5, 1.0, 1.0, 2)
