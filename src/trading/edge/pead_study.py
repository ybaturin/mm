from __future__ import annotations

from typing import Callable

from trading.edge.events import EarningsEvent
from trading.edge.portfolio import PeadRecord
from trading.edge.realize import FetchWindow, market_adjusted_multi
from trading.edge.sue import prior_surprises, sue_by_price, sue_by_sigma, surprise


def split_events_by_date(events: list[EarningsEvent],
                         split_date: str) -> tuple[list[EarningsEvent], list[EarningsEvent]]:
    """Time-based split: report_date < split_date -> train, else test. The held-out
    test half is touched once, at the very end (anti-overfit, spec §7)."""
    train = [e for e in events if e.report_date < split_date]
    test = [e for e in events if e.report_date >= split_date]
    return train, test


PriceOf = Callable[[str, str], float]
SeriesOf = Callable[[str], list[dict]]


def build_records(events: list[EarningsEvent], *, tier: str, horizon: int,
                  normalization: str, price_of: PriceOf, earnings_series_of: SeriesOf,
                  fetch_window: FetchWindow) -> list[PeadRecord]:
    """Turn events into tradeable records for one (tier, horizon, normalization) config.
    Drops any event whose signal or realized return cannot be computed.

    `normalization` is 'price' or 'sigma'. Deps are injected so this is testable without
    network: price_of(symbol, date)->price, earnings_series_of(symbol)->history rows,
    fetch_window(symbol, start, end)->bars.
    """
    out: list[PeadRecord] = []
    for ev in events:
        s = surprise(ev.eps_actual, ev.eps_consensus)
        if s is None:
            continue
        if normalization == "price":
            signal = sue_by_price(s, price_of(ev.symbol, ev.decision_date))
        else:
            priors = prior_surprises(earnings_series_of(ev.symbol), ev.report_date)
            signal = sue_by_sigma(s, priors)
        if signal is None:
            continue
        try:
            stock = fetch_window(ev.symbol, _start(ev.decision_date),
                                 _end(ev.decision_date, horizon))
            spy = fetch_window("SPY", _start(ev.decision_date),
                               _end(ev.decision_date, horizon))
        except Exception:
            continue
        realized = market_adjusted_multi(stock, spy, ev.decision_date, [horizon])[horizon]
        if realized is None:
            continue
        out.append(PeadRecord(symbol=ev.symbol, decision_date=ev.decision_date,
                              tier=tier, signal=signal, realized=realized))
    return out


def _start(decision_date: str) -> str:
    from trading.edge.realize import _add_calendar_days
    return _add_calendar_days(decision_date, -3)


def _end(decision_date: str, horizon: int) -> str:
    from trading.edge.realize import _add_calendar_days
    return _add_calendar_days(decision_date, horizon * 2 + 14)
