from trading.edge.metrics import information_coefficient


def test_perfect_rank_agreement_is_one():
    signals = [1.0, 2.0, 3.0, 4.0]
    realized = [0.01, 0.02, 0.03, 0.04]
    assert abs(information_coefficient(signals, realized) - 1.0) < 1e-9


def test_perfect_inverse_is_minus_one():
    signals = [1.0, 2.0, 3.0, 4.0]
    realized = [0.04, 0.03, 0.02, 0.01]
    assert abs(information_coefficient(signals, realized) + 1.0) < 1e-9


def test_handles_ties_via_average_ranks():
    signals = [1.0, 1.0, 2.0, 3.0]
    realized = [0.01, 0.02, 0.03, 0.04]
    ic = information_coefficient(signals, realized)
    assert -1.0 <= ic <= 1.0


def test_too_few_points_is_zero():
    assert information_coefficient([1.0], [0.1]) == 0.0
