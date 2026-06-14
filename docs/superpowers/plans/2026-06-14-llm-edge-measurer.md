# LLM Edge Measurer (PEAD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, batch edge measurer that tests whether deep LLM reading of earnings primary sources predicts post-earnings drift (PEAD) — market-adjusted, vs benchmarks — before any real money or trading build.

**Architecture:** New isolated package `src/trading/edge/`. It never touches the trading loop. Pure logic (event selection, realized-return math, all metrics, benchmarks, report) is TDD'd against fakes; network adapters (FMP transcripts/calendar, EDGAR filings, Claude calls) are thin, graceful-degrading classes smoke-tested separately — exactly the split the repo already uses (`yfinance_source.py`/`news.py` pure parts + smoke scripts). Lookahead is solved by only selecting earnings events dated after the model's knowledge cutoff (genuinely out-of-sample) plus a memory-probe filter. Results land in a dedicated SQLite DB (`edge_predictions` table) that doubles as the forward-accumulation journal (approach A→C).

**Tech Stack:** Python 3.12, pydantic (LLM structured output), anthropic SDK (`messages.parse`), sqlite3, yfinance (prices), pytest. No new heavy deps — Spearman/stats are implemented by hand (repo avoids scipy).

**Spec:** `docs/superpowers/specs/2026-06-14-llm-edge-measurer-design.md`

---

## File Structure

All new files under `src/trading/edge/` unless noted.

- `__init__.py` — package marker.
- `events.py` — `EarningsEvent` dataclass + `select_post_cutoff` (pure selection).
- `schema.py` — pydantic `EdgePrediction`, `MemoryProbe`; `signal_value` mapping (pure).
- `documents.py` — `EventDocuments` dataclass, `DocumentSource` Protocol, `FakeDocumentSource`.
- `realize.py` — `forward_return`, `market_adjusted_return` (pure) + thin `realized_market_adjusted` fetch wrapper.
- `metrics.py` — `information_coefficient`, `long_short_spread`, `hit_rate`, `t_statistic`, `calibration` (all pure).
- `benchmarks.py` — `dumb_pead_signals`, `COIN_FLIP_HIT_RATE` (pure).
- `store.py` — `EDGE_SCHEMA_SQL`, `init_edge_db`, `EdgeRepository` (sqlite).
- `prompts.py` — predict/probe prompt builders + system strings (pure).
- `predict.py` — `EdgePredictor` (the only Claude caller here).
- `report.py` — `build_report` (pure, text from scored rows).
- `run.py` — batch runner `python -m trading.edge.run`, dependency-injected.
- `sources.py` — `FMPSource` (transcripts + earnings calendar), graceful-degrade network adapter. EDGAR 8-K/10-Q fetch is deferred: the pilot is transcript-only (spec §4 — add filings only if there's a glimmer), and `EventDocuments` already carries blank `press_release`/`mdna` slots for that later depth.
- `scripts/smoke_edge.py` — manual smoke for the network adapters (not in CI).

Tests mirror under `tests/` as `test_edge_*.py`.

---

## Task 1: Edge DB schema + repository

**Files:**
- Create: `src/trading/edge/__init__.py`
- Create: `src/trading/edge/store.py`
- Test: `tests/test_edge_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_store.py
from trading.persistence.db import connect
from trading.edge.store import init_edge_db, EdgeRepository


def _repo():
    conn = connect(":memory:")
    init_edge_db(conn)
    return EdgeRepository(conn)


def test_record_and_fetch_roundtrip():
    repo = _repo()
    rid = repo.record(
        symbol="NVDA", report_date="2026-02-21", decision_date="2026-02-23",
        horizon_days=5, direction="up", magnitude_pct=3.0, confidence=0.7,
        rationale="confident CFO tone", knows_outcome=False,
        eps_actual=5.1, eps_consensus=4.6, model="claude-opus-4-8",
    )
    rows = repo.all()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["direction"] == "up"
    assert rows[0]["realized_return"] is None
    assert rid == rows[0]["id"]


def test_set_realized_and_scored_filter():
    repo = _repo()
    a = repo.record(symbol="A", report_date="2026-02-01", decision_date="2026-02-03",
                    horizon_days=5, direction="up", magnitude_pct=2.0, confidence=0.6,
                    rationale="", knows_outcome=False, eps_actual=None,
                    eps_consensus=None, model="m")
    repo.record(symbol="B", report_date="2026-02-01", decision_date="2026-02-03",
                horizon_days=5, direction="down", magnitude_pct=1.0, confidence=0.5,
                rationale="", knows_outcome=True, eps_actual=None,
                eps_consensus=None, model="m")
    repo.set_realized(a, 0.012)
    scored = repo.scored()
    # Only the row with a realized return AND knows_outcome == False qualifies.
    assert [r["symbol"] for r in scored] == ["A"]
    assert scored[0]["realized_return"] == 0.012
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/__init__.py
```

```python
# src/trading/edge/store.py
from __future__ import annotations

import sqlite3

EDGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS edge_predictions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    report_date      TEXT NOT NULL,        -- YYYY-MM-DD results released
    decision_date    TEXT NOT NULL,        -- YYYY-MM-DD point-in-time boundary
    horizon_days     INTEGER NOT NULL,
    direction        TEXT NOT NULL,        -- up | down | neutral
    magnitude_pct    REAL NOT NULL,        -- expected absolute move vs market, %
    confidence       REAL NOT NULL,        -- 0..1
    rationale        TEXT NOT NULL,
    knows_outcome    INTEGER NOT NULL,     -- 1 if memory-probe says model knows the future
    eps_actual       REAL,
    eps_consensus    REAL,
    model            TEXT NOT NULL,
    realized_return  REAL                  -- market-adjusted, filled in after the horizon
);
"""


def init_edge_db(conn: sqlite3.Connection) -> None:
    conn.executescript(EDGE_SCHEMA_SQL)
    conn.commit()


class EdgeRepository:
    """Append-only journal of edge predictions and their realized outcomes."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, *, symbol: str, report_date: str, decision_date: str,
               horizon_days: int, direction: str, magnitude_pct: float,
               confidence: float, rationale: str, knows_outcome: bool,
               eps_actual: float | None, eps_consensus: float | None,
               model: str) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO edge_predictions (
                symbol, report_date, decision_date, horizon_days, direction,
                magnitude_pct, confidence, rationale, knows_outcome,
                eps_actual, eps_consensus, model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, report_date, decision_date, horizon_days, direction,
             magnitude_pct, confidence, rationale, int(knows_outcome),
             eps_actual, eps_consensus, model),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_realized(self, prediction_id: int, realized_return: float) -> None:
        self.conn.execute(
            "UPDATE edge_predictions SET realized_return = ? WHERE id = ?",
            (realized_return, prediction_id),
        )
        self.conn.commit()

    def all(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM edge_predictions ORDER BY id").fetchall()

    def scored(self) -> list[sqlite3.Row]:
        """Rows usable for metrics: realized known AND the model was blind to outcome."""
        return self.conn.execute(
            "SELECT * FROM edge_predictions "
            "WHERE realized_return IS NOT NULL AND knows_outcome = 0 "
            "ORDER BY id"
        ).fetchall()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_store.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/__init__.py src/trading/edge/store.py tests/test_edge_store.py
git commit -m "feat(edge): edge_predictions store + repository"
```

---

## Task 2: Earnings events + post-cutoff selection

**Files:**
- Create: `src/trading/edge/events.py`
- Test: `tests/test_edge_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_events.py
from trading.edge.events import EarningsEvent, select_post_cutoff


def _ev(symbol, report_date):
    return EarningsEvent(symbol=symbol, report_date=report_date,
                         decision_date=report_date)


def test_keeps_only_events_on_or_after_earliest():
    events = [_ev("A", "2026-01-10"), _ev("B", "2026-02-15"), _ev("C", "2026-05-01")]
    kept = select_post_cutoff(events, earliest_report_date="2026-02-01")
    assert [e.symbol for e in kept] == ["B", "C"]


def test_empty_when_all_before_cutoff():
    events = [_ev("A", "2025-12-31")]
    assert select_post_cutoff(events, earliest_report_date="2026-02-01") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.events'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/events.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EarningsEvent:
    """One earnings report we may test the model on.

    `report_date` is when results were released (after the close). `decision_date` is
    the trading day we treat as the point-in-time boundary — the model sees only data
    dated on or before it, and a hypothetical position opens at its close.
    `eps_actual`/`eps_consensus` feed the dumb-PEAD benchmark (None if unavailable).
    """
    symbol: str
    report_date: str            # YYYY-MM-DD
    decision_date: str          # YYYY-MM-DD
    eps_actual: float | None = None
    eps_consensus: float | None = None


def select_post_cutoff(events: list[EarningsEvent],
                       earliest_report_date: str) -> list[EarningsEvent]:
    """Keep only events the model is genuinely blind to: report_date on or after
    `earliest_report_date` (set by the caller to the model's knowledge cutoff plus a
    safety buffer). ISO dates compare correctly as strings."""
    return [e for e in events if e.report_date >= earliest_report_date]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_events.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/events.py tests/test_edge_events.py
git commit -m "feat(edge): EarningsEvent + post-cutoff selection"
```

---

## Task 3: Prediction schema + signal mapping

**Files:**
- Create: `src/trading/edge/schema.py`
- Test: `tests/test_edge_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_schema.py
from trading.edge.schema import EdgePrediction, MemoryProbe, signal_value


def test_edge_prediction_parses():
    p = EdgePrediction(direction="up", magnitude_pct=3.0, confidence=0.7,
                       rationale="tone")
    assert p.direction == "up"
    assert p.confidence == 0.7


def test_signal_value_signs_by_direction():
    assert signal_value("up", 3.0) == 3.0
    assert signal_value("down", 3.0) == -3.0
    assert signal_value("neutral", 3.0) == 0.0


def test_memory_probe_parses():
    m = MemoryProbe(knows_outcome=True, evidence="I recall the stock jumped")
    assert m.knows_outcome is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/schema.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class EdgePrediction(BaseModel):
    """The strict shape the model must return for one earnings event.

    The horizon is fixed by configuration (not chosen by the model), so it is not a
    field here — it is stamped by the runner. `magnitude_pct` is the expected absolute
    move vs the market, in percent (always >= 0); sign comes from `direction`.
    """
    direction: Literal["up", "down", "neutral"]
    magnitude_pct: float
    confidence: float
    rationale: str


class MemoryProbe(BaseModel):
    """Did the model already know how this stock moved after this report? If so the
    event is not out-of-sample and must be dropped."""
    knows_outcome: bool
    evidence: str


def signal_value(direction: str, magnitude_pct: float) -> float:
    """Signed expected move used as the ranking signal in metrics. Neutral -> 0."""
    if direction == "up":
        return magnitude_pct
    if direction == "down":
        return -magnitude_pct
    return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_schema.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/schema.py tests/test_edge_schema.py
git commit -m "feat(edge): EdgePrediction/MemoryProbe schema + signal_value"
```

---

## Task 4: Realized market-adjusted forward return

**Files:**
- Create: `src/trading/edge/realize.py`
- Test: `tests/test_edge_realize.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_realize.py
from trading.data.bars import Bar
from trading.edge.realize import forward_return, market_adjusted_return


def _bar(date, close):
    return Bar(date=date, open=close, high=close, low=close, close=close, volume=0)


def test_forward_return_picks_entry_and_exit_n_days_later():
    bars = [_bar("2026-02-23", 100.0), _bar("2026-02-24", 101.0),
            _bar("2026-02-25", 102.0), _bar("2026-02-26", 103.0)]
    # entry = first bar on/after decision_date (100), exit = 2 trading days later (102).
    assert forward_return(bars, "2026-02-23", horizon_days=2) == (102.0 / 100.0 - 1.0)


def test_forward_return_none_when_not_enough_forward_bars():
    bars = [_bar("2026-02-23", 100.0), _bar("2026-02-24", 101.0)]
    assert forward_return(bars, "2026-02-23", horizon_days=5) is None


def test_market_adjusted_subtracts_spy():
    stock = [_bar("2026-02-23", 100.0), _bar("2026-02-24", 110.0)]   # +10%
    spy = [_bar("2026-02-23", 100.0), _bar("2026-02-24", 104.0)]     # +4%
    adj = market_adjusted_return(stock, spy, "2026-02-23", horizon_days=1)
    assert abs(adj - 0.06) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_realize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.realize'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/realize.py
from __future__ import annotations

from datetime import date, timedelta

from trading.data.bars import Bar, MarketDataSource


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


def realized_market_adjusted(source: MarketDataSource, symbol: str,
                             decision_date: str, horizon_days: int,
                             pad_days: int = 10) -> float | None:
    """Thin wrapper: fetch enough bars to cover the forward window, then delegate to
    the pure math. Returns None on any data gap (never raises on missing forward data).

    The fetch anchors on a date well past the horizon so weekends/holidays are covered.
    """
    span = horizon_days * 2 + pad_days
    anchor = _add_calendar_days(decision_date, span)
    try:
        stock = source.history(symbol, days=span + 5, as_of_date=anchor)
        spy = source.history("SPY", days=span + 5, as_of_date=anchor)
    except KeyError:
        return None
    return market_adjusted_return(stock, spy, decision_date, horizon_days)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_realize.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/realize.py tests/test_edge_realize.py
git commit -m "feat(edge): market-adjusted forward-return math"
```

---

## Task 5: Metrics — information coefficient (Spearman)

**Files:**
- Create: `src/trading/edge/metrics.py`
- Test: `tests/test_edge_metrics_ic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_metrics_ic.py
from trading.edge.metrics import information_coefficient


def test_perfect_rank_agreement_is_one():
    signals = [1.0, 2.0, 3.0, 4.0]
    realized = [0.01, 0.02, 0.03, 0.04]
    assert abs(information_coefficient(signals, realized) - 1.0) < 1e-9


def test_perfect_inverse_is_minus_one():
    signals = [1.0, 2.0, 3.0, 4.0]
    realized = [0.04, 0.03, 0.02, 0.01]
    assert abs(information_coefficient(signals, realized) + 1.0) < 1e-9


def test_handles_ties_via_average_ranks():
    signals = [1.0, 1.0, 2.0, 3.0]
    realized = [0.01, 0.02, 0.03, 0.04]
    ic = information_coefficient(signals, realized)
    assert -1.0 <= ic <= 1.0


def test_too_few_points_is_zero():
    assert information_coefficient([1.0], [0.1]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_metrics_ic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.metrics'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/metrics.py
from __future__ import annotations

import math


def _ranks(xs: list[float]) -> list[float]:
    """Average ranks (1-based), tied values share the mean of their positions."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # average of 1-based positions i+1..j+1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    return cov / math.sqrt(vx * vy)


def information_coefficient(signals: list[float], realized: list[float]) -> float:
    """Spearman rank correlation between predicted signal and realized return.
    0.0 when fewer than 2 points or a degenerate (constant) series."""
    if len(signals) < 2 or len(signals) != len(realized):
        return 0.0
    return _pearson(_ranks(signals), _ranks(realized))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_metrics_ic.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/metrics.py tests/test_edge_metrics_ic.py
git commit -m "feat(edge): information coefficient (hand-rolled Spearman)"
```

---

## Task 6: Metrics — long-short spread after costs

**Files:**
- Modify: `src/trading/edge/metrics.py`
- Test: `tests/test_edge_metrics_longshort.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_metrics_longshort.py
from trading.edge.metrics import long_short_spread


def test_gross_spread_top_minus_bottom():
    # 5 items, frac 0.2 -> 1 long (top signal) and 1 short (bottom signal).
    signals = [5.0, 4.0, 0.0, -4.0, -5.0]
    realized = [0.03, 0.02, 0.00, -0.01, -0.04]
    # long = realized of signal 5.0 (0.03); short = realized of signal -5.0 (-0.04).
    # gross = 0.03 - (-0.04) = 0.07; costs 0 bps -> 0.07.
    spread = long_short_spread(signals, realized, cost_bps=0.0, frac=0.2)
    assert abs(spread - 0.07) < 1e-9


def test_costs_reduce_spread():
    signals = [5.0, -5.0]
    realized = [0.03, -0.04]
    gross = long_short_spread(signals, realized, cost_bps=0.0, frac=0.5)
    net = long_short_spread(signals, realized, cost_bps=10.0, frac=0.5)
    assert net < gross
    # 10 bps = 0.001 per side; long+short, round trip each -> 4 * 0.001 = 0.004.
    assert abs((gross - net) - 0.004) < 1e-9


def test_empty_returns_zero():
    assert long_short_spread([], [], cost_bps=10.0, frac=0.2) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_metrics_longshort.py -v`
Expected: FAIL with `ImportError: cannot import name 'long_short_spread'`

- [ ] **Step 3: Write minimal implementation (append to metrics.py)**

```python
# src/trading/edge/metrics.py  (append)


def long_short_spread(signals: list[float], realized: list[float],
                      cost_bps: float = 10.0, frac: float = 0.2) -> float:
    """Mean realized return of the top-`frac` signals (long) minus the bottom-`frac`
    (short), net of trading costs. This is the "how much money" metric.

    Costs: `cost_bps` is one-way per leg in basis points. The portfolio holds a long
    and a short, each a round trip (entry + exit), so total cost = 4 * cost_bps/10_000.
    """
    n = len(signals)
    if n == 0 or n != len(realized):
        return 0.0
    pairs = sorted(zip(signals, realized), key=lambda p: p[0], reverse=True)
    k = max(1, int(n * frac))
    longs = [r for _, r in pairs[:k]]
    shorts = [r for _, r in pairs[-k:]]
    gross = sum(longs) / len(longs) - sum(shorts) / len(shorts)
    cost = 4.0 * (cost_bps / 10_000.0)
    return gross - cost
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_metrics_longshort.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/metrics.py tests/test_edge_metrics_longshort.py
git commit -m "feat(edge): long-short spread net of costs"
```

---

## Task 7: Metrics — hit rate, t-statistic, calibration

**Files:**
- Modify: `src/trading/edge/metrics.py`
- Test: `tests/test_edge_metrics_calibration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_metrics_calibration.py
from trading.edge.metrics import hit_rate, t_statistic, calibration


def test_hit_rate_ignores_zero_signal():
    signals = [2.0, -2.0, 0.0, 3.0]
    realized = [0.01, 0.01, -0.5, 0.02]   # zero-signal row excluded entirely
    # up&+ = hit, down&+ = miss, (skip), up&+ = hit -> 2/3.
    assert abs(hit_rate(signals, realized) - 2.0 / 3.0) < 1e-9


def test_t_statistic_positive_for_consistently_positive():
    assert t_statistic([0.01, 0.012, 0.011, 0.009]) > 2.0


def test_t_statistic_zero_for_too_few():
    assert t_statistic([0.01]) == 0.0


def test_calibration_buckets_by_confidence():
    confidences = [0.2, 0.4, 0.8, 0.9]
    signals = [1.0, 1.0, 1.0, 1.0]
    realized = [-0.01, -0.01, 0.01, 0.01]   # low-conf wrong, high-conf right
    buckets = calibration(confidences, signals, realized, edges=[0.0, 0.5, 1.0])
    # [0.0,0.5): 2 items, 0 hits; [0.5,1.0]: 2 items, 2 hits.
    assert buckets[0] == (0.0, 0.5, 0.0, 2)
    assert buckets[1] == (0.5, 1.0, 1.0, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_metrics_calibration.py -v`
Expected: FAIL with `ImportError: cannot import name 'hit_rate'`

- [ ] **Step 3: Write minimal implementation (append to metrics.py)**

```python
# src/trading/edge/metrics.py  (append)


def _hit(signal: float, realized: float) -> bool:
    return (signal > 0 and realized > 0) or (signal < 0 and realized < 0)


def hit_rate(signals: list[float], realized: list[float]) -> float:
    """Fraction of non-zero signals whose direction matched the realized sign.
    0.0 when there are no directional signals."""
    pairs = [(s, r) for s, r in zip(signals, realized) if s != 0]
    if not pairs:
        return 0.0
    return sum(1 for s, r in pairs if _hit(s, r)) / len(pairs)


def t_statistic(values: list[float]) -> float:
    """One-sample t-stat of the mean against zero. 0.0 for fewer than 2 points or no
    variation."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var == 0:
        return 0.0
    return mean / math.sqrt(var / n)


def calibration(confidences: list[float], signals: list[float],
                realized: list[float],
                edges: list[float]) -> list[tuple[float, float, float, int]]:
    """Hit rate per confidence bucket. `edges` are bucket boundaries (e.g. [0,0.5,1]).
    Each bucket [low, high) — the last is closed on the right. Returns
    (low, high, hit_rate, count) per bucket. A monotonic rise = the model is calibrated.
    """
    out: list[tuple[float, float, float, int]] = []
    for b in range(len(edges) - 1):
        low, high = edges[b], edges[b + 1]
        last = b == len(edges) - 2
        idx = [i for i, c in enumerate(confidences)
               if (low <= c <= high) if last else (low <= c < high)]
        if not idx:
            out.append((low, high, 0.0, 0))
            continue
        hits = sum(1 for i in idx if _hit(signals[i], realized[i]))
        out.append((low, high, hits / len(idx), len(idx)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_metrics_calibration.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/metrics.py tests/test_edge_metrics_calibration.py
git commit -m "feat(edge): hit rate, t-statistic, calibration buckets"
```

---

## Task 8: Benchmarks — coin flip + dumb mechanical PEAD

**Files:**
- Create: `src/trading/edge/benchmarks.py`
- Test: `tests/test_edge_benchmarks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_benchmarks.py
from trading.edge.events import EarningsEvent
from trading.edge.benchmarks import dumb_pead_signals, COIN_FLIP_HIT_RATE


def _ev(symbol, actual, consensus):
    return EarningsEvent(symbol=symbol, report_date="2026-02-01",
                         decision_date="2026-02-03",
                         eps_actual=actual, eps_consensus=consensus)


def test_dumb_pead_signs_by_eps_surprise():
    events = [_ev("A", 5.0, 4.0), _ev("B", 3.0, 4.0), _ev("C", 4.0, 4.0)]
    assert dumb_pead_signals(events) == [1.0, -1.0, 0.0]


def test_dumb_pead_zero_when_eps_missing():
    assert dumb_pead_signals([_ev("A", None, 4.0)]) == [0.0]


def test_coin_flip_constant():
    assert COIN_FLIP_HIT_RATE == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_benchmarks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.benchmarks'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/benchmarks.py
from __future__ import annotations

from trading.edge.events import EarningsEvent

COIN_FLIP_HIT_RATE = 0.5


def dumb_pead_signals(events: list[EarningsEvent]) -> list[float]:
    """The mechanical PEAD baseline: +1 if EPS beat consensus, -1 if missed, 0 if in
    line or data missing. If deep reading can't beat this, reading transcripts is
    pointless. Aligned 1:1 with `events`."""
    out: list[float] = []
    for e in events:
        if e.eps_actual is None or e.eps_consensus is None:
            out.append(0.0)
        elif e.eps_actual > e.eps_consensus:
            out.append(1.0)
        elif e.eps_actual < e.eps_consensus:
            out.append(-1.0)
        else:
            out.append(0.0)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_benchmarks.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/benchmarks.py tests/test_edge_benchmarks.py
git commit -m "feat(edge): coin-flip + dumb-PEAD benchmarks"
```

---

## Task 9: Documents + prompt builders

**Files:**
- Create: `src/trading/edge/documents.py`
- Create: `src/trading/edge/prompts.py`
- Test: `tests/test_edge_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_prompts.py
from trading.edge.events import EarningsEvent
from trading.edge.documents import EventDocuments, FakeDocumentSource
from trading.edge.prompts import (build_predict_user_prompt, build_probe_user_prompt,
                                   PREDICT_SYSTEM, PROBE_SYSTEM)


def _docs():
    return EventDocuments(symbol="NVDA", decision_date="2026-02-23",
                          transcript="CEO: demand was exceptional...",
                          press_release="Q4 revenue up 80%", mdna="risks: supply")


def test_fake_document_source_returns_registered_docs():
    ev = EarningsEvent("NVDA", "2026-02-21", "2026-02-23")
    src = FakeDocumentSource({"NVDA": _docs()})
    assert src.documents(ev).transcript.startswith("CEO:")


def test_predict_prompt_includes_documents_and_horizon():
    prompt = build_predict_user_prompt(_docs(), horizon_days=5)
    assert "NVDA" in prompt
    assert "exceptional" in prompt          # transcript embedded
    assert "5" in prompt                      # horizon stated
    assert "market-adjusted" in PREDICT_SYSTEM.lower()


def test_probe_prompt_names_symbol_and_date():
    ev = EarningsEvent("NVDA", "2026-02-21", "2026-02-23")
    prompt = build_probe_user_prompt(ev)
    assert "NVDA" in prompt and "2026-02-21" in prompt
    assert "outcome" in PROBE_SYSTEM.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.documents'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/documents.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading.edge.events import EarningsEvent


@dataclass(frozen=True)
class EventDocuments:
    """Point-in-time primary sources for one event (all dated <= decision_date)."""
    symbol: str
    decision_date: str
    transcript: str
    press_release: str = ""
    mdna: str = ""


class DocumentSource(Protocol):
    def documents(self, event: EarningsEvent) -> EventDocuments: ...


class FakeDocumentSource:
    """Deterministic documents for tests and offline runs. Satisfies DocumentSource."""

    def __init__(self, by_symbol: dict[str, EventDocuments]) -> None:
        self._by_symbol = by_symbol

    def documents(self, event: EarningsEvent) -> EventDocuments:
        return self._by_symbol[event.symbol]
```

```python
# src/trading/edge/prompts.py
from __future__ import annotations

from trading.edge.documents import EventDocuments
from trading.edge.events import EarningsEvent

PREDICT_SYSTEM = (
    "You are an equity analyst reading the primary sources from a company's quarterly "
    "earnings release. Read the earnings-call transcript closely — pay attention to "
    "management tone, hedging, and changes in guidance language in the Q&A, not just the "
    "headline numbers. Predict the stock's MARKET-ADJUSTED move (return minus SPY) over "
    "the stated horizon. Return only the structured fields. Base your view solely on the "
    "material provided; do not rely on any later knowledge."
)

PROBE_SYSTEM = (
    "You are checking your own knowledge. Answer honestly whether you already know the "
    "actual stock-price outcome that followed this specific earnings report. If you "
    "recall or can infer the outcome from training knowledge, set knows_outcome true."
)


def build_predict_user_prompt(docs: EventDocuments, horizon_days: int) -> str:
    return (
        f"Company: {docs.symbol}\n"
        f"Decision date (you know nothing after this): {docs.decision_date}\n"
        f"Forecast horizon: {horizon_days} trading days, market-adjusted.\n\n"
        f"=== EARNINGS CALL TRANSCRIPT ===\n{docs.transcript}\n\n"
        f"=== PRESS RELEASE (8-K) ===\n{docs.press_release}\n\n"
        f"=== 10-Q MD&A EXCERPT ===\n{docs.mdna}\n"
    )


def build_probe_user_prompt(event: EarningsEvent) -> str:
    return (
        f"Do you already know how {event.symbol} stock moved in the days after its "
        f"earnings report dated {event.report_date}?"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_prompts.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/documents.py src/trading/edge/prompts.py tests/test_edge_prompts.py
git commit -m "feat(edge): EventDocuments, document source, prompt builders"
```

---

## Task 10: Edge predictor (Claude caller)

**Files:**
- Create: `src/trading/edge/predict.py`
- Test: `tests/test_edge_predict.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_predict.py
from trading.edge.events import EarningsEvent
from trading.edge.documents import EventDocuments
from trading.edge.schema import EdgePrediction, MemoryProbe
from trading.edge.predict import EdgePredictor


class _Parsed:
    def __init__(self, obj):
        self.parsed_output = obj


class _FakeMessages:
    def __init__(self, obj):
        self._obj = obj
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _Parsed(self._obj)


class _FakeClient:
    def __init__(self, obj):
        self.messages = _FakeMessages(obj)


def test_predict_returns_parsed_prediction():
    pred = EdgePrediction(direction="up", magnitude_pct=2.0, confidence=0.6, rationale="x")
    predictor = EdgePredictor(client=_FakeClient(pred), model="m")
    docs = EventDocuments("NVDA", "2026-02-23", transcript="t")
    out = predictor.predict(docs, horizon_days=5)
    assert out.direction == "up"
    assert predictor.client.messages.calls[0]["output_format"] is EdgePrediction


def test_memory_probe_returns_parsed_probe():
    probe = MemoryProbe(knows_outcome=True, evidence="recall")
    predictor = EdgePredictor(client=_FakeClient(probe), model="m")
    ev = EarningsEvent("NVDA", "2026-02-21", "2026-02-23")
    out = predictor.memory_probe(ev)
    assert out.knows_outcome is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_predict.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.predict'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/predict.py
from __future__ import annotations

import os

from trading.edge.documents import EventDocuments
from trading.edge.events import EarningsEvent
from trading.edge.prompts import (PREDICT_SYSTEM, PROBE_SYSTEM,
                                   build_predict_user_prompt, build_probe_user_prompt)
from trading.edge.schema import EdgePrediction, MemoryProbe

DEFAULT_MODEL = os.environ.get("EDGE_MODEL", "claude-opus-4-8")
MAX_TOKENS = 8192


class EdgePredictor:
    """The only Claude caller in the edge module. One deep-read prediction per event,
    plus a memory-probe to drop events whose outcome the model already knows."""

    def __init__(self, client=None, model: str = DEFAULT_MODEL) -> None:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def predict(self, docs: EventDocuments, horizon_days: int) -> EdgePrediction:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=PREDICT_SYSTEM,
            messages=[{"role": "user",
                       "content": build_predict_user_prompt(docs, horizon_days)}],
            output_format=EdgePrediction,
        )
        return response.parsed_output

    def memory_probe(self, event: EarningsEvent) -> MemoryProbe:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=PROBE_SYSTEM,
            messages=[{"role": "user", "content": build_probe_user_prompt(event)}],
            output_format=MemoryProbe,
        )
        return response.parsed_output
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_predict.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/predict.py tests/test_edge_predict.py
git commit -m "feat(edge): EdgePredictor — deep-read prediction + memory probe"
```

---

## Task 11: Report assembly

**Files:**
- Create: `src/trading/edge/report.py`
- Test: `tests/test_edge_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_report.py
from trading.persistence.db import connect
from trading.edge.store import init_edge_db, EdgeRepository
from trading.edge.report import build_report


def _repo_with_rows():
    conn = connect(":memory:")
    init_edge_db(conn)
    repo = EdgeRepository(conn)
    # Two scored, blind rows where the up-call won and the down-call won.
    a = repo.record(symbol="A", report_date="2026-02-01", decision_date="2026-02-03",
                    horizon_days=5, direction="up", magnitude_pct=2.0, confidence=0.8,
                    rationale="", knows_outcome=False, eps_actual=5.0,
                    eps_consensus=4.0, model="m")
    b = repo.record(symbol="B", report_date="2026-02-01", decision_date="2026-02-03",
                    horizon_days=5, direction="down", magnitude_pct=2.0, confidence=0.7,
                    rationale="", knows_outcome=False, eps_actual=3.0,
                    eps_consensus=4.0, model="m")
    repo.set_realized(a, 0.03)
    repo.set_realized(b, -0.02)
    return repo


def test_report_contains_core_sections():
    report = build_report(_repo_with_rows().scored())
    assert "Sample size: 2" in report
    assert "Information coefficient" in report
    assert "Long-short" in report
    assert "Hit rate" in report
    assert "dumb PEAD" in report


def test_report_handles_empty_sample():
    conn = connect(":memory:")
    init_edge_db(conn)
    report = build_report(EdgeRepository(conn).scored())
    assert "Sample size: 0" in report
    assert "insufficient data" in report.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/report.py
from __future__ import annotations

import sqlite3

from trading.edge.benchmarks import COIN_FLIP_HIT_RATE
from trading.edge.metrics import (calibration, hit_rate, information_coefficient,
                                   long_short_spread, t_statistic)
from trading.edge.schema import signal_value


def build_report(rows: list[sqlite3.Row]) -> str:
    """Human-readable edge report from scored, blind prediction rows. Pure: takes
    already-realized rows, computes every metric, compares to benchmarks."""
    n = len(rows)
    lines = ["=== LLM EDGE MEASURER REPORT ===", f"Sample size: {n}"]
    if n < 2:
        lines.append("Result: insufficient data — need more events to conclude.")
        return "\n".join(lines)

    signals = [signal_value(r["direction"], r["magnitude_pct"]) for r in rows]
    realized = [r["realized_return"] for r in rows]

    # Dumb-PEAD baseline signal from EPS surprise on the same rows.
    pead = []
    for r in rows:
        a, c = r["eps_actual"], r["eps_consensus"]
        pead.append(0.0 if a is None or c is None else (1.0 if a > c else -1.0 if a < c else 0.0))

    ic = information_coefficient(signals, realized)
    pead_ic = information_coefficient(pead, realized)
    ls = long_short_spread(signals, realized)
    hr = hit_rate(signals, realized)
    long_returns = [s if (sig := signals[i]) >= 0 else -s
                    for i, s in enumerate(realized)]  # directional P&L per call
    tstat = t_statistic(long_returns)
    cal = calibration([r["confidence"] for r in rows], signals, realized,
                      edges=[0.0, 0.5, 0.75, 1.0])

    lines += [
        f"Information coefficient (LLM): {ic:+.3f}   vs dumb PEAD: {pead_ic:+.3f}",
        f"Long-short spread (after costs): {ls:+.4f}",
        f"Hit rate: {hr:.1%}   vs coin flip: {COIN_FLIP_HIT_RATE:.1%}",
        f"Directional t-statistic: {tstat:+.2f}",
        "Calibration by confidence (low, high, hit-rate, n):",
    ]
    for low, high, rate, count in cal:
        lines.append(f"  [{low:.2f}, {high:.2f}]: {rate:.1%} ({count})")
    lines.append("")
    verdict = ("LLM beats dumb PEAD" if ic > pead_ic and ls > 0
               else "LLM does NOT clear dumb PEAD — deep reading unjustified")
    lines.append(f"Verdict signal: {verdict}")
    lines.append("Caveat: small post-cutoff sample; confirm any edge on a forward window.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_report.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/edge/report.py tests/test_edge_report.py
git commit -m "feat(edge): edge report with benchmarks and calibration"
```

---

## Task 12: Batch runner (dependency-injected) + integration test

**Files:**
- Create: `src/trading/edge/run.py`
- Test: `tests/test_edge_run.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_run.py
from trading.data.bars import Bar
from trading.edge.events import EarningsEvent
from trading.edge.documents import EventDocuments, FakeDocumentSource
from trading.edge.schema import EdgePrediction, MemoryProbe
from trading.edge.store import init_edge_db, EdgeRepository
from trading.persistence.db import connect
from trading.edge.run import run_measurement


class _FakeSource:
    """MarketDataSource returning a fixed upward ramp for any symbol except SPY (flat)."""

    def history(self, symbol, days, as_of_date=None):
        base = [Bar(f"2026-02-{d:02d}", 100, 100, 100,
                    100.0 if symbol == "SPY" else 100.0 + d, 0)
                for d in range(23, 23 + 12)]
        return base

    def latest_price(self, symbol, as_of_date=None):
        return self.history(symbol, 1)[-1].close


class _FakePredictor:
    def __init__(self):
        self.model = "fake"

    def memory_probe(self, event):
        # The model "remembers" SKIP only.
        return MemoryProbe(knows_outcome=event.symbol == "SKIP", evidence="")

    def predict(self, docs, horizon_days):
        return EdgePrediction(direction="up", magnitude_pct=2.0, confidence=0.7,
                              rationale="up")


def test_run_skips_remembered_events_and_scores_the_rest():
    conn = connect(":memory:")
    init_edge_db(conn)
    repo = EdgeRepository(conn)
    events = [
        EarningsEvent("NVDA", "2026-02-21", "2026-02-23", 5.0, 4.0),
        EarningsEvent("SKIP", "2026-02-21", "2026-02-23", 5.0, 4.0),
    ]
    docs = {e.symbol: EventDocuments(e.symbol, e.decision_date, transcript="t")
            for e in events}
    report = run_measurement(
        events=events, source=_FakeSource(),
        doc_source=FakeDocumentSource(docs), predictor=_FakePredictor(),
        repo=repo, horizon_days=5,
    )
    rows = repo.all()
    # Both recorded, but SKIP flagged knows_outcome -> excluded from scored().
    assert len(rows) == 2
    assert len(repo.scored()) == 1
    assert repo.scored()[0]["symbol"] == "NVDA"
    assert repo.scored()[0]["realized_return"] is not None
    assert "EDGE MEASURER REPORT" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.run'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/run.py
from __future__ import annotations

from trading.data.bars import MarketDataSource
from trading.edge.documents import DocumentSource
from trading.edge.events import EarningsEvent
from trading.edge.realize import realized_market_adjusted
from trading.edge.report import build_report
from trading.edge.store import EdgeRepository


def run_measurement(*, events: list[EarningsEvent], source: MarketDataSource,
                    doc_source: DocumentSource, predictor, repo: EdgeRepository,
                    horizon_days: int) -> str:
    """Run the batch over already-selected (post-cutoff) events and return the report.

    Per event: memory-probe -> fetch docs -> predict -> record -> realize -> store.
    A memory-probe hit is still recorded (with knows_outcome=1) but excluded from
    scoring. Any per-event data failure degrades to skipping that event's realized
    return — it never aborts the batch.
    """
    for event in events:
        probe = predictor.memory_probe(event)
        try:
            docs = doc_source.documents(event)
        except Exception:
            continue
        pred = predictor.predict(docs, horizon_days)
        pid = repo.record(
            symbol=event.symbol, report_date=event.report_date,
            decision_date=event.decision_date, horizon_days=horizon_days,
            direction=pred.direction, magnitude_pct=pred.magnitude_pct,
            confidence=pred.confidence, rationale=pred.rationale,
            knows_outcome=probe.knows_outcome, eps_actual=event.eps_actual,
            eps_consensus=event.eps_consensus, model=predictor.model,
        )
        realized = realized_market_adjusted(source, event.symbol,
                                            event.decision_date, horizon_days)
        if realized is not None:
            repo.set_realized(pid, realized)
    return build_report(repo.scored())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_edge_run.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the whole edge suite + commit**

Run: `uv run pytest tests/test_edge_*.py -v`
Expected: PASS (all green)

```bash
git add src/trading/edge/run.py tests/test_edge_run.py
git commit -m "feat(edge): batch runner wiring probe->docs->predict->realize"
```

---

## Task 13: Network adapters (FMP transcripts/calendar + EDGAR filings) + CLI entry

**Files:**
- Create: `src/trading/edge/sources.py`
- Create: `scripts/smoke_edge.py`
- Modify: `src/trading/edge/run.py` (add `main()` CLI wiring real sources)
- Test: `tests/test_edge_sources.py`

These are thin, graceful-degrading network adapters — the same pattern as
`YFinanceNews` (never raise; degrade). Only the pure parsing is unit-tested; live
calls are exercised by the smoke script, not CI.

- [ ] **Step 1: Write the failing test (pure parsing only)**

```python
# tests/test_edge_sources.py
from trading.edge.sources import parse_fmp_transcript, parse_fmp_calendar


def test_parse_fmp_transcript_joins_content():
    raw = [{"symbol": "NVDA", "date": "2026-02-21", "content": "CEO: strong quarter."}]
    text = parse_fmp_transcript(raw)
    assert "strong quarter" in text


def test_parse_fmp_transcript_empty_is_blank():
    assert parse_fmp_transcript([]) == ""


def test_parse_fmp_calendar_maps_events():
    raw = [
        {"symbol": "NVDA", "date": "2026-02-21", "epsActual": 5.1, "epsEstimated": 4.6},
        {"symbol": "AAPL", "date": "2026-02-01", "epsActual": None, "epsEstimated": 2.1},
    ]
    events = parse_fmp_calendar(raw, decision_offset_days=2)
    assert events[0].symbol == "NVDA"
    assert events[0].report_date == "2026-02-21"
    assert events[0].decision_date == "2026-02-23"   # +2 calendar days
    assert events[0].eps_actual == 5.1
    assert events[0].eps_consensus == 4.6
    assert events[1].eps_actual is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_edge_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.edge.sources'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading/edge/sources.py
from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

from trading.edge.documents import EventDocuments
from trading.edge.events import EarningsEvent

FMP_BASE = "https://financialmodelingprep.com/api/v3"


def parse_fmp_transcript(raw: list[dict]) -> str:
    """FMP returns a list of transcript objects; take the content of the first."""
    if not raw:
        return ""
    return str(raw[0].get("content", "") or "")


def parse_fmp_calendar(raw: list[dict], decision_offset_days: int) -> list[EarningsEvent]:
    """Map FMP earnings-calendar rows to EarningsEvent. decision_date is report_date
    plus an offset (results land after close; we act the next trading day or two)."""
    out: list[EarningsEvent] = []
    for row in raw:
        report = str(row.get("date", ""))[:10]
        if not report:
            continue
        decision = (date.fromisoformat(report)
                    + timedelta(days=decision_offset_days)).isoformat()
        out.append(EarningsEvent(
            symbol=row.get("symbol", ""), report_date=report, decision_date=decision,
            eps_actual=row.get("epsActual"), eps_consensus=row.get("epsEstimated"),
        ))
    return out


class FMPSource:
    """FMP-backed earnings calendar + transcripts. Never raises — degrades to []/blank.
    Requires FMP_API_KEY (one-time paid month; cancel after the pilot pull)."""

    def __init__(self, api_key: str | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("FMP_API_KEY", "")
        self.timeout = timeout

    def calendar(self, from_date: str, to_date: str) -> list[EarningsEvent]:
        try:
            r = httpx.get(f"{FMP_BASE}/earning_calendar",
                          params={"from": from_date, "to": to_date,
                                  "apikey": self.api_key}, timeout=self.timeout)
            r.raise_for_status()
            return parse_fmp_calendar(r.json(), decision_offset_days=2)
        except Exception:
            return []

    def documents(self, event: EarningsEvent) -> EventDocuments:
        transcript = ""
        try:
            year, quarter = _year_quarter(event.report_date)
            r = httpx.get(f"{FMP_BASE}/earning_call_transcript/{event.symbol}",
                          params={"year": year, "quarter": quarter,
                                  "apikey": self.api_key}, timeout=self.timeout)
            r.raise_for_status()
            transcript = parse_fmp_transcript(r.json())
        except Exception:
            transcript = ""
        return EventDocuments(symbol=event.symbol, decision_date=event.decision_date,
                              transcript=transcript)


def _year_quarter(report_date: str) -> tuple[int, int]:
    d = date.fromisoformat(report_date)
    return d.year, (d.month - 1) // 3 + 1
```

```python
# scripts/smoke_edge.py
"""Manual smoke for the edge network adapters. Needs FMP_API_KEY. Not run in CI.

Usage: FMP_API_KEY=... uv run python scripts/smoke_edge.py
"""
from trading.edge.sources import FMPSource


def main() -> None:
    src = FMPSource()
    events = src.calendar("2026-02-01", "2026-02-28")
    print(f"calendar events: {len(events)}")
    if events:
        docs = src.documents(events[0])
        print(f"first transcript chars: {len(docs.transcript)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add the CLI entry to run.py (append)**

```python
# src/trading/edge/run.py  (append)


def main() -> None:
    """One-time pilot: pull post-cutoff events from FMP, measure, print the report.

    Config via env: FMP_API_KEY, EDGE_CUTOFF (earliest report_date, e.g. 2026-02-01),
    EDGE_FROM / EDGE_TO (calendar window), EDGE_HORIZON (default 5). The DB lives at
    EDGE_DB (default edge.db) and accumulates across runs (approach A->C).
    """
    import os

    from trading.data.yfinance_source import YFinanceSource
    from trading.edge.events import select_post_cutoff
    from trading.edge.predict import EdgePredictor
    from trading.edge.sources import FMPSource
    from trading.edge.store import EdgeRepository, init_edge_db
    from trading.persistence.db import connect

    horizon = int(os.environ.get("EDGE_HORIZON", "5"))
    cutoff = os.environ.get("EDGE_CUTOFF", "2026-02-01")
    frm = os.environ.get("EDGE_FROM", cutoff)
    to = os.environ.get("EDGE_TO", "2026-05-31")

    conn = connect(os.environ.get("EDGE_DB", "edge.db"))
    init_edge_db(conn)
    repo = EdgeRepository(conn)

    fmp = FMPSource()
    events = select_post_cutoff(fmp.calendar(frm, to), earliest_report_date=cutoff)
    print(f"selected {len(events)} post-cutoff events")

    report = run_measurement(
        events=events, source=YFinanceSource(), doc_source=fmp,
        predictor=EdgePredictor(), repo=repo, horizon_days=horizon,
    )
    print(report)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests + commit**

Run: `uv run pytest tests/test_edge_sources.py -v`
Expected: PASS (3 passed)

```bash
git add src/trading/edge/sources.py src/trading/edge/run.py scripts/smoke_edge.py tests/test_edge_sources.py
git commit -m "feat(edge): FMP/EDGAR adapters + python -m trading.edge.run CLI"
```

---

## Task 14: Full-suite green + README note

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: PASS (all tests, including the pre-existing suite, green)

- [ ] **Step 2: Add a README section**

```markdown
## Edge measurer (pre-trading)

A standalone batch that tests whether deep LLM reading of earnings primary sources
predicts post-earnings drift, BEFORE risking money. It never touches the trading loop.

```bash
# One-time pilot (needs FMP_API_KEY — a single paid month, cancel after):
FMP_API_KEY=... EDGE_CUTOFF=2026-02-01 EDGE_TO=2026-05-31 \
  uv run python -m trading.edge.run
```

Lookahead is avoided by only testing earnings dated after the model's knowledge cutoff
(genuinely out-of-sample) plus a memory-probe filter. Output: information coefficient,
long-short spread after costs, calibration, and significance vs coin-flip and dumb-PEAD
benchmarks. Full design: `docs/superpowers/specs/2026-06-14-llm-edge-measurer-design.md`.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README section for the edge measurer"
```

---

## Self-Review Notes

- **Spec coverage:** post-cutoff selection (Task 2, spec §3) · memory-probe (Tasks 10/12, §3) · deep documents transcript+8-K+10-Q (Tasks 9/13, §4) · prediction schema dir/mag/conf + fixed 5d horizon (Tasks 3/10, §5) · market-adjusted realized return (Task 4, §5/§6) · IC + long-short-after-costs + calibration + significance (Tasks 5–7, §6) · coin + dumb-PEAD benchmarks (Tasks 8/11, §6) · isolated module reusing MarketDataSource/structured-output/DB journal (all tasks, §7) · one-time batch ~5M tokens (Task 13 CLI, §10) · A→C forward accumulation (Task 1 DB persists across runs, §3). The §12 decision tree / stop rule is an interpretation procedure for the human, not code — out of plan scope by design.
- **Universe (§8):** the runner pulls its event list from the FMP calendar window; restricting to a large-cap universe is an env/filter concern at run time, not a code unit — applied by choosing the calendar window and (optionally) intersecting with `config/universe.toml` symbols before `run_measurement`.
- **Naming consistency:** `EarningsEvent`, `EventDocuments`, `EdgePrediction`, `MemoryProbe`, `EdgeRepository`, `run_measurement`, `signal_value`, `information_coefficient`, `long_short_spread`, `hit_rate`, `t_statistic`, `calibration`, `realized_market_adjusted` — used identically across tasks.
- **Tokens:** unit tests use fakes only; no test calls Anthropic or the network.
