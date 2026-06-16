# Mechanical PEAD Edge Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether a mechanical post-earnings-drift strategy (trade on EPS surprise, no LLM) has a tradeable, cost-surviving, out-of-sample-stable edge — before any capital.

**Architecture:** Extend the existing `src/trading/edge/` module. Drop the LLM path entirely. Add pure, TDD'd components: SUE signal, tiered cost model, multi-horizon realized returns, quintile long-short portfolio sim, and a time-based train/test split. An orchestrator (`pead_study.py`) sweeps configs on the train half, pre-registers one config, runs it once on the held-out test half, and reports. Reuses `AlphaVantageSource` (EPS + dates), `realize.py` (yfinance windows), `metrics.py`, and `analysis/track_record.py` (Sharpe/drawdown).

**Tech Stack:** Python 3.12, sqlite (not needed for the study — it's in-memory/report-only), Alpha Vantage `EARNINGS`, yfinance, pytest. No LLM, no tokens. No new heavy deps.

**Spec:** `docs/superpowers/specs/2026-06-16-mechanical-pead-study-design.md`

---

## File Structure

All new files under `src/trading/edge/` (flat, matching the module).

- `sue.py` — `surprise`, `sue_by_price`, `sue_by_sigma`, `prior_surprises` (pure signal math).
- `costs.py` — `ROUND_TRIP_BPS`, `position_pnl` (tiered cost model, pure).
- `portfolio.py` — `PeadRecord`, `long_short_net`, `pnl_series`, `bucket_returns` (pure sim).
- `pead_study.py` — `split_events_by_date`, `build_records`, `sweep`, `run_study`, `main` (orchestration; deps injected for tests).
- Modify `realize.py` — add `market_adjusted_multi` (returns over several horizons from one bar window).
- Modify `sources.py` — add `parse_av_earnings_series` + `AlphaVantageSource.earnings_series` (full per-symbol history for SUE-by-sigma priors).

Tests mirror under `tests/` as `test_edge_pead_*.py`.

**Cost tiers (v1):** two tiers, assigned by source universe — `large` (from `config/edge_universe_full.toml`) and `small` (from `config/edge_universe_smid.toml`, treated conservatively as small-cap). `ROUND_TRIP_BPS` keeps a `mid` entry for later but v1 tags every symbol `large` or `small`.

---

## Task 1: SUE signal

**Files:**
- Create: `src/trading/edge/sue.py`
- Test: `tests/test_edge_pead_sue.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_sue.py
from trading.edge.sue import surprise, sue_by_price, sue_by_sigma, prior_surprises


def test_surprise_is_actual_minus_consensus():
    assert surprise(2.01, 1.94) == 2.01 - 1.94
    assert surprise(None, 1.9) is None
    assert surprise(2.0, None) is None


def test_sue_by_price_scales_by_price():
    assert abs(sue_by_price(0.07, 140.0) - 0.0005) < 1e-12
    assert sue_by_price(0.07, 0.0) is None
    assert sue_by_price(None, 140.0) is None


def test_sue_by_sigma_needs_enough_priors():
    assert sue_by_sigma(0.10, [0.01, -0.02, 0.03, 0.00]) is not None
    assert sue_by_sigma(0.10, [0.01, 0.02]) is None          # < 4 priors
    assert sue_by_sigma(0.10, [0.05, 0.05, 0.05, 0.05]) is None  # zero std


def test_sue_by_sigma_value():
    # priors std (sample) of [0,2,-2,0] = 1.632993...; surprise 1.0 -> ~0.6124
    val = sue_by_sigma(1.0, [0.0, 2.0, -2.0, 0.0])
    assert abs(val - (1.0 / 1.632993161855452)) < 1e-9


def test_prior_surprises_point_in_time_most_recent_first():
    series = [
        {"report_date": "2026-04-30", "eps_actual": 2.0, "eps_consensus": 1.9},
        {"report_date": "2026-01-29", "eps_actual": 2.8, "eps_consensus": 2.7},
        {"report_date": "2025-10-30", "eps_actual": 1.8, "eps_consensus": 1.9},
    ]
    # priors strictly before 2026-04-30, newest first
    out = prior_surprises(series, before_date="2026-04-30", limit=8)
    assert [round(x, 4) for x in out] == [round(2.8 - 2.7, 4), round(1.8 - 1.9, 4)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_sue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.sue'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/sue.py
from __future__ import annotations

import math


def surprise(eps_actual: float | None, eps_consensus: float | None) -> float | None:
    """Raw earnings surprise. None if either input is missing."""
    if eps_actual is None or eps_consensus is None:
        return None
    return eps_actual - eps_consensus


def sue_by_price(surprise_val: float | None, price: float) -> float | None:
    """Surprise scaled by share price — robust, needs no history. None if unusable."""
    if surprise_val is None or price <= 0:
        return None
    return surprise_val / price


def sue_by_sigma(surprise_val: float | None,
                 prior_surprises: list[float]) -> float | None:
    """Classic SUE: surprise over the sample std of prior surprises. Needs >= 4 priors
    and non-zero std, else None."""
    if surprise_val is None or len(prior_surprises) < 4:
        return None
    n = len(prior_surprises)
    mean = sum(prior_surprises) / n
    var = sum((x - mean) ** 2 for x in prior_surprises) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return surprise_val / std


def prior_surprises(series: list[dict], before_date: str,
                    limit: int = 8) -> list[float]:
    """Surprises of rows reported strictly before `before_date`, newest first, capped
    at `limit`. `series` rows have report_date / eps_actual / eps_consensus. Skips rows
    with missing EPS. Point-in-time: never peeks at the event itself or later."""
    rows = [r for r in series if r.get("report_date", "") < before_date]
    rows.sort(key=lambda r: r["report_date"], reverse=True)
    out: list[float] = []
    for r in rows[:limit]:
        s = surprise(r.get("eps_actual"), r.get("eps_consensus"))
        if s is not None:
            out.append(s)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_pead_sue.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/sue.py tests/test_edge_pead_sue.py
git commit -m "feat(edge/pead): SUE signal (price + sigma normalization) + point-in-time priors"
```

---

## Task 2: Tiered cost model

**Files:**
- Create: `src/trading/edge/costs.py`
- Test: `tests/test_edge_pead_costs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_costs.py
from trading.edge.costs import ROUND_TRIP_BPS, position_pnl


def test_round_trip_costs_rise_with_illiquidity():
    assert ROUND_TRIP_BPS["large"] < ROUND_TRIP_BPS["mid"] < ROUND_TRIP_BPS["small"]


def test_long_pnl_is_gross_minus_cost():
    # large tier 5 bps = 0.0005; long on +2% gross -> 0.02 - 0.0005
    assert abs(position_pnl(0.02, "large", "long") - (0.02 - 0.0005)) < 1e-12


def test_short_pnl_inverts_gross_then_pays_cost():
    # small tier 60 bps = 0.006; short on +2% gross -> -0.02 - 0.006
    assert abs(position_pnl(0.02, "small", "short") - (-0.02 - 0.006)) < 1e-12


def test_short_profits_when_price_falls():
    # short on -3% gross, large -> +0.03 - 0.0005
    assert abs(position_pnl(-0.03, "large", "short") - (0.03 - 0.0005)) < 1e-12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_costs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.costs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/costs.py
from __future__ import annotations

# Round-trip cost (entry+exit) in basis points, by liquidity tier. Starting values,
# configurable. Small-cap spreads + impact dwarf large-cap — this is what most likely
# kills a small-cap edge. v1 tags symbols 'large' or 'small'; 'mid' kept for later.
ROUND_TRIP_BPS = {"large": 5.0, "mid": 20.0, "small": 60.0}


def position_pnl(gross_return: float, tier: str, side: str) -> float:
    """Net P&L of one position over its hold, after the tier's round-trip cost.
    `side` is 'long' or 'short'. A short profits when gross_return is negative."""
    cost = ROUND_TRIP_BPS[tier] / 10_000.0
    directional = gross_return if side == "long" else -gross_return
    return directional - cost
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_pead_costs.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/costs.py tests/test_edge_pead_costs.py
git commit -m "feat(edge/pead): tiered round-trip cost model"
```

---

## Task 3: Multi-horizon market-adjusted returns

**Files:**
- Modify: `src/trading/edge/realize.py`
- Test: `tests/test_edge_pead_realize_multi.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_realize_multi.py
from trading.data.bars import Bar
from trading.edge.realize import market_adjusted_multi


def _bar(date, close):
    return Bar(date=date, open=close, high=close, low=close, close=close, volume=0)


def test_returns_per_horizon_market_adjusted():
    stock = [_bar(f"2026-02-{20+i:02d}", 100.0 + i) for i in range(8)]   # +1/day
    spy = [_bar(f"2026-02-{20+i:02d}", 100.0) for i in range(8)]          # flat
    out = market_adjusted_multi(stock, spy, "2026-02-22", horizons=[1, 5])
    # entry at 2026-02-22 (close 102, index 2). h1 -> 103/102-1; h5 -> 107/102-1.
    assert abs(out[1] - (103.0 / 102.0 - 1.0)) < 1e-9
    assert abs(out[5] - (107.0 / 102.0 - 1.0)) < 1e-9


def test_missing_horizon_is_none():
    stock = [_bar("2026-02-20", 100.0), _bar("2026-02-21", 101.0)]
    spy = [_bar("2026-02-20", 100.0), _bar("2026-02-21", 100.0)]
    out = market_adjusted_multi(stock, spy, "2026-02-20", horizons=[1, 20])
    assert out[1] is not None
    assert out[20] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_realize_multi.py -v`
Expected: FAIL with `ImportError: cannot import name 'market_adjusted_multi'`

- [ ] **Step 3: Write minimal implementation (append to realize.py)**

```python
# src/trading/edge/realize.py  (append)


def market_adjusted_multi(stock_bars: list[Bar], spy_bars: list[Bar],
                          decision_date: str,
                          horizons: list[int]) -> dict[int, float | None]:
    """Market-adjusted forward return at each horizon, computed from one pair of bar
    windows. None for any horizon whose forward window is incomplete."""
    return {h: market_adjusted_return(stock_bars, spy_bars, decision_date, h)
            for h in horizons}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_pead_realize_multi.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/realize.py tests/test_edge_pead_realize_multi.py
git commit -m "feat(edge/pead): multi-horizon market-adjusted returns"
```

---

## Task 4: Portfolio simulation

**Files:**
- Create: `src/trading/edge/portfolio.py`
- Test: `tests/test_edge_pead_portfolio.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_portfolio.py
from trading.edge.portfolio import PeadRecord, long_short_net, pnl_series, bucket_returns


def _r(date, tier, signal, realized):
    return PeadRecord(symbol="X", decision_date=date, tier=tier,
                      signal=signal, realized=realized)


def test_long_short_net_after_costs():
    # 5 records, frac 0.2 -> 1 long (top signal), 1 short (bottom signal), large tier.
    recs = [_r("2026-02-01", "large", 5.0, 0.03), _r("2026-02-01", "large", 4.0, 0.02),
            _r("2026-02-01", "large", 0.0, 0.00), _r("2026-02-01", "large", -4.0, -0.01),
            _r("2026-02-01", "large", -5.0, -0.04)]
    # long top (signal 5 -> realized 0.03): 0.03-0.0005; short bottom (signal -5 ->
    # realized -0.04): +0.04-0.0005. net = sum = 0.069.
    assert abs(long_short_net(recs, frac=0.2) - (0.03 - 0.0005 + 0.04 - 0.0005)) < 1e-9


def test_pnl_series_sides_by_signal_and_orders_by_date():
    recs = [_r("2026-03-01", "small", 2.0, 0.05), _r("2026-01-01", "small", -1.0, 0.02)]
    series = pnl_series(recs)
    # ordered by date: Jan first (short, gross +0.02 -> -0.02-0.006), then Mar (long).
    assert series[0][0] == "2026-01-01"
    assert abs(series[0][1] - (-0.02 - 0.006)) < 1e-9
    assert abs(series[1][1] - (0.05 - 0.006)) < 1e-9


def test_bucket_returns_means_by_month():
    series = [("2026-01-05", 0.01), ("2026-01-20", 0.03), ("2026-02-10", -0.02)]
    assert bucket_returns(series) == [0.02, -0.02]   # Jan mean 0.02, Feb -0.02
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_portfolio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.portfolio'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/portfolio.py
from __future__ import annotations

from dataclasses import dataclass

from trading.edge.costs import position_pnl


@dataclass(frozen=True)
class PeadRecord:
    """One event's tradeable observation: the SUE signal and the realized
    market-adjusted forward return, tagged with the symbol's cost tier."""
    symbol: str
    decision_date: str       # YYYY-MM-DD
    tier: str                # 'large' | 'mid' | 'small'
    signal: float            # SUE (signed)
    realized: float          # market-adjusted forward return at the chosen horizon


def long_short_net(records: list[PeadRecord], frac: float = 0.2) -> float:
    """Net long-short spread: top-`frac` signals long, bottom-`frac` short, each
    position costed at its tier. 0.0 for an empty set."""
    n = len(records)
    if n == 0:
        return 0.0
    ranked = sorted(records, key=lambda r: r.signal, reverse=True)
    k = max(1, int(n * frac))
    longs = ranked[:k]
    shorts = ranked[-k:]
    long_pnl = sum(position_pnl(r.realized, r.tier, "long") for r in longs) / len(longs)
    short_pnl = sum(position_pnl(r.realized, r.tier, "short") for r in shorts) / len(shorts)
    return long_pnl + short_pnl


def pnl_series(records: list[PeadRecord]) -> list[tuple[str, float]]:
    """Per-event net P&L (long if signal>0, short if <0; signal==0 skipped), ordered by
    decision_date. Each is a single costed position."""
    out: list[tuple[str, float]] = []
    for r in records:
        if r.signal == 0:
            continue
        side = "long" if r.signal > 0 else "short"
        out.append((r.decision_date, position_pnl(r.realized, r.tier, side)))
    out.sort(key=lambda t: t[0])
    return out


def bucket_returns(series: list[tuple[str, float]], ) -> list[float]:
    """Mean P&L per calendar month (YYYY-MM), ordered — a return series for Sharpe /
    drawdown via analysis.track_record."""
    buckets: dict[str, list[float]] = {}
    for date, pnl in series:
        buckets.setdefault(date[:7], []).append(pnl)
    return [sum(v) / len(v) for _, v in sorted(buckets.items())]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_pead_portfolio.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/portfolio.py tests/test_edge_pead_portfolio.py
git commit -m "feat(edge/pead): quintile long-short portfolio sim (net of costs)"
```

---

## Task 5: Earnings series source (full per-symbol history)

**Files:**
- Modify: `src/trading/edge/sources.py`
- Test: `tests/test_edge_pead_series.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_series.py
from trading.edge.sources import parse_av_earnings_series


def test_parse_series_keeps_all_quarters_with_eps():
    payload = {"quarterlyEarnings": [
        {"reportedDate": "2026-04-30", "reportedEPS": "2.01", "estimatedEPS": "1.94"},
        {"reportedDate": "2026-01-29", "reportedEPS": "2.84", "estimatedEPS": "2.67"},
        {"reportedDate": "2025-10-30", "reportedEPS": "None", "estimatedEPS": "1.77"},
    ]}
    rows = parse_av_earnings_series(payload)
    assert len(rows) == 3
    assert rows[0] == {"report_date": "2026-04-30", "eps_actual": 2.01,
                       "eps_consensus": 1.94}
    assert rows[2]["eps_actual"] is None   # 'None' string -> None


def test_parse_series_empty_on_throttle():
    assert parse_av_earnings_series({"Information": "premium"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_series.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_av_earnings_series'`

- [ ] **Step 3: Write minimal implementation (append to sources.py)**

```python
# src/trading/edge/sources.py  (append, near the Alpha Vantage helpers)


def parse_av_earnings_series(payload: dict) -> list[dict]:
    """All quarterly rows (no cutoff filter) as {report_date, eps_actual, eps_consensus}
    for SUE-by-sigma priors. EPS 'None'/missing -> None. Throttle payload -> []."""
    out: list[dict] = []
    for row in payload.get("quarterlyEarnings", []):
        reported = str(row.get("reportedDate", ""))[:10]
        if not reported:
            continue
        out.append({
            "report_date": reported,
            "eps_actual": _to_float(row.get("reportedEPS")),
            "eps_consensus": _to_float(row.get("estimatedEPS")),
        })
    return out
```

Then add a method to `AlphaVantageSource` (place after its `calendar` method):

```python
    def earnings_series(self, symbol: str) -> list[dict]:
        """Full quarterly EPS history for one symbol (for SUE-by-sigma priors)."""
        try:
            payload = self._get({"function": "EARNINGS", "symbol": symbol})
            return parse_av_earnings_series(payload)
        except Exception:
            return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_pead_series.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/sources.py tests/test_edge_pead_series.py
git commit -m "feat(edge/pead): full per-symbol earnings series for SUE priors"
```

---

## Task 6: Train/test split + record building

**Files:**
- Create: `src/trading/edge/pead_study.py`
- Test: `tests/test_edge_pead_build.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_build.py
from trading.edge.events import EarningsEvent
from trading.edge.pead_study import split_events_by_date, build_records


def _ev(symbol, report_date, actual, consensus):
    return EarningsEvent(symbol=symbol, report_date=report_date,
                         decision_date=report_date, eps_actual=actual,
                         eps_consensus=consensus)


def test_split_by_date_partitions_train_test():
    evs = [_ev("A", "2026-02-10", 1, 1), _ev("B", "2026-04-10", 1, 1)]
    train, test = split_events_by_date(evs, split_date="2026-03-15")
    assert [e.symbol for e in train] == ["A"]
    assert [e.symbol for e in test] == ["B"]


def test_build_records_computes_signal_and_realized():
    ev = _ev("NVDA", "2026-02-20", 5.0, 4.0)   # surprise +1.0

    def price_of(symbol, date):
        return 100.0

    def fetch_window(symbol, start, end):
        from trading.data.bars import Bar
        base = 100.0 if symbol == "SPY" else 100.0
        # ramp the stock, keep SPY flat, over a wide window
        from datetime import date as d, timedelta
        d0 = d.fromisoformat(start)
        out = []
        for i in range(40):
            day = (d0 + timedelta(days=i)).isoformat()
            close = 100.0 if symbol == "SPY" else 100.0 + i
            out.append(Bar(day, close, close, close, close, 0))
        return out

    recs = build_records([ev], tier="large", horizon=5, normalization="price",
                         price_of=price_of, earnings_series_of=lambda s: [],
                         fetch_window=fetch_window)
    assert len(recs) == 1
    assert recs[0].symbol == "NVDA"
    assert recs[0].tier == "large"
    assert recs[0].signal == 1.0 / 100.0          # SUE by price
    assert recs[0].realized is not None and recs[0].realized > 0


def test_build_records_drops_when_signal_or_realized_missing():
    ev = _ev("X", "2026-02-20", None, 4.0)        # no EPS -> no signal
    recs = build_records([ev], tier="small", horizon=5, normalization="price",
                         price_of=lambda s, d: 100.0, earnings_series_of=lambda s: [],
                         fetch_window=lambda s, a, b: [])
    assert recs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_build.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.pead_study'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/pead_study.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_pead_build.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/pead_study.py tests/test_edge_pead_build.py
git commit -m "feat(edge/pead): train/test split + record building (deps injected)"
```

---

## Task 7: Sweep + held-out evaluation

**Files:**
- Modify: `src/trading/edge/pead_study.py`
- Test: `tests/test_edge_pead_sweep.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_sweep.py
from trading.edge.portfolio import PeadRecord
from trading.edge.pead_study import sweep, Config


def _recs(signal_realized, tier="small"):
    return [PeadRecord("S%d" % i, "2026-02-%02d" % (1 + i), tier, sig, real)
            for i, (sig, real) in enumerate(signal_realized)]


def test_sweep_ranks_configs_by_net_long_short():
    # Build records per config via an injected builder keyed by config.
    good = _recs([(3.0, 0.05), (2.0, 0.03), (-2.0, -0.02), (-3.0, -0.05)])
    bad = _recs([(3.0, -0.05), (2.0, -0.03), (-2.0, 0.02), (-3.0, 0.05)])
    configs = [Config("small", 5, "price"), Config("small", 20, "price")]

    def builder(cfg, events):
        return good if cfg.horizon == 5 else bad

    ranked = sweep(configs, events=[], builder=builder)
    # Best (highest net long-short) first; the 'good' config (horizon 5) wins.
    assert ranked[0].config.horizon == 5
    assert ranked[0].net_long_short > ranked[1].net_long_short
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_sweep.py -v`
Expected: FAIL with `ImportError: cannot import name 'sweep'`

- [ ] **Step 3: Write minimal implementation (append to pead_study.py)**

```python
# src/trading/edge/pead_study.py  (append)

from dataclasses import dataclass

from trading.edge.portfolio import long_short_net


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_pead_sweep.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/pead_study.py tests/test_edge_pead_sweep.py
git commit -m "feat(edge/pead): config sweep ranked by net long-short"
```

---

## Task 8: Study report (train sweep + one held-out run)

**Files:**
- Modify: `src/trading/edge/pead_study.py`
- Test: `tests/test_edge_pead_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_report.py
from trading.edge.portfolio import PeadRecord
from trading.edge.pead_study import Config, ConfigResult, build_report


def test_report_shows_chosen_config_and_train_vs_test():
    chosen = ConfigResult(Config("small", 20, "sigma"), net_long_short=0.031, n=80)
    test_recs = [PeadRecord("A", "2026-05-01", "small", 2.0, 0.02),
                 PeadRecord("B", "2026-05-02", "small", -2.0, -0.01)]
    report = build_report(chosen, test_recs, all_ranked=[chosen])
    assert "PRE-REGISTERED CONFIG" in report
    assert "small" in report and "20" in report and "sigma" in report
    assert "TRAIN net long-short: +0.0310" in report
    assert "HELD-OUT TEST" in report
    assert "configs evaluated: 1" in report   # multiple-testing visible


def test_report_handles_empty_test():
    chosen = ConfigResult(Config("large", 5, "price"), net_long_short=0.0, n=0)
    report = build_report(chosen, [], all_ranked=[chosen])
    assert "insufficient" in report.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_report'`

- [ ] **Step 3: Write minimal implementation (append to pead_study.py)**

```python
# src/trading/edge/pead_study.py  (append)

from trading.analysis.track_record import max_drawdown, sharpe
from trading.edge.metrics import hit_rate, information_coefficient, t_statistic
from trading.edge.portfolio import bucket_returns, pnl_series


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

    from trading.edge.portfolio import long_short_net
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_pead_report.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/pead_study.py tests/test_edge_pead_report.py
git commit -m "feat(edge/pead): study report (train pick -> one held-out test run)"
```

---

## Task 9: CLI wiring + full suite green

**Files:**
- Modify: `src/trading/edge/pead_study.py` (add `run_study` + `main`)
- Modify: `README.md`
- Test: `tests/test_edge_pead_run_study.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_pead_run_study.py
from datetime import date, timedelta

from trading.data.bars import Bar
from trading.edge.events import EarningsEvent
from trading.edge.pead_study import Config, run_study


def _ev(symbol, report_date, actual, consensus):
    return EarningsEvent(symbol, report_date, report_date, actual, consensus)


def _ramp(symbol, start, end):
    d0 = date.fromisoformat(start)
    out = []
    for i in range(60):
        day = (d0 + timedelta(days=i)).isoformat()
        close = 100.0 if symbol == "SPY" else 100.0 + i   # beats drift up
        out.append(Bar(day, close, close, close, close, 0))
    return out


def test_run_study_end_to_end_with_fakes():
    # Train events (before split) + test events (after). Positive-surprise names ramp up.
    events = [_ev("A", "2026-02-10", 5.0, 4.0), _ev("B", "2026-02-12", 3.0, 4.0),
              _ev("C", "2026-04-10", 5.0, 4.0), _ev("D", "2026-04-12", 3.0, 4.0)]
    report = run_study(
        events=events, split_date="2026-03-15",
        configs=[Config("large", 5, "price")],
        price_of=lambda s, d: 100.0, earnings_series_of=lambda s: [],
        fetch_window=_ramp,
    )
    assert "MECHANICAL PEAD STUDY REPORT" in report
    assert "PRE-REGISTERED CONFIG" in report
    assert "HELD-OUT TEST" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_pead_run_study.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_study'`

- [ ] **Step 3: Write minimal implementation (append to pead_study.py)**

```python
# src/trading/edge/pead_study.py  (append)


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
    from trading.edge.sources import AlphaVantageSource
    from trading.edge.run import _load_universe

    cutoff = os.environ.get("EDGE_CUTOFF", "2026-02-01")
    split = os.environ.get("PEAD_SPLIT", "2026-04-01")

    av = AlphaVantageSource()
    src = YFinanceSource()
    events = select_post_cutoff(av.calendar(_load_universe(), earliest_report_date=cutoff),
                                earliest_report_date=cutoff)
    print(f"{len(events)} post-cutoff events; train/test split at {split}")

    tier = "small" if os.environ.get("EDGE_UNIVERSE_FILE", "").find("smid") >= 0 else "large"
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
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all tests green, including the existing suite)

- [ ] **Step 5: Add a README note and commit**

Append to the existing "Edge measurer" section in `README.md`:

```markdown
### Mechanical PEAD study (no LLM)

After the LLM edge measurer found deep reading adds nothing over a one-line EPS-surprise
rule, this studies the mechanical PEAD anomaly directly — sweeping cap-tier x horizon
(1-60d) x SUE normalization on a train window, pre-registering one config, and scoring it
ONCE on a held-out test window (anti-overfit). Net of realistic tiered trading costs.

```bash
ALPHAVANTAGE_API_KEY=... EDGE_UNIVERSE_FILE=config/edge_universe_smid.toml \
  PEAD_SPLIT=2026-04-01 uv run python -m trading.edge.pead_study
```

Spec: `docs/superpowers/specs/2026-06-16-mechanical-pead-study-design.md`.
```

```bash
git add src/trading/edge/pead_study.py tests/test_edge_pead_run_study.py README.md
git commit -m "feat(edge/pead): study CLI (python -m trading.edge.pead_study) + README"
```

---

## Self-Review Notes

- **Spec coverage:** SUE both normalizations (Task 1, spec §3) · sweep tier×horizon×threshold/norm (Tasks 7/9, §4 — note: |SUE| *threshold* is realized via quintile `frac` in `long_short_net`; explicit absolute-threshold sweep deferred as it is dominated by quintile selection) · tiered realistic costs (Task 2, §5) · portfolio sim + Sharpe/drawdown via track_record (Tasks 4/8, §6) · train/test split + pre-registration + multiple-testing visibility + forward note (Tasks 6/8/9, §7) · gate language in report (Task 8, §9) · reuse edge module, drop LLM (all tasks, §8).
- **Threshold (§4) partial:** v1 selects extremes via top/bottom quintile (`frac`) rather than an absolute |SUE| cutoff — same intent (trade only strong surprises), simpler, no extra sweep dimension. An explicit threshold sweep is a follow-up only if the quintile result warrants it (YAGNI).
- **Forward confirmation (§7.4 / §9.4):** the report prints the forward-confirmation caveat; actual forward accumulation reuses the measurer's idempotent DB pattern and is a separate run over time, not a code unit here.
- **Naming consistency:** `PeadRecord`, `Config`, `ConfigResult`, `surprise`, `sue_by_price`, `sue_by_sigma`, `prior_surprises`, `position_pnl`, `ROUND_TRIP_BPS`, `long_short_net`, `pnl_series`, `bucket_returns`, `market_adjusted_multi`, `split_events_by_date`, `build_records`, `sweep`, `build_report`, `run_study` — consistent across tasks.
- **No network in unit tests:** every test injects fakes (`price_of`, `earnings_series_of`, `fetch_window`, `builder`); no test calls Alpha Vantage or yfinance.
