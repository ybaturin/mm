from trading.edge.events import EarningsEvent, select_post_cutoff


def _ev(symbol, report_date):
    return EarningsEvent(symbol=symbol, report_date=report_date,
                         decision_date=report_date)


def test_keeps_only_events_on_or_after_earliest():
    events = [_ev("A", "2026-01-10"), _ev("B", "2026-02-15"), _ev("C", "2026-05-01")]
    kept = select_post_cutoff(events, earliest_report_date="2026-02-01")
    assert [e.symbol for e in kept] == ["B", "C"]


def test_empty_when_all_before_cutoff():
    events = [_ev("A", "2025-12-31")]
    assert select_post_cutoff(events, earliest_report_date="2026-02-01") == []
