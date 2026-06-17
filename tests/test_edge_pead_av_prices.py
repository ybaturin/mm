from trading.edge.sources import parse_av_daily


def test_parse_av_daily_ascending_bars():
    payload = {"Time Series (Daily)": {
        "2026-05-02": {"1. open": "10", "2. high": "11", "3. low": "9",
                       "4. close": "10.5", "5. volume": "1000"},
        "2026-05-01": {"1. open": "9", "2. high": "10", "3. low": "8",
                       "4. close": "9.5", "5. volume": "2000"},
    }}
    bars = parse_av_daily(payload)
    assert [b.date for b in bars] == ["2026-05-01", "2026-05-02"]   # ascending
    assert bars[1].close == 10.5
    assert bars[0].volume == 2000


def test_parse_av_daily_empty_on_throttle():
    assert parse_av_daily({"Information": "premium"}) == []
