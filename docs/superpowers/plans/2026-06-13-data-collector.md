# Data Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn raw market data into a structured "briefing" for one agent: current cash/positions, plus per-symbol price and technical indicators across the agent's universe. This is the input the Agent Core (plan 5) feeds to the LLM.

**Architecture:** A `MarketDataSource` Protocol abstracts where bars come from. `YFinanceSource` (free, no API key) is the real implementation; `FakeMarketDataSource` (canned bars) is the test backbone. Indicators are pure functions over close-price lists. `build_briefing()` assembles a `Briefing` dataclass — plain data, no LLM, no I/O beyond the injected source — so it is fully testable. News is deliberately out of scope for the MVP (squishy free APIs); a `NewsSource` can be added behind its own interface later.

**Tech Stack:** Python 3.12+, `yfinance` (new dependency; pulls `pandas`), stdlib, `pytest`.

This is plan **4 of 9**. Depends on plan 1 (`AgentState`, `Position`). Spec: `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md`.

---

## Existing interfaces this plan consumes (from plan 1, verified)

```python
# src/trading/domain.py
@dataclass(frozen=True)
class Position:
    symbol: str; quantity: int; avg_price: float

@dataclass
class AgentState:
    agent_id: str; cash: float; positions: list[Position]
    peak_equity: float; equity_day_start: float
    def position_for(self, symbol: str) -> Position | None: ...
    def equity(self, prices: dict[str, float]) -> float: ...
```

## File Structure

```
config/universe.toml                  # tradable symbols (whitelist) per the spec
src/trading/data/__init__.py
src/trading/data/bars.py              # Bar dataclass + MarketDataSource Protocol
src/trading/data/fake_source.py       # FakeMarketDataSource (canned bars)
src/trading/data/indicators.py        # pure: sma, rsi, pct_change
src/trading/data/yfinance_source.py   # YFinanceSource + bars_from_dataframe
src/trading/data/briefing.py          # Briefing/SymbolBrief + build_briefing + load_universe
scripts/smoke_yfinance.py             # manual: fetch a real symbol, print bars
tests/test_indicators.py
tests/test_fake_source.py
tests/test_yfinance_parsing.py
tests/test_briefing.py
```

**Responsibilities:**
- `bars.py` — the data vocabulary + source contract.
- `indicators.py` — pure math, no I/O. Independently testable.
- `briefing.py` — assembles the agent-facing snapshot. Knows nothing about LLMs.
- `yfinance_source.py` — the only file importing `yfinance`/`pandas`.

---

## Task 1: Bar type, source contract, FakeMarketDataSource

**Files:**
- Create: `src/trading/data/__init__.py` (empty)
- Create: `src/trading/data/bars.py`
- Create: `src/trading/data/fake_source.py`
- Test: `tests/test_fake_source.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fake_source.py
import pytest
from trading.data.bars import Bar
from trading.data.fake_source import FakeMarketDataSource


def bars(closes):
    return [Bar(date=f"2026-06-{i+1:02d}", open=c, high=c, low=c, close=c, volume=1000)
            for i, c in enumerate(closes)]


def test_history_returns_supplied_bars():
    src = FakeMarketDataSource({"AAPL": bars([100.0, 101.0, 102.0])})
    hist = src.history("AAPL", days=5)
    assert [b.close for b in hist] == [100.0, 101.0, 102.0]


def test_history_respects_days_limit():
    src = FakeMarketDataSource({"AAPL": bars([1, 2, 3, 4, 5])})
    hist = src.history("AAPL", days=2)
    assert [b.close for b in hist] == [4, 5]   # most recent `days` bars


def test_latest_price_is_last_close():
    src = FakeMarketDataSource({"AAPL": bars([100.0, 105.0])})
    assert src.latest_price("AAPL") == 105.0


def test_unknown_symbol_raises():
    src = FakeMarketDataSource({})
    with pytest.raises(KeyError):
        src.history("ZZZ", days=5)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fake_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.data.bars'`.

- [ ] **Step 3: Write the implementations**

```python
# src/trading/data/__init__.py
```

```python
# src/trading/data/bars.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    date: str        # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int


class MarketDataSource(Protocol):
    def history(self, symbol: str, days: int) -> list[Bar]: ...
    def latest_price(self, symbol: str) -> float: ...
```

```python
# src/trading/data/fake_source.py
from __future__ import annotations

from trading.data.bars import Bar


class FakeMarketDataSource:
    """In-memory market data for tests and simulation. Satisfies MarketDataSource."""

    def __init__(self, data: dict[str, list[Bar]]) -> None:
        self._data = data

    def history(self, symbol: str, days: int) -> list[Bar]:
        if symbol not in self._data:
            raise KeyError(symbol)
        return self._data[symbol][-days:]

    def latest_price(self, symbol: str) -> float:
        if symbol not in self._data or not self._data[symbol]:
            raise KeyError(symbol)
        return self._data[symbol][-1].close
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fake_source.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/data/__init__.py src/trading/data/bars.py src/trading/data/fake_source.py tests/test_fake_source.py
git commit -m "feat: market data source contract and fake source"
```

---

## Task 2: Technical indicators (pure)

**Files:**
- Create: `src/trading/data/indicators.py`
- Test: `tests/test_indicators.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_indicators.py
import pytest
from trading.data.indicators import pct_change, rsi, sma


def test_sma_full_window():
    assert sma([1, 2, 3, 4, 5], 5) == 3.0


def test_sma_uses_most_recent_window():
    assert sma([1, 2, 3, 4, 5], 3) == 4.0   # mean of 3,4,5


def test_sma_insufficient_data_returns_none():
    assert sma([1, 2], 5) is None


def test_pct_change_over_n_days():
    assert pct_change([100.0, 110.0], 1) == pytest.approx(0.10)
    assert pct_change([100.0, 105.0, 110.0], 2) == pytest.approx(0.10)


def test_pct_change_insufficient_data_returns_none():
    assert pct_change([100.0], 1) is None


def test_rsi_all_gains_is_100():
    closes = list(range(1, 16))            # strictly increasing, 15 values
    assert rsi(closes, period=14) == 100.0


def test_rsi_all_losses_is_0():
    closes = list(range(15, 0, -1))        # strictly decreasing
    assert rsi(closes, period=14) == 0.0


def test_rsi_balanced_is_50():
    # 7 up moves of +1 then 7 down moves of -1 -> avg gain == avg loss -> RSI 50
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 16, 15, 14, 13, 12, 11, 10]
    assert rsi(closes, period=14) == pytest.approx(50.0)


def test_rsi_insufficient_data_returns_none():
    assert rsi([1, 2, 3], period=14) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_indicators.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.data.indicators'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/data/indicators.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_indicators.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/data/indicators.py tests/test_indicators.py
git commit -m "feat: pure technical indicators (sma, rsi, pct_change)"
```

---

## Task 3: yfinance source and DataFrame parsing

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `src/trading/data/yfinance_source.py`
- Create: `scripts/smoke_yfinance.py`
- Test: `tests/test_yfinance_parsing.py`

- [ ] **Step 1: Add the dependency**

Run: `uv add yfinance`
Expected: `pyproject.toml` gains `yfinance`; `uv.lock` updates (pulls `pandas`).

- [ ] **Step 2: Write the failing test**

The risky part is translating a yfinance DataFrame into our `Bar` list. That translator
is pure and tested by constructing a DataFrame with the same columns yfinance returns.
The network fetch is verified by the smoke script, not unit tests.

```python
# tests/test_yfinance_parsing.py
import pandas as pd
from trading.data.bars import Bar
from trading.data.yfinance_source import bars_from_dataframe


def test_bars_from_dataframe_maps_columns_and_dates():
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.5],
            "Close": [101.0, 102.5],
            "Volume": [1000, 1200],
        },
        index=pd.to_datetime(["2026-06-10", "2026-06-11"]),
    )
    out = bars_from_dataframe(df)
    assert out == [
        Bar("2026-06-10", 100.0, 102.0, 99.0, 101.0, 1000),
        Bar("2026-06-11", 101.0, 103.0, 100.5, 102.5, 1200),
    ]


def test_bars_from_dataframe_empty():
    df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    assert bars_from_dataframe(df) == []
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_yfinance_parsing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.data.yfinance_source'`.

- [ ] **Step 4: Write the implementation**

```python
# src/trading/data/yfinance_source.py
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
```

- [ ] **Step 5: Write the smoke script**

```python
# scripts/smoke_yfinance.py
"""Manual check: fetch real bars for a symbol and print the last few.

Run:  uv run python scripts/smoke_yfinance.py AAPL
"""
from __future__ import annotations

import sys

from trading.data.yfinance_source import YFinanceSource


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    bars = YFinanceSource().history(symbol, days=5)
    print(f"{symbol}: {len(bars)} bars")
    for b in bars:
        print(f"  {b.date}  close={b.close:.2f}  vol={b.volume}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the parsing test to verify it passes**

Run: `uv run pytest tests/test_yfinance_parsing.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/trading/data/yfinance_source.py scripts/smoke_yfinance.py tests/test_yfinance_parsing.py
git commit -m "feat: yfinance market data source with tested DataFrame parsing"
```

---

## Task 4: Briefing assembler and universe config

**Files:**
- Create: `config/universe.toml`
- Create: `src/trading/data/briefing.py`
- Test: `tests/test_briefing.py`

- [ ] **Step 1: Write the universe config**

```toml
# config/universe.toml — tradable whitelist (liquid US equities/ETFs), spec §3
symbols = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "SPY", "QQQ", "IWM", "DIA",
]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_briefing.py
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
                              as_of_date="2026-06-15", lookback_days=60)
    symbols = {s.symbol for s in briefing.symbols}
    assert symbols == {"AAPL", "MSFT", "NVDA"}   # universe + held, deduped


def test_build_briefing_computes_price_indicators_and_holding():
    source = FakeMarketDataSource({"AAPL": ramp(100, 60)})
    state = AgentState(agent_id="moderate", cash=2000.0,
                       positions=[Position("AAPL", 5, 120.0)],
                       peak_equity=5000.0, equity_day_start=5000.0)
    briefing = build_briefing(state, universe=["AAPL"], source=source,
                              as_of_date="2026-06-15", lookback_days=60)
    brief = briefing.symbols[0]
    assert brief.symbol == "AAPL"
    assert brief.price == 159.0                 # last close of ramp(100,60)
    assert brief.sma20 is not None
    assert brief.held_quantity == 5
    assert brief.held_avg_price == 120.0


def test_build_briefing_reports_cash_and_equity():
    source = FakeMarketDataSource({"AAPL": ramp(100, 60)})
    state = AgentState(agent_id="moderate", cash=2000.0,
                       positions=[Position("AAPL", 5, 120.0)],
                       peak_equity=5000.0, equity_day_start=5000.0)
    briefing = build_briefing(state, universe=["AAPL"], source=source,
                              as_of_date="2026-06-15", lookback_days=60)
    assert briefing.cash == 2000.0
    # equity = cash + 5 * last price 159 = 2000 + 795 = 2795
    assert briefing.equity == pytest.approx(2795.0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_briefing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.data.briefing'`.

- [ ] **Step 4: Write the implementation**

```python
# src/trading/data/briefing.py
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from trading.data.bars import MarketDataSource
from trading.data.indicators import pct_change, rsi, sma
from trading.domain import AgentState


@dataclass(frozen=True)
class SymbolBrief:
    symbol: str
    price: float
    sma20: float | None
    sma50: float | None
    rsi14: float | None
    return_5d: float | None
    held_quantity: int          # 0 if not held
    held_avg_price: float | None


@dataclass(frozen=True)
class Briefing:
    agent_id: str
    as_of_date: str
    cash: float
    equity: float
    symbols: list[SymbolBrief]


def load_universe(path: str | Path) -> list[str]:
    with open(path, "rb") as f:
        return tomllib.load(f)["symbols"]


def build_briefing(
    state: AgentState,
    universe: list[str],
    source: MarketDataSource,
    as_of_date: str,
    lookback_days: int = 60,
) -> Briefing:
    """Assemble the agent-facing snapshot: cash/equity + per-symbol price & indicators.

    Covers the union of the universe and currently-held symbols (deduped, sorted).
    """
    held = {p.symbol: p for p in state.positions}
    symbols = sorted(set(universe) | set(held))

    briefs: list[SymbolBrief] = []
    prices: dict[str, float] = {}
    for symbol in symbols:
        closes = [b.close for b in source.history(symbol, days=lookback_days)]
        price = closes[-1]
        prices[symbol] = price
        position = held.get(symbol)
        briefs.append(SymbolBrief(
            symbol=symbol,
            price=price,
            sma20=sma(closes, 20),
            sma50=sma(closes, 50),
            rsi14=rsi(closes, 14),
            return_5d=pct_change(closes, 5),
            held_quantity=position.quantity if position else 0,
            held_avg_price=position.avg_price if position else None,
        ))

    return Briefing(
        agent_id=state.agent_id,
        as_of_date=as_of_date,
        cash=state.cash,
        equity=state.equity(prices),
        symbols=briefs,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_briefing.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add config/universe.toml src/trading/data/briefing.py tests/test_briefing.py
git commit -m "feat: briefing assembler and tradable universe config"
```

---

## Task 5: README and full suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all tests pass (plans 1–4), exit code 0.

- [ ] **Step 2: Update the Status section of `README.md`**

Replace the `## Status` section with:

```markdown
## Status

- Plan 1 of 9: domain models, risk-profile config, deterministic Guardrails Engine. ✓
- Plan 2 of 9: SQLite persistence — ledger, decision journal, fills, equity snapshots. ✓
- Plan 3 of 9: Broker boundary — Protocol, FakeBroker, IBKRBroker (ib-async). ✓
- Plan 4 of 9: Data Collector — MarketDataSource (yfinance), pure indicators, and the
  `build_briefing()` snapshot fed to the agent. ✓

Tradable universe lives in `config/universe.toml`. Verify live data with:

    uv run python scripts/smoke_yfinance.py AAPL
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: mark data collector plan complete in README"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §4 component 1 "Data Collector", §5 briefing input):**
- Prices + history from a source → `MarketDataSource` + `YFinanceSource`. ✓
- Computed indicators (SMA, RSI, returns) → `indicators.py`, fully tested. ✓
- Structured briefing for the agent (cash, equity, per-symbol price + indicators +
  current holding) → `Briefing`/`SymbolBrief` + `build_briefing`. ✓
- Tradable whitelist (liquid US equities/ETFs) → `config/universe.toml` + `load_universe`.
  The briefing covers the union of universe and held symbols so the agent can always
  decide to close what it holds. ✓
- Free data source chosen (yfinance) to avoid IBKR market-data subscription cost during
  the paper phase; swappable behind `MarketDataSource`. ✓

**Deferred to later plans (correctly out of scope here):**
- News headlines → a future `NewsSource` behind its own interface (noted; not in MVP).
- Serializing the `Briefing` into an LLM prompt → Agent Core (plan 5); this plan stops
  at plain structured data.
- Point-in-time historical replay for evaluating deterministic parts → uses the same
  source interface with a date-bounded implementation when needed (spec §11).

**Live-data honesty:** `YFinanceSource.history`/`latest_price` hit the network and are NOT
unit-tested; verified by `scripts/smoke_yfinance.py`. The pure parser `bars_from_dataframe`
(the real mistake risk) IS tested against a constructed DataFrame.

**No hidden clock:** `build_briefing` takes `as_of_date` from the caller; nothing reads the
system clock.

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `Bar`, `MarketDataSource.history(symbol, days)` /
`latest_price(symbol)`, `sma(closes, window)`, `rsi(closes, period)`,
`pct_change(closes, days)`, `Briefing`/`SymbolBrief`, and
`build_briefing(state, universe, source, as_of_date, lookback_days)` are used identically
across Tasks 1–5 and consume the verified plan-1 `AgentState`/`Position`. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-data-collector.md`.
