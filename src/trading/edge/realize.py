from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from trading.data.bars import Bar
from trading.data.yfinance_source import bars_from_dataframe


def forward_return(bars: list[Bar], decision_date: str,
                   horizon_days: int) -> float | None:
    """Fractional return from the entry close (first bar on/after decision_date) to the
    close `horizon_days` trading bars later. None if the forward window is incomplete."""
    bars = sorted(bars, key=lambda b: b.date)
    entry_idx = next((i for i, b in enumerate(bars) if b.date >= decision_date), None)
    if entry_idx is None:
        return None
    exit_idx = entry_idx + horizon_days
    if exit_idx >= len(bars):
        return None
    entry = bars[entry_idx].close
    if entry <= 0:
        return None
    return bars[exit_idx].close / entry - 1.0


def market_adjusted_return(stock_bars: list[Bar], spy_bars: list[Bar],
                           decision_date: str, horizon_days: int) -> float | None:
    """Stock forward return minus SPY forward return over the same window. None if
    either leg cannot be computed."""
    s = forward_return(stock_bars, decision_date, horizon_days)
    m = forward_return(spy_bars, decision_date, horizon_days)
    if s is None or m is None:
        return None
    return s - m


def _add_calendar_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def yfinance_window(symbol: str, start_date: str, end_date: str) -> list[Bar]:
    """Daily bars in [start_date, end_date] via yfinance. Unlike YFinanceSource.history
    (which is anchored on *today* for the live cycle), this fetches an explicit historical
    window — needed to score events that happened weeks/months ago. Degrades to []."""
    import yfinance as yf

    df = yf.Ticker(symbol).history(start=start_date, end=end_date, interval="1d")
    if df.empty:
        return []
    return bars_from_dataframe(df)


FetchWindow = Callable[[str, str, str], list[Bar]]


def realized_market_adjusted(symbol: str, decision_date: str, horizon_days: int,
                             fetch_window: FetchWindow = yfinance_window,
                             pad_days: int = 14) -> float | None:
    """Market-adjusted realized return over the horizon. Fetches an explicit date window
    around the event (so historical events score correctly) and delegates to the pure
    math. Returns None on any data gap — never raises on missing forward data."""
    start = _add_calendar_days(decision_date, -3)
    end = _add_calendar_days(decision_date, horizon_days * 2 + pad_days)
    try:
        stock = fetch_window(symbol, start, end)
        spy = fetch_window("SPY", start, end)
    except Exception:
        return None
    return market_adjusted_return(stock, spy, decision_date, horizon_days)
