from pathlib import Path

import pytest
from trading.data.bars import Bar
from trading.data.briefing import build_briefing, load_universe
from trading.data.fake_source import FakeMarketDataSource
from trading.domain import AgentState, Position

UNIVERSE_TOML = Path(__file__).resolve().parents[1] / "config" / "universe.toml"


def ramp(start, n):
    return [Bar(f"2026-06-{i+1:02d}", v, v, v, v, 1000)
            for i, v in enumerate(float(start + i) for i in range(n))]


def test_load_universe_reads_symbols():
    symbols = load_universe(UNIVERSE_TOML)
    assert "AAPL" in symbols and "SPY" in symbols


def test_build_briefing_covers_universe_and_held_symbols():
    source = FakeMarketDataSource({
        "AAPL": ramp(100, 60),
        "MSFT": ramp(300, 60),
        "NVDA": ramp(900, 60),   # held but not in universe arg
    })
    state = AgentState(
        agent_id="moderate", cash=2000.0,
        positions=[Position("NVDA", 2, 800.0)],
        peak_equity=5000.0, equity_day_start=5000.0,
    )
    briefing = build_briefing(state, universe=["AAPL", "MSFT"], source=source,
                              as_of_date="2026-12-31", lookback_days=60)
    symbols = {s.symbol for s in briefing.symbols}
    assert symbols == {"AAPL", "MSFT", "NVDA"}   # universe + held, deduped


def test_build_briefing_computes_price_indicators_and_holding():
    source = FakeMarketDataSource({"AAPL": ramp(100, 60)})
    state = AgentState(agent_id="moderate", cash=2000.0,
                       positions=[Position("AAPL", 5, 120.0)],
                       peak_equity=5000.0, equity_day_start=5000.0)
    briefing = build_briefing(state, universe=["AAPL"], source=source,
                              as_of_date="2026-12-31", lookback_days=60)
    brief = briefing.symbols[0]
    assert brief.symbol == "AAPL"
    assert brief.price == 159.0                 # last close of ramp(100,60)
    assert brief.sma20 is not None
    assert brief.held_quantity == 5
    assert brief.held_avg_price == 120.0


def test_build_briefing_excludes_bars_after_as_of_date():
    # Point-in-time: a backfill/replay must not see bars after the as-of date.
    source = FakeMarketDataSource({"AAPL": ramp(100, 60)})
    state = AgentState(agent_id="moderate", cash=2000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    briefing = build_briefing(state, universe=["AAPL"], source=source,
                              as_of_date="2026-06-10", lookback_days=60)
    assert briefing.symbols[0].price == 109.0    # last close on/before 2026-06-10, not 159


def test_build_briefing_reports_cash_and_equity():
    source = FakeMarketDataSource({"AAPL": ramp(100, 60)})
    state = AgentState(agent_id="moderate", cash=2000.0,
                       positions=[Position("AAPL", 5, 120.0)],
                       peak_equity=5000.0, equity_day_start=5000.0)
    briefing = build_briefing(state, universe=["AAPL"], source=source,
                              as_of_date="2026-12-31", lookback_days=60)
    assert briefing.cash == 2000.0
    # equity = cash + 5 * last price 159 = 2000 + 795 = 2795
    assert briefing.equity == pytest.approx(2795.0)
