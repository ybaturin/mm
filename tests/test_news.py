from trading.data.news import (FakeNews, Headline, _parse_item, collect_news)


def test_fake_news_returns_configured_headlines():
    src = FakeNews({"AAPL": [Headline("AAPL", "iPhone day", "Reuters", "2026-06-13")]})
    assert src.headlines("AAPL")[0].title == "iPhone day"
    assert src.headlines("MSFT") == []


def test_collect_news_swallows_per_symbol_errors():
    class Boom:
        def headlines(self, symbol, as_of_date=None):
            if symbol == "BAD":
                raise RuntimeError("network down")
            return [Headline(symbol, "ok", "X", "2026-06-13")]

    got = collect_news(Boom(), ["AAPL", "BAD"], as_of_date="2026-06-14")
    assert [h.title for h in got["AAPL"]] == ["ok"]
    assert got["BAD"] == []                       # error degraded to empty, no raise


def test_parse_item_handles_new_yfinance_schema():
    item = {"content": {"title": "Earnings beat",
                        "provider": {"displayName": "Bloomberg"},
                        "pubDate": "2026-06-13T20:00:00Z"}}
    h = _parse_item("AAPL", item)
    assert h == Headline("AAPL", "Earnings beat", "Bloomberg", "2026-06-13")


def test_parse_item_handles_legacy_schema():
    item = {"title": "Old style", "publisher": "WSJ",
            "providerPublishTime": 1781726400}   # 2026-06-17 UTC
    h = _parse_item("AAPL", item)
    assert h.title == "Old style" and h.publisher == "WSJ"
    assert h.published_date == "2026-06-17"


def test_parse_item_returns_none_without_title():
    assert _parse_item("AAPL", {"content": {"provider": {"displayName": "X"}}}) is None
