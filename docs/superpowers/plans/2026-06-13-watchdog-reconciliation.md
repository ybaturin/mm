# Watchdog & Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two independent safety nets. **Reconciliation** compares the system's ledger to the broker's actual state every cycle and freezes on any divergence. **Watchdog** monitors total NAV against a hard floor and, on breach, flattens all positions and freezes everything — independent of agent logic so a bug in an agent can't disable it.

**Architecture:** Both are pure, deterministic functions over `Broker` + ledger state — no LLM, no network — so they're fully unit-tested with `FakeBroker`. A `FreezeStore` (new `freezes` table) records freezes by scope (an `agent_id` or `"GLOBAL"`); the daily orchestrator (plan 10) checks it before running an agent and calls these on breach, sending alerts via the Reporter (plan 8). `flatten()` uses only the `Broker` Protocol, so it works on the fake and the real broker alike.

**Tech Stack:** Python 3.12+, stdlib, `pytest`. No new dependencies.

This is plan **9 of 10**. Depends on plans 1 (`AgentState`, `Position`), 2 (persistence schema + `connect`), 3 (`Broker`, `Action`, `Fill`, `FakeBroker`). Spec §6 (layers 4–5).

---

## Existing interfaces this plan consumes (verified)

```python
# plan 1
@dataclass class AgentState: agent_id; cash; positions: list[Position]; peak_equity; equity_day_start
@dataclass(frozen=True) class Position: symbol; quantity; avg_price   # quantity signed
# plan 2
def connect(path) -> sqlite3.Connection; def init_db(conn); SCHEMA_SQL
# plan 3
class Action(str, Enum): BUY; SELL
@dataclass(frozen=True) class Fill: symbol; action; quantity; price
class Broker(Protocol): cash()->float; positions()->list[Position]; place_market_order(symbol, action, quantity)->Fill
class FakeBroker: + set_price(symbol, price)   # test helper
```

## File Structure

```
src/trading/persistence/schema.py    # MODIFY: add freezes table
src/trading/persistence/freezes.py   # CREATE: FreezeStore
src/trading/safety/__init__.py
src/trading/safety/reconcile.py      # reconcile() + ReconResult
src/trading/safety/watchdog.py       # nav() + Watchdog + flatten() + WatchdogResult
tests/test_freezes.py
tests/test_reconcile.py
tests/test_watchdog.py
```

---

## Task 1: FreezeStore

**Files:**
- Modify: `src/trading/persistence/schema.py`
- Create: `src/trading/persistence/freezes.py`
- Test: `tests/test_freezes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_freezes.py
import pytest
from trading.persistence.db import connect
from trading.persistence.freezes import FreezeStore, GLOBAL
from trading.persistence.schema import init_db


@pytest.fixture
def store(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return FreezeStore(conn)


def test_unfrozen_by_default(store):
    assert store.is_frozen("moderate") is False
    assert store.is_frozen(GLOBAL) is False


def test_freeze_and_check(store):
    store.freeze("moderate", "daily loss limit", "2026-06-15T13:00:00Z")
    assert store.is_frozen("moderate") is True
    assert store.is_frozen("aggressive") is False


def test_global_freeze_is_its_own_scope(store):
    store.freeze(GLOBAL, "NAV floor breached", "2026-06-15T13:00:00Z")
    assert store.is_frozen(GLOBAL) is True
    assert store.is_frozen("moderate") is False


def test_unfreeze(store):
    store.freeze("moderate", "x", "2026-06-15T13:00:00Z")
    store.unfreeze("moderate")
    assert store.is_frozen("moderate") is False


def test_freeze_is_idempotent_and_updates_reason(store):
    store.freeze("moderate", "first", "2026-06-15T13:00:00Z")
    store.freeze("moderate", "second", "2026-06-16T13:00:00Z")
    assert store.is_frozen("moderate") is True
    assert store.reason("moderate") == "second"


def test_frozen_scopes_lists_all(store):
    store.freeze("moderate", "x", "t")
    store.freeze(GLOBAL, "y", "t")
    assert set(store.frozen_scopes()) == {"moderate", GLOBAL}
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_freezes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.persistence.freezes'`.

- [ ] **Step 3: Extend the schema**

Append to `SCHEMA_SQL` in `src/trading/persistence/schema.py` (before the closing `"""`):

```sql

CREATE TABLE IF NOT EXISTS freezes (
    scope   TEXT PRIMARY KEY,    -- an agent_id, or 'GLOBAL'
    reason  TEXT NOT NULL,
    ts      TEXT NOT NULL
);
```

- [ ] **Step 4: Write `FreezeStore`**

```python
# src/trading/persistence/freezes.py
from __future__ import annotations

import sqlite3

GLOBAL = "GLOBAL"


class FreezeStore:
    """Records which scopes (an agent_id or GLOBAL) are halted. Survives restarts."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def freeze(self, scope: str, reason: str, ts: str) -> None:
        self.conn.execute(
            """
            INSERT INTO freezes (scope, reason, ts) VALUES (?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET reason = excluded.reason, ts = excluded.ts
            """,
            (scope, reason, ts),
        )
        self.conn.commit()

    def unfreeze(self, scope: str) -> None:
        self.conn.execute("DELETE FROM freezes WHERE scope = ?", (scope,))
        self.conn.commit()

    def is_frozen(self, scope: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM freezes WHERE scope = ?", (scope,)).fetchone()
        return row is not None

    def reason(self, scope: str) -> str | None:
        row = self.conn.execute(
            "SELECT reason FROM freezes WHERE scope = ?", (scope,)).fetchone()
        return row["reason"] if row else None

    def frozen_scopes(self) -> list[str]:
        rows = self.conn.execute("SELECT scope FROM freezes ORDER BY scope").fetchall()
        return [r["scope"] for r in rows]
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_freezes.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add src/trading/persistence/schema.py src/trading/persistence/freezes.py tests/test_freezes.py
git commit -m "feat: FreezeStore for halting agents or the whole system"
```

---

## Task 2: Reconciliation

**Files:**
- Create: `src/trading/safety/__init__.py` (empty)
- Create: `src/trading/safety/reconcile.py`
- Test: `tests/test_reconcile.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reconcile.py
from trading.broker.fake import FakeBroker
from trading.broker.types import Action
from trading.domain import AgentState, Position
from trading.safety.reconcile import reconcile


def broker_with(cash, holdings):
    """holdings: list of (symbol, qty, price) — built by buying at `price`."""
    b = FakeBroker(cash=cash)
    for symbol, qty, price in holdings:
        b.set_price(symbol, price)
        b.place_market_order(symbol, Action.BUY, qty)
    return b


def test_matching_state_reconciles_ok():
    b = FakeBroker(cash=5000.0)
    state = AgentState("moderate", cash=5000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    result = reconcile(state, b)
    assert result.ok is True
    assert result.discrepancies == []


def test_cash_mismatch_is_flagged():
    b = FakeBroker(cash=4000.0)
    state = AgentState("moderate", cash=5000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    result = reconcile(state, b)
    assert result.ok is False
    assert any("cash" in d.lower() for d in result.discrepancies)


def test_unknown_position_is_flagged():
    # broker holds AAPL the ledger doesn't know about
    b = FakeBroker(cash=4000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 10)
    state = AgentState("moderate", cash=3000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    result = reconcile(state, b)
    assert result.ok is False
    assert any("AAPL" in d for d in result.discrepancies)


def test_quantity_mismatch_is_flagged():
    b = FakeBroker(cash=4000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 10)
    state = AgentState("moderate", cash=4000.0,
                       positions=[Position("AAPL", 7, 100.0)],   # ledger says 7, broker 10
                       peak_equity=5000.0, equity_day_start=5000.0)
    result = reconcile(state, b)
    assert result.ok is False
    assert any("AAPL" in d for d in result.discrepancies)


def test_cash_within_tolerance_is_ok():
    b = FakeBroker(cash=5000.005)
    state = AgentState("moderate", cash=5000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    assert reconcile(state, b, tolerance=0.01).ok is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_reconcile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.safety.reconcile'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/safety/__init__.py
```

```python
# src/trading/safety/reconcile.py
from __future__ import annotations

from dataclasses import dataclass, field

from trading.broker.base import Broker
from trading.domain import AgentState


@dataclass(frozen=True)
class ReconResult:
    ok: bool
    discrepancies: list[str] = field(default_factory=list)


def reconcile(ledger: AgentState, broker: Broker, tolerance: float = 0.01) -> ReconResult:
    """Compare the system's ledger to the broker's real state. Any divergence is a problem.

    Catches bugs in our accounting and positions opened outside the system.
    """
    discrepancies: list[str] = []

    if abs(ledger.cash - broker.cash()) > tolerance:
        discrepancies.append(
            f"cash mismatch: ledger {ledger.cash:.2f} vs broker {broker.cash():.2f}")

    ledger_pos = {p.symbol: p.quantity for p in ledger.positions}
    broker_pos = {p.symbol: p.quantity for p in broker.positions()}
    for symbol in sorted(set(ledger_pos) | set(broker_pos)):
        lq, bq = ledger_pos.get(symbol, 0), broker_pos.get(symbol, 0)
        if lq != bq:
            discrepancies.append(
                f"{symbol} quantity mismatch: ledger {lq} vs broker {bq}")

    return ReconResult(ok=not discrepancies, discrepancies=discrepancies)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_reconcile.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/safety/__init__.py src/trading/safety/reconcile.py tests/test_reconcile.py
git commit -m "feat: ledger-vs-broker reconciliation"
```

---

## Task 3: Watchdog and flatten

**Files:**
- Create: `src/trading/safety/watchdog.py`
- Test: `tests/test_watchdog.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_watchdog.py
from trading.broker.fake import FakeBroker
from trading.broker.types import Action
from trading.safety.watchdog import Watchdog, flatten, nav


def funded_broker():
    b = FakeBroker(cash=5000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 20)   # spend 2000 -> cash 3000, 20 @ 100
    return b


def test_nav_is_cash_plus_position_value():
    b = funded_broker()
    assert nav(b, {"AAPL": 100.0}) == 5000.0       # 3000 cash + 20*100
    assert nav(b, {"AAPL": 110.0}) == 5200.0       # mark up


def test_watchdog_not_breached_above_floor():
    b = funded_broker()
    wd = Watchdog(starting_nav=5000.0, floor_fraction=0.8)   # floor = 4000
    result = wd.check(b, {"AAPL": 100.0})
    assert result.breached is False
    assert result.nav == 5000.0
    assert result.floor == 4000.0


def test_watchdog_breached_below_floor():
    b = funded_broker()
    wd = Watchdog(starting_nav=5000.0, floor_fraction=0.8)
    # AAPL collapses to 40 -> nav = 3000 + 20*40 = 3800 < 4000
    result = wd.check(b, {"AAPL": 40.0})
    assert result.breached is True
    assert result.nav == 3800.0


def test_flatten_closes_all_positions():
    b = funded_broker()
    b.set_price("AAPL", 90.0)
    fills = flatten(b, {"AAPL": 90.0})
    assert b.positions() == []
    assert len(fills) == 1
    assert fills[0].action is Action.SELL and fills[0].quantity == 20


def test_flatten_buys_back_a_short():
    b = FakeBroker(cash=5000.0)
    b.set_price("TSLA", 200.0)
    b.place_market_order("TSLA", Action.SELL, 5)   # open short
    flatten(b, {"TSLA": 200.0})
    assert b.positions() == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_watchdog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.safety.watchdog'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/safety/watchdog.py
from __future__ import annotations

from dataclasses import dataclass

from trading.broker.base import Broker
from trading.broker.types import Action, Fill


def nav(broker: Broker, prices: dict[str, float]) -> float:
    """Net liquidation value: cash plus signed mark-to-market of all positions."""
    return broker.cash() + sum(p.quantity * prices[p.symbol] for p in broker.positions())


@dataclass(frozen=True)
class WatchdogResult:
    breached: bool
    nav: float
    floor: float


class Watchdog:
    """Independent NAV-floor monitor. A breach triggers a global stop (flatten + freeze)."""

    def __init__(self, starting_nav: float, floor_fraction: float = 0.8) -> None:
        self.starting_nav = starting_nav
        self.floor_fraction = floor_fraction

    def check(self, broker: Broker, prices: dict[str, float]) -> WatchdogResult:
        current = nav(broker, prices)
        floor = self.starting_nav * self.floor_fraction
        return WatchdogResult(breached=current < floor, nav=current, floor=floor)


def flatten(broker: Broker, prices: dict[str, float]) -> list[Fill]:
    """Close every open position with market orders. Uses only the Broker Protocol."""
    fills: list[Fill] = []
    for p in list(broker.positions()):
        action = Action.SELL if p.quantity > 0 else Action.BUY
        fills.append(broker.place_market_order(p.symbol, action, abs(p.quantity)))
    return fills
```

(`prices` is accepted by `flatten` for symmetry with `nav`/`check` and to document that
the caller supplies marks; the live broker fills at market, the FakeBroker at its set price.)

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_watchdog.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/safety/watchdog.py tests/test_watchdog.py
git commit -m "feat: NAV watchdog and flatten-all"
```

---

## Task 4: README and full suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all tests pass, exit code 0.

- [ ] **Step 2: Update the Status section of `README.md`**

Add to the plan list:

```markdown
- Plan 9 of 10: Safety nets — FreezeStore (halt an agent or everything), reconciliation
  (ledger vs broker, freeze on divergence), and an independent NAV watchdog that flattens
  all positions and freezes globally on a floor breach. All deterministic, fully tested. ✓
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: mark watchdog + reconciliation plan complete"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §6 layers 4–5):**
- Watchdog monitors NAV against a floor and flattens + freezes on breach, independent of
  agent code → `Watchdog`, `flatten`, `FreezeStore` (`GLOBAL`). ✓
- Reconciliation compares ledger to real account each cycle; divergence (drift or an
  unknown/external position) → freeze + alert → `reconcile`, `ReconResult`, `FreezeStore`. ✓
- Freeze persists across restarts → `freezes` table. ✓
- Per-agent kill-switches (daily-loss / drawdown) already live in the Guardrails Engine
  (plan 1); this plan adds the *system-level* nets above them. ✓

**Deferred to plan 10 (correctly out of scope here):**
- Calling `reconcile`/`Watchdog.check` each cycle, sending `format_alert` (plan 8) on a
  breach, and skipping `is_frozen` agents at the top of the daily loop → the orchestrator.
  This plan provides the deterministic building blocks; plan 10 wires them.
- Cancelling resting (stop) orders as part of the global kill → the real `IBKRBroker`
  needs a `cancel_all` method; `flatten` closes positions, which is the core protection.
  Adding `cancel_all` to the Broker Protocol is a small plan-10 task when wiring the real
  broker.

**Determinism / honesty:** everything here is pure logic over the `Broker` Protocol and the
ledger — no LLM, no network — so all of it is genuinely unit-tested with `FakeBroker`,
including the breach and flatten paths. This is the safety layer, so that full coverage is
the point.

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `FreezeStore(conn)` (`freeze`/`unfreeze`/`is_frozen`/`reason`/
`frozen_scopes`, `GLOBAL`), `reconcile(ledger, broker, tolerance) -> ReconResult`,
`nav(broker, prices)`, `Watchdog(starting_nav, floor_fraction).check(broker, prices) ->
WatchdogResult`, and `flatten(broker, prices) -> list[Fill]` are used identically across
Tasks 1–3 and consume the verified plan-1/2/3 interfaces. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-watchdog-reconciliation.md`.
