from trading.edge.events import EarningsEvent
from trading.edge.pead_study import split_events_by_date, build_records


def _ev(symbol, report_date, actual, consensus):
    return EarningsEvent(symbol=symbol, report_date=report_date,
                         decision_date=report_date, eps_actual=actual,
                         eps_consensus=consensus)


def test_split_by_date_partitions_train_test():
    evs = [_ev("A", "2026-02-10", 1, 1), _ev("B", "2026-04-10", 1, 1)]
    train, test = split_events_by_date(evs, split_date="2026-03-15")
    assert [e.symbol for e in train] == ["A"]
    assert [e.symbol for e in test] == ["B"]


def _ramp(symbol, start, end):
    from datetime import date, timedelta
    from trading.data.bars import Bar
    d0 = date.fromisoformat(start)
    out = []
    for i in range(40):
        day = (d0 + timedelta(days=i)).isoformat()
        close = 100.0 if symbol == "SPY" else 100.0 + i
        out.append(Bar(day, close, close, close, close, 0))
    return out


def test_build_records_computes_signal_and_realized():
    ev = _ev("NVDA", "2026-02-20", 5.0, 4.0)   # surprise +1.0
    recs = build_records([ev], tier="large", horizon=5, normalization="price",
                         price_of=lambda s, d: 100.0, earnings_series_of=lambda s: [],
                         fetch_window=_ramp)
    assert len(recs) == 1
    assert recs[0].symbol == "NVDA"
    assert recs[0].tier == "large"
    assert recs[0].signal == 1.0 / 100.0          # SUE by price
    assert recs[0].realized is not None and recs[0].realized > 0


def test_build_records_drops_when_signal_or_realized_missing():
    ev = _ev("X", "2026-02-20", None, 4.0)        # no EPS -> no signal
    recs = build_records([ev], tier="small", horizon=5, normalization="price",
                         price_of=lambda s, d: 100.0, earnings_series_of=lambda s: [],
                         fetch_window=lambda s, a, b: [])
    assert recs == []
