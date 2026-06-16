from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from trading.analysis.track_record import max_drawdown, sharpe
from trading.edge.events import EarningsEvent
from trading.edge.metrics import hit_rate, information_coefficient, t_statistic
from trading.edge.portfolio import (PeadRecord, bucket_returns, long_short_net,
                                    pnl_series)
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


@dataclass(frozen=True)
class Config:
    tier: str
    horizon: int
    normalization: str       # 'price' | 'sigma'


@dataclass(frozen=True)
class ConfigResult:
    config: Config
    net_long_short: float
    n: int


def sweep(configs: list[Config], *, events: list[EarningsEvent],
          builder: Callable[[Config, list[EarningsEvent]], list[PeadRecord]]
          ) -> list[ConfigResult]:
    """Evaluate each config on the given (train) events, scored by net long-short spread,
    ranked best-first. `builder(config, events) -> records` is injected so the sweep is
    testable without network and so the real run can plug in build_records."""
    results: list[ConfigResult] = []
    for cfg in configs:
        recs = builder(cfg, events)
        results.append(ConfigResult(config=cfg, net_long_short=long_short_net(recs),
                                    n=len(recs)))
    results.sort(key=lambda r: r.net_long_short, reverse=True)
    return results


def build_report(chosen: ConfigResult, test_records: list[PeadRecord],
                 all_ranked: list[ConfigResult]) -> str:
    """Final report. The chosen config was picked on TRAIN; here it is scored ONCE on the
    held-out TEST records. Multiple-testing breadth is printed (configs evaluated)."""
    c = chosen.config
    lines = [
        "=== MECHANICAL PEAD STUDY REPORT ===",
        f"PRE-REGISTERED CONFIG: tier={c.tier} horizon={c.horizon} norm={c.normalization}",
        f"TRAIN net long-short: {chosen.net_long_short:+.4f} (n={chosen.n})",
        f"configs evaluated: {len(all_ranked)}",
        "--- HELD-OUT TEST ---",
        f"test sample: {len(test_records)}",
    ]
    if len(test_records) < 2:
        lines.append("Result: insufficient held-out data to conclude.")
        return "\n".join(lines)

    signals = [r.signal for r in test_records]
    realized = [r.realized for r in test_records]
    series = pnl_series(test_records)
    monthly = bucket_returns(series)
    lines += [
        f"net long-short (after costs): {long_short_net(test_records):+.4f}",
        f"information coefficient: {information_coefficient(signals, realized):+.3f}",
        f"hit rate: {hit_rate(signals, realized):.1%}",
        f"directional t-stat: {t_statistic([p for _, p in series]):+.2f}",
        f"monthly Sharpe (annualized): {sharpe(monthly, periods_per_year=12):+.2f}",
        f"max drawdown: {max_drawdown(_equity(monthly)):.1%}",
        "",
        "Gate: real only if held-out long-short > 0, significant, and stable on forward.",
        "Caveat: one regime + multiple-testing — confirm on forward accumulation.",
    ]
    return "\n".join(lines)


def _equity(returns: list[float]) -> list[float]:
    """Cumulative equity curve from a return series, starting at 1.0."""
    curve = [1.0]
    for r in returns:
        curve.append(curve[-1] * (1.0 + r))
    return curve


def run_study(*, events: list[EarningsEvent], split_date: str, configs: list[Config],
              price_of: PriceOf, earnings_series_of: SeriesOf,
              fetch_window: FetchWindow) -> str:
    """Full study: sweep configs on TRAIN, pre-register the best, score it ONCE on the
    held-out TEST half. Deps injected for tests; main() wires the real providers."""
    train, test = split_events_by_date(events, split_date)

    def builder(cfg: Config, evs: list[EarningsEvent]) -> list[PeadRecord]:
        return build_records(evs, tier=cfg.tier, horizon=cfg.horizon,
                             normalization=cfg.normalization, price_of=price_of,
                             earnings_series_of=earnings_series_of,
                             fetch_window=fetch_window)

    ranked = sweep(configs, events=train, builder=builder)
    chosen = ranked[0]
    test_records = builder(chosen.config, test)
    return build_report(chosen, test_records, all_ranked=ranked)


def main() -> None:
    """Run the PEAD study from Alpha Vantage data. Env: ALPHAVANTAGE_API_KEY,
    EDGE_CUTOFF (default 2026-02-01), PEAD_SPLIT (train/test boundary, default
    2026-04-01). Sweeps tier x horizon x normalization; reports train pick + held-out.
    """
    import os

    from trading.data.yfinance_source import YFinanceSource
    from trading.edge.events import select_post_cutoff
    from trading.edge.realize import yfinance_window
    from trading.edge.run import _load_universe
    from trading.edge.sources import AlphaVantageSource

    cutoff = os.environ.get("EDGE_CUTOFF", "2026-02-01")
    split = os.environ.get("PEAD_SPLIT", "2026-04-01")

    av = AlphaVantageSource()
    src = YFinanceSource()
    events = select_post_cutoff(av.calendar(_load_universe(), earliest_report_date=cutoff),
                                earliest_report_date=cutoff)
    print(f"{len(events)} post-cutoff events; train/test split at {split}")

    tier = "small" if "smid" in os.environ.get("EDGE_UNIVERSE_FILE", "") else "large"
    configs = [Config(tier, h, n) for h in (1, 5, 10, 20, 60) for n in ("price", "sigma")]

    def price_of(symbol: str, d: str) -> float:
        try:
            return src.latest_price(symbol, as_of_date=d)
        except Exception:
            return 0.0

    report = run_study(events=events, split_date=split, configs=configs,
                       price_of=price_of, earnings_series_of=av.earnings_series,
                       fetch_window=yfinance_window)
    print(report)


if __name__ == "__main__":
    main()
