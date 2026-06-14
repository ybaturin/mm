from __future__ import annotations

import math

from trading.broker.fake import FakeBroker
from trading.config import RiskProfile, load_profiles
from trading.data.bars import Bar
from trading.data.briefing import build_briefing, load_universe
from trading.data.fake_source import FakeMarketDataSource
from trading.orchestrator.cycle import run_cycle
from trading.orchestrator.strategy import FakeStrategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db

LOOKBACK = 60


def synthetic_series(symbols: list[str], total_bars: int) -> dict[str, list[Bar]]:
    """Deterministic price paths: a drifting sinusoid per symbol. Reproducible (no RNG)."""
    series: dict[str, list[Bar]] = {}
    for idx, symbol in enumerate(symbols):
        base = 100.0 + 50.0 * idx
        bars = []
        for i in range(total_bars):
            # upward drift plus a slow wave so trends cross the SMA both ways
            price = base * (1.0 + 0.004 * i + 0.05 * math.sin((i + idx * 3) / 6.0))
            price = round(price, 2)
            bars.append(Bar(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                            price, price, price, price, 1_000_000))
        series[symbol] = bars
    return series


def run_simulation(
    days: int,
    profiles: dict[str, RiskProfile],
    universe: list[str],
    series: dict[str, list[Bar]],
    accounts: AccountRepository,
    journal: JournalRepository,
    start_index: int = LOOKBACK,
) -> dict[str, list[tuple[str, float]]]:
    """Run `days` trading days for every profile on its own FakeBroker.

    Returns {agent_id: [(date, equity), ...]}. Each day exposes only bars up to that day
    (point-in-time — no peeking ahead), and fills happen at that day's close.
    """
    brokers = {name: FakeBroker(cash=p.budget) for name, p in profiles.items()}
    results: dict[str, list[tuple[str, float]]] = {name: [] for name in profiles}

    for d in range(days):
        cutoff = start_index + d + 1
        source = FakeMarketDataSource({s: bars[:cutoff] for s, bars in series.items()})
        as_of = series[universe[0]][cutoff - 1].date
        prices = {s: source.latest_price(s) for s in universe}

        for name, profile in profiles.items():
            broker = brokers[name]
            for s in universe:
                broker.set_price(s, prices[s])
            # Memory is point-in-time (journal) so it stays on; news is current-only and
            # would be look-ahead in a historical replay, so it is deliberately omitted.
            state = run_cycle(
                agent_id=name, profile=profile, broker=broker, source=source,
                accounts=accounts, journal=journal, strategy=FakeStrategy(),
                universe=universe, as_of_date=as_of, ts=f"{as_of}T13:30:00Z",
                # Offline simulation runs unattended: explicitly auto-approve.
                confirm=lambda proposal, decision: True,
            )
            results[name].append((as_of, round(state.equity(prices), 2)))

    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Simulate the trading scheme on synthetic data.")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    profiles = load_profiles("config/profiles.toml")
    universe = load_universe("config/universe.toml")
    series = synthetic_series(universe, total_bars=LOOKBACK + args.days + 1)

    conn = connect(":memory:")
    init_db(conn)
    accounts, journal = AccountRepository(conn), JournalRepository(conn)

    results = run_simulation(args.days, profiles, universe, series, accounts, journal)

    print(f"Simulated {args.days} trading days on {len(universe)} symbols (FakeStrategy).\n")
    for name, profile in profiles.items():
        curve = results[name]
        start, end = profile.budget, curve[-1][1]
        pnl = end - start
        print(f"{name:>13}: ${start:,.0f} -> ${end:,.2f}  "
              f"({pnl:+,.2f}, {pnl / start:+.1%})  trades={len(journal.fills_for(name))}")


if __name__ == "__main__":
    main()
