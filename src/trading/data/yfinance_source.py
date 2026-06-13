from __future__ import annotations

from trading.data.bars import Bar


def bars_from_dataframe(df) -> list[Bar]:
    """Convert a yfinance OHLCV DataFrame (DatetimeIndex) into our Bar list."""
    out: list[Bar] = []
    for ts, row in df.iterrows():
        out.append(
            Bar(
                date=ts.strftime("%Y-%m-%d"),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
            )
        )
    return out


class YFinanceSource:
    """Free market data via yfinance. Satisfies MarketDataSource."""

    def history(self, symbol: str, days: int) -> list[Bar]:
        import yfinance as yf

        # Pad the calendar window so we clear weekends/holidays, then trim to `days`.
        period_days = max(days * 2, days + 10)
        df = yf.Ticker(symbol).history(period=f"{period_days}d", interval="1d")
        if df.empty:
            raise KeyError(symbol)
        return bars_from_dataframe(df)[-days:]

    def latest_price(self, symbol: str) -> float:
        bars = self.history(symbol, days=1)
        if not bars:
            raise KeyError(symbol)
        return bars[-1].close
