from __future__ import annotations


def sma(closes: list[float], window: int) -> float | None:
    """Simple moving average over the most recent `window` closes."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def pct_change(closes: list[float], days: int) -> float | None:
    """Fractional return over `days` bars: closes[-1] / closes[-1-days] - 1."""
    if len(closes) <= days:
        return None
    past = closes[-1 - days]
    if past == 0:
        return None
    return closes[-1] / past - 1


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Relative Strength Index over the most recent `period` price changes."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains = sum(d for d in deltas if d > 0)
    losses = sum(-d for d in deltas if d < 0)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)
