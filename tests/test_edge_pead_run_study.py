from datetime import date, timedelta

from trading.data.bars import Bar
from trading.edge.events import EarningsEvent
from trading.edge.pead_study import Config, run_study


def _ev(symbol, report_date, actual, consensus):
    return EarningsEvent(symbol, report_date, report_date, actual, consensus)


def _ramp(symbol, start, end):
    d0 = date.fromisoformat(start)
    out = []
    for i in range(60):
        day = (d0 + timedelta(days=i)).isoformat()
        close = 100.0 if symbol == "SPY" else 100.0 + i   # beats drift up
        out.append(Bar(day, close, close, close, close, 0))
    return out


def test_run_study_end_to_end_with_fakes():
    # Train events (before split) + test events (after). Positive-surprise names ramp up.
    events = [_ev("A", "2026-02-10", 5.0, 4.0), _ev("B", "2026-02-12", 3.0, 4.0),
              _ev("C", "2026-04-10", 5.0, 4.0), _ev("D", "2026-04-12", 3.0, 4.0)]
    report = run_study(
        events=events, split_date="2026-03-15",
        configs=[Config("large", 5, "price")],
        price_of=lambda s, d: 100.0, earnings_series_of=lambda s: [],
        fetch_window=_ramp,
    )
    assert "MECHANICAL PEAD STUDY REPORT" in report
    assert "PRE-REGISTERED CONFIG" in report
    assert "HELD-OUT TEST" in report
