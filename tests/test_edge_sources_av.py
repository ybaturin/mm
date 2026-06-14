from trading.edge.sources import parse_av_earnings, parse_av_transcript


def test_parse_av_earnings_keeps_post_cutoff_and_maps_eps():
    payload = {"quarterlyEarnings": [
        {"reportedDate": "2026-04-30", "reportedEPS": "2.01", "estimatedEPS": "1.94"},
        {"reportedDate": "2025-10-30", "reportedEPS": "1.85", "estimatedEPS": "1.77"},
    ]}
    events = parse_av_earnings(payload, "AAPL", earliest_report_date="2026-02-01",
                               decision_offset_days=2)
    assert len(events) == 1
    e = events[0]
    assert e.symbol == "AAPL"
    assert e.report_date == "2026-04-30"
    assert e.decision_date == "2026-05-02"   # +2 calendar days
    assert e.eps_actual == 2.01
    assert e.eps_consensus == 1.94


def test_parse_av_earnings_handles_missing_eps_and_throttle():
    # 'None' string -> None; a throttle/premium payload (no quarterlyEarnings) -> [].
    payload = {"quarterlyEarnings": [
        {"reportedDate": "2026-03-01", "reportedEPS": "None", "estimatedEPS": None},
    ]}
    e = parse_av_earnings(payload, "X", "2026-02-01", 2)[0]
    assert e.eps_actual is None and e.eps_consensus is None
    assert parse_av_earnings({"Information": "premium"}, "X", "2026-02-01", 2) == []


def test_parse_av_transcript_prefixes_speaker_and_title():
    payload = {"transcript": [
        {"speaker": "Jim Kavanaugh", "title": "CFO", "content": "Margins were strong."},
        {"speaker": "Operator", "title": "", "content": "Next question."},
    ]}
    text = parse_av_transcript(payload)
    assert "Jim Kavanaugh (CFO): Margins were strong." in text
    assert "Operator: Next question." in text


def test_parse_av_transcript_empty_is_blank():
    assert parse_av_transcript({}) == ""
    assert parse_av_transcript({"transcript": []}) == ""
