from trading.edge.sources import parse_fmp_transcript, parse_fmp_calendar


def test_parse_fmp_transcript_joins_content():
    raw = [{"symbol": "NVDA", "date": "2026-02-21", "content": "CEO: strong quarter."}]
    text = parse_fmp_transcript(raw)
    assert "strong quarter" in text


def test_parse_fmp_transcript_empty_is_blank():
    assert parse_fmp_transcript([]) == ""


def test_parse_fmp_calendar_maps_events():
    raw = [
        {"symbol": "NVDA", "date": "2026-02-21", "epsActual": 5.1, "epsEstimated": 4.6},
        {"symbol": "AAPL", "date": "2026-02-01", "epsActual": None, "epsEstimated": 2.1},
    ]
    events = parse_fmp_calendar(raw, decision_offset_days=2)
    assert events[0].symbol == "NVDA"
    assert events[0].report_date == "2026-02-21"
    assert events[0].decision_date == "2026-02-23"   # +2 calendar days
    assert events[0].eps_actual == 5.1
    assert events[0].eps_consensus == 4.6
    assert events[1].eps_actual is None
