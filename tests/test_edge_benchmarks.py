from trading.edge.events import EarningsEvent
from trading.edge.benchmarks import dumb_pead_signals, COIN_FLIP_HIT_RATE


def _ev(symbol, actual, consensus):
    return EarningsEvent(symbol=symbol, report_date="2026-02-01",
                         decision_date="2026-02-03",
                         eps_actual=actual, eps_consensus=consensus)


def test_dumb_pead_signs_by_eps_surprise():
    events = [_ev("A", 5.0, 4.0), _ev("B", 3.0, 4.0), _ev("C", 4.0, 4.0)]
    assert dumb_pead_signals(events) == [1.0, -1.0, 0.0]


def test_dumb_pead_zero_when_eps_missing():
    assert dumb_pead_signals([_ev("A", None, 4.0)]) == [0.0]


def test_coin_flip_constant():
    assert COIN_FLIP_HIT_RATE == 0.5
