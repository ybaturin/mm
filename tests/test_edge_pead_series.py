from trading.edge.sources import parse_av_earnings_series


def test_parse_series_keeps_all_quarters_with_eps():
    payload = {"quarterlyEarnings": [
        {"reportedDate": "2026-04-30", "reportedEPS": "2.01", "estimatedEPS": "1.94"},
        {"reportedDate": "2026-01-29", "reportedEPS": "2.84", "estimatedEPS": "2.67"},
        {"reportedDate": "2025-10-30", "reportedEPS": "None", "estimatedEPS": "1.77"},
    ]}
    rows = parse_av_earnings_series(payload)
    assert len(rows) == 3
    assert rows[0] == {"report_date": "2026-04-30", "eps_actual": 2.01,
                       "eps_consensus": 1.94}
    assert rows[2]["eps_actual"] is None   # 'None' string -> None


def test_parse_series_empty_on_throttle():
    assert parse_av_earnings_series({"Information": "premium"}) == []
