from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class Headline:
    symbol: str
    title: str
    publisher: str
    published_date: str          # YYYY-MM-DD, or "" if unknown


class NewsSource(Protocol):
    def headlines(self, symbol: str, as_of_date: str | None = None) -> list[Headline]: ...


class FakeNews:
    """Deterministic news for tests and offline runs. Satisfies NewsSource."""

    def __init__(self, by_symbol: dict[str, list[Headline]] | None = None) -> None:
        self._by_symbol = by_symbol or {}

    def headlines(self, symbol: str, as_of_date: str | None = None) -> list[Headline]:
        return list(self._by_symbol.get(symbol, []))


class YFinanceNews:
    """Recent headlines via yfinance. Never raises — failures degrade to []."""

    def __init__(self, max_items: int = 5) -> None:
        self.max_items = max_items

    def headlines(self, symbol: str, as_of_date: str | None = None) -> list[Headline]:
        try:
            import yfinance as yf
            raw = yf.Ticker(symbol).news or []
        except Exception:
            return []
        out: list[Headline] = []
        for item in raw[: self.max_items]:
            parsed = _parse_item(symbol, item)
            if parsed is not None:
                out.append(parsed)
        return out


def _parse_item(symbol: str, item: dict) -> Headline | None:
    """Parse one yfinance news item, tolerating both the new (nested 'content')
    and legacy (flat) schemas. Returns None when there is no usable title."""
    content = item.get("content", item)
    title = content.get("title") or item.get("title") or ""
    if not title:
        return None

    provider = content.get("provider")
    publisher = (provider.get("displayName", "") if isinstance(provider, dict)
                 else "") or item.get("publisher", "")

    date = ""
    if content.get("pubDate"):
        date = str(content["pubDate"])[:10]
    elif item.get("providerPublishTime"):
        date = datetime.fromtimestamp(
            item["providerPublishTime"], tz=timezone.utc).strftime("%Y-%m-%d")

    return Headline(symbol=symbol, title=title, publisher=publisher, published_date=date)


def collect_news(news_source, symbols, as_of_date: str | None = None) -> dict[str, list[Headline]]:
    """Headlines for each symbol. A per-symbol failure degrades to [] — never raises,
    so a flaky news provider can never abort a trading cycle."""
    out: dict[str, list[Headline]] = {}
    for symbol in symbols:
        try:
            out[symbol] = news_source.headlines(symbol, as_of_date=as_of_date)
        except Exception:
            out[symbol] = []
    return out
