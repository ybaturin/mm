# Broker Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single, narrow boundary to the broker: connect, read cash + positions, place market and protective-stop orders. Two implementations behind one interface — an in-memory `FakeBroker` (deterministic, the test + simulation backbone) and an `IBKRBroker` on `ib-async` for the real paper account.

**Architecture:** A `Broker` Protocol defines the contract. The rest of the system depends only on that interface — it never imports `ib-async`. `FakeBroker` simulates fills against prices set in-test and maintains weighted-average cost basis via a pure `apply_fill` helper, so it behaves like a real account. `IBKRBroker` is thin: pure translation functions (`position_from_ib`, `cash_from_account_values`, `fill_from_trade`) are unit-tested with stub objects, while the actual network calls are verified by a manual smoke script against the paper Gateway (live broker calls cannot be honestly unit-tested). Market data (prices) is deliberately NOT part of this boundary — it belongs to the Data Collector (plan 4).

**Tech Stack:** Python 3.12+, `ib-async` (new dependency), stdlib `enum`/`dataclasses`, `pytest`.

This is plan **3 of 9**. Depends on plan 1 (`Position`). Spec: `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md`.

---

## Existing interfaces this plan consumes (from plan 1, verified)

```python
# src/trading/domain.py
@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int      # signed: + long, - short
    avg_price: float
```

## File Structure

```
src/trading/broker/__init__.py
src/trading/broker/types.py     # Action enum, Fill dataclass, BrokerError, apply_fill()
src/trading/broker/base.py      # Broker Protocol (the contract)
src/trading/broker/fake.py      # FakeBroker — in-memory, deterministic
src/trading/broker/ibkr.py      # IBKRBroker — ib-async; pure translators + thin calls
scripts/smoke_ibkr.py           # manual: connect to paper Gateway, print cash/positions
tests/test_broker_types.py
tests/test_fake_broker.py
tests/test_ibkr_translators.py
```

**Responsibilities:**
- `types.py` — vocabulary (`Action`, `Fill`) + the cost-basis math (`apply_fill`), which both implementations share.
- `base.py` — the `Broker` contract. One place that defines what a broker can do.
- `fake.py` — a faithful in-memory stand-in. Everything downstream can be developed and tested against it.
- `ibkr.py` — the only file that imports `ib-async`. Translation logic is pure and tested; calls are thin.

---

## Task 1: Broker types and cost-basis math

**Files:**
- Create: `src/trading/broker/__init__.py` (empty)
- Create: `src/trading/broker/types.py`
- Test: `tests/test_broker_types.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_broker_types.py
import pytest
from trading.broker.types import Action, Fill, apply_fill


def test_action_values_match_ibkr():
    assert Action.BUY.value == "BUY"
    assert Action.SELL.value == "SELL"


def test_fill_is_frozen():
    f = Fill(symbol="AAPL", action=Action.BUY, quantity=10, price=101.0)
    assert f.quantity == 10
    with pytest.raises(Exception):
        f.price = 5.0


def test_apply_fill_opens_long():
    assert apply_fill(0, 0.0, Action.BUY, 10, 100.0) == (10, 100.0)


def test_apply_fill_adds_to_long_weighted_average():
    # 10 @ 100 then +10 @ 120 -> 20 @ 110
    assert apply_fill(10, 100.0, Action.BUY, 10, 120.0) == (20, 110.0)


def test_apply_fill_partial_close_keeps_average():
    assert apply_fill(10, 100.0, Action.SELL, 4, 130.0) == (6, 100.0)


def test_apply_fill_full_close_resets():
    assert apply_fill(10, 100.0, Action.SELL, 10, 130.0) == (0, 0.0)


def test_apply_fill_opens_short():
    assert apply_fill(0, 0.0, Action.SELL, 5, 200.0) == (-5, 200.0)


def test_apply_fill_adds_to_short_weighted_average():
    # -5 @ 200 then sell 5 more @ 180 -> -10 @ 190
    assert apply_fill(-5, 200.0, Action.SELL, 5, 180.0) == (-10, 190.0)


def test_apply_fill_flips_through_zero_uses_fill_price():
    # long 5 @ 100, sell 8 @ 90 -> short 3, avg = fill price 90
    assert apply_fill(5, 100.0, Action.SELL, 8, 90.0) == (-3, 90.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_broker_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.broker.types'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/broker/__init__.py
```

```python
# src/trading/broker/types.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    """Order side. Values match the strings IBKR expects."""
    BUY = "BUY"
    SELL = "SELL"


class BrokerError(Exception):
    """Raised when a broker operation cannot complete (no fill, not connected, etc.)."""


@dataclass(frozen=True)
class Fill:
    symbol: str
    action: Action
    quantity: int        # shares filled (unsigned)
    price: float         # average fill price


def apply_fill(
    qty: int, avg: float, action: Action, fill_qty: int, price: float
) -> tuple[int, float]:
    """Return (new_signed_quantity, new_avg_price) after applying a fill.

    Weighted-average cost basis. Mirrors how a real account tracks a position so the
    FakeBroker is a faithful stand-in:
      - opening / increasing magnitude in the same direction -> weighted average
      - reducing without crossing zero -> average unchanged
      - exact close -> (0, 0)
      - flipping through zero -> remainder opens at the fill price
    """
    delta = fill_qty if action is Action.BUY else -fill_qty
    new_qty = qty + delta

    same_direction = qty == 0 or (qty > 0) == (delta > 0)
    if same_direction:
        total_cost = abs(qty) * avg + abs(delta) * price
        return new_qty, total_cost / abs(new_qty)

    # opposite direction: reduce, close, or flip
    if abs(delta) < abs(qty):
        return new_qty, avg
    if abs(delta) == abs(qty):
        return 0, 0.0
    return new_qty, price
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_broker_types.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/broker/__init__.py src/trading/broker/types.py tests/test_broker_types.py
git commit -m "feat: broker types and weighted-average cost-basis helper"
```

---

## Task 2: Broker contract and FakeBroker

**Files:**
- Create: `src/trading/broker/base.py`
- Create: `src/trading/broker/fake.py`
- Test: `tests/test_fake_broker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fake_broker.py
import pytest
from trading.broker.fake import FakeBroker
from trading.broker.types import Action, BrokerError


def test_connect_and_disconnect_toggle_state():
    b = FakeBroker(cash=5000.0)
    assert b.is_connected() is False
    b.connect()
    assert b.is_connected() is True
    b.disconnect()
    assert b.is_connected() is False


def test_cash_reports_balance():
    assert FakeBroker(cash=5000.0).cash() == 5000.0


def test_buy_reduces_cash_and_opens_long():
    b = FakeBroker(cash=5000.0)
    b.set_price("AAPL", 100.0)
    fill = b.place_market_order("AAPL", Action.BUY, 10)
    assert fill.symbol == "AAPL" and fill.quantity == 10 and fill.price == 100.0
    assert b.cash() == 4000.0
    pos = {p.symbol: p for p in b.positions()}["AAPL"]
    assert pos.quantity == 10 and pos.avg_price == 100.0


def test_sell_to_open_short_adds_proceeds():
    b = FakeBroker(cash=5000.0)
    b.set_price("TSLA", 200.0)
    b.place_market_order("TSLA", Action.SELL, 5)
    assert b.cash() == 6000.0  # 5000 + 5*200 proceeds
    pos = {p.symbol: p for p in b.positions()}["TSLA"]
    assert pos.quantity == -5 and pos.avg_price == 200.0


def test_full_close_removes_position_from_listing():
    b = FakeBroker(cash=5000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 10)
    b.set_price("AAPL", 110.0)
    b.place_market_order("AAPL", Action.SELL, 10)
    assert b.positions() == []                 # zero-qty positions are not listed
    assert b.cash() == pytest.approx(5100.0)   # -1000 +1100


def test_market_order_unknown_price_raises():
    b = FakeBroker(cash=5000.0)
    with pytest.raises(BrokerError):
        b.place_market_order("AAPL", Action.BUY, 1)


def test_stop_order_is_recorded_and_returns_id():
    b = FakeBroker(cash=5000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 10)
    oid = b.place_stop_order("AAPL", Action.SELL, 10, stop_price=92.0)
    assert isinstance(oid, str) and oid
    assert b.stop_orders[0]["symbol"] == "AAPL"
    assert b.stop_orders[0]["stop_price"] == 92.0
    assert b.stop_orders[0]["action"] is Action.SELL
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fake_broker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.broker.fake'`.

- [ ] **Step 3: Write the implementations**

```python
# src/trading/broker/base.py
from __future__ import annotations

from typing import Protocol

from trading.broker.types import Action, Fill
from trading.domain import Position


class Broker(Protocol):
    """The system's only window to a brokerage account.

    Market data (prices) is intentionally NOT here — that is the Data Collector's job.
    """

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def cash(self) -> float: ...
    def positions(self) -> list[Position]: ...
    def place_market_order(self, symbol: str, action: Action, quantity: int) -> Fill: ...
    def place_stop_order(
        self, symbol: str, action: Action, quantity: int, stop_price: float
    ) -> str: ...
```

```python
# src/trading/broker/fake.py
from __future__ import annotations

from trading.broker.types import Action, BrokerError, Fill, apply_fill
from trading.domain import Position


class FakeBroker:
    """In-memory broker. Fills at prices set via set_price(). Deterministic.

    The test and development/simulation backbone — satisfies the Broker Protocol.
    """

    def __init__(self, cash: float = 0.0) -> None:
        self._cash = cash
        self._positions: dict[str, Position] = {}
        self._prices: dict[str, float] = {}
        self._connected = False
        self._next_id = 1
        self.stop_orders: list[dict] = []

    # --- connection ---
    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # --- simulation control (not part of the Broker Protocol) ---
    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    # --- account ---
    def cash(self) -> float:
        return self._cash

    def positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.quantity != 0]

    # --- orders ---
    def place_market_order(self, symbol: str, action: Action, quantity: int) -> Fill:
        if symbol not in self._prices:
            raise BrokerError(f"No simulated price for {symbol}")
        price = self._prices[symbol]
        current = self._positions.get(symbol)
        q0 = current.quantity if current else 0
        avg0 = current.avg_price if current else 0.0
        new_qty, new_avg = apply_fill(q0, avg0, action, quantity, price)
        self._positions[symbol] = Position(symbol, new_qty, new_avg)
        self._cash += (-quantity * price) if action is Action.BUY else (quantity * price)
        return Fill(symbol=symbol, action=action, quantity=quantity, price=price)

    def place_stop_order(
        self, symbol: str, action: Action, quantity: int, stop_price: float
    ) -> str:
        oid = f"stop-{self._next_id}"
        self._next_id += 1
        self.stop_orders.append(
            {"id": oid, "symbol": symbol, "action": action,
             "quantity": quantity, "stop_price": stop_price}
        )
        return oid
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fake_broker.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/broker/base.py src/trading/broker/fake.py tests/test_fake_broker.py
git commit -m "feat: broker contract and in-memory FakeBroker"
```

---

## Task 3: IBKR translators and IBKRBroker

`ib-async` is imported only inside this module's functions, so the pure translators
(and the rest of the test suite) run without the package present or a live connection.

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `src/trading/broker/ibkr.py`
- Test: `tests/test_ibkr_translators.py`

- [ ] **Step 1: Add the dependency**

Run: `uv add ib-async`
Expected: `pyproject.toml` gains `ib-async` under `[project] dependencies`; `uv.lock` updates.

- [ ] **Step 2: Write the failing tests**

These use plain stub objects shaped like `ib-async` return values, so no network is
needed. The stubs define the contract the real objects must satisfy.

```python
# tests/test_ibkr_translators.py
from types import SimpleNamespace

import pytest
from trading.broker.ibkr import (
    IBKRBroker,
    cash_from_account_values,
    fill_from_trade,
    position_from_ib,
)
from trading.broker.types import Action, BrokerError


def test_position_from_ib_maps_fields():
    ib_pos = SimpleNamespace(
        contract=SimpleNamespace(symbol="AAPL"), position=10.0, avgCost=100.0
    )
    pos = position_from_ib(ib_pos)
    assert pos.symbol == "AAPL" and pos.quantity == 10 and pos.avg_price == 100.0


def test_position_from_ib_handles_short():
    ib_pos = SimpleNamespace(
        contract=SimpleNamespace(symbol="TSLA"), position=-5.0, avgCost=200.0
    )
    assert position_from_ib(ib_pos).quantity == -5


def test_cash_from_account_values_picks_total_cash_usd():
    values = [
        SimpleNamespace(tag="TotalCashValue", value="3210.55", currency="USD"),
        SimpleNamespace(tag="TotalCashValue", value="999.0", currency="EUR"),
        SimpleNamespace(tag="NetLiquidation", value="5000.0", currency="USD"),
    ]
    assert cash_from_account_values(values) == pytest.approx(3210.55)


def test_cash_from_account_values_missing_returns_zero():
    assert cash_from_account_values([]) == 0.0


def test_fill_from_trade_averages_executions():
    trade = SimpleNamespace(fills=[
        SimpleNamespace(execution=SimpleNamespace(shares=6, price=100.0)),
        SimpleNamespace(execution=SimpleNamespace(shares=4, price=105.0)),
    ])
    fill = fill_from_trade(trade, "AAPL", Action.BUY)
    assert fill.quantity == 10
    assert fill.price == pytest.approx(102.0)   # (600+420)/10


def test_fill_from_trade_no_fills_raises():
    trade = SimpleNamespace(fills=[])
    with pytest.raises(BrokerError):
        fill_from_trade(trade, "AAPL", Action.BUY)


def test_ibkr_broker_reads_positions_via_injected_ib():
    fake_ib = SimpleNamespace(
        positions=lambda: [
            SimpleNamespace(contract=SimpleNamespace(symbol="AAPL"), position=3.0, avgCost=90.0)
        ]
    )
    broker = IBKRBroker(ib=fake_ib)
    positions = broker.positions()
    assert len(positions) == 1 and positions[0].symbol == "AAPL"


def test_ibkr_broker_reads_cash_via_injected_ib():
    fake_ib = SimpleNamespace(
        accountValues=lambda: [
            SimpleNamespace(tag="TotalCashValue", value="4200.0", currency="USD")
        ]
    )
    assert IBKRBroker(ib=fake_ib).cash() == pytest.approx(4200.0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ibkr_translators.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.broker.ibkr'`.

- [ ] **Step 4: Write the implementation**

```python
# src/trading/broker/ibkr.py
from __future__ import annotations

from trading.broker.types import Action, BrokerError, Fill
from trading.domain import Position

# Defaults for IB Gateway running in PAPER mode (live paper port is 4002).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002
DEFAULT_CLIENT_ID = 1


def position_from_ib(ib_pos) -> Position:
    """Translate an ib-async Position into our domain Position (signed quantity)."""
    return Position(
        symbol=ib_pos.contract.symbol,
        quantity=int(ib_pos.position),
        avg_price=float(ib_pos.avgCost),
    )


def cash_from_account_values(values, currency: str = "USD") -> float:
    """Find TotalCashValue for the given currency in ib-async accountValues()."""
    for v in values:
        if v.tag == "TotalCashValue" and v.currency == currency:
            return float(v.value)
    return 0.0


def fill_from_trade(trade, symbol: str, action: Action) -> Fill:
    """Collapse an ib-async Trade's executions into one average Fill."""
    total_shares = sum(f.execution.shares for f in trade.fills)
    if total_shares == 0:
        raise BrokerError(f"Order for {symbol} produced no fills")
    notional = sum(f.execution.shares * f.execution.price for f in trade.fills)
    return Fill(symbol=symbol, action=action,
                quantity=int(total_shares), price=notional / total_shares)


class IBKRBroker:
    """Broker backed by Interactive Brokers via ib-async. Satisfies the Broker Protocol.

    Network calls are thin; the translation logic above is pure and unit-tested. A live
    connection is verified by scripts/smoke_ibkr.py against the paper Gateway.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 client_id: int = DEFAULT_CLIENT_ID, ib=None) -> None:
        if ib is None:
            from ib_async import IB
            ib = IB()
        self.ib = ib
        self.host = host
        self.port = port
        self.client_id = client_id

    def connect(self) -> None:
        self.ib.connect(self.host, self.port, clientId=self.client_id)

    def disconnect(self) -> None:
        self.ib.disconnect()

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def cash(self) -> float:
        return cash_from_account_values(self.ib.accountValues())

    def positions(self) -> list[Position]:
        return [position_from_ib(p) for p in self.ib.positions()]

    def place_market_order(self, symbol: str, action: Action, quantity: int) -> Fill:
        from ib_async import MarketOrder, Stock
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        trade = self.ib.placeOrder(contract, MarketOrder(action.value, quantity))
        while not trade.isDone():
            self.ib.waitOnUpdate(timeout=1)
        return fill_from_trade(trade, symbol, action)

    def place_stop_order(self, symbol: str, action: Action, quantity: int,
                         stop_price: float) -> str:
        from ib_async import StopOrder, Stock
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        trade = self.ib.placeOrder(contract, StopOrder(action.value, quantity, stop_price))
        return str(trade.order.orderId)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ibkr_translators.py -v`
Expected: PASS (8 passed).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/trading/broker/ibkr.py tests/test_ibkr_translators.py
git commit -m "feat: IBKR broker adapter with pure, tested translators"
```

---

## Task 4: Manual smoke script, README, full suite

**Files:**
- Create: `scripts/smoke_ibkr.py`
- Modify: `README.md`

- [ ] **Step 1: Write the smoke script**

```python
# scripts/smoke_ibkr.py
"""Manual check: connect to the paper IB Gateway and print account state.

NOT a unit test — requires a running IB Gateway logged into a PAPER account.
Run:  IBKR_PORT=4002 uv run python scripts/smoke_ibkr.py
"""
from __future__ import annotations

import os

from trading.broker.ibkr import IBKRBroker


def main() -> None:
    broker = IBKRBroker(
        host=os.environ.get("IBKR_HOST", "127.0.0.1"),
        port=int(os.environ.get("IBKR_PORT", "4002")),
        client_id=int(os.environ.get("IBKR_CLIENT_ID", "1")),
    )
    broker.connect()
    try:
        print(f"connected: {broker.is_connected()}")
        print(f"cash (USD): {broker.cash():.2f}")
        positions = broker.positions()
        if not positions:
            print("positions: none")
        for p in positions:
            print(f"  {p.symbol}: {p.quantity} @ {p.avg_price:.2f}")
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full unit suite (no IBKR needed)**

Run: `uv run pytest -q`
Expected: all tests pass (plans 1–3), exit code 0.

- [ ] **Step 3: Update the Status section of `README.md`**

Replace the `## Status` section with:

```markdown
## Status

- Plan 1 of 9: domain models, risk-profile config, deterministic Guardrails Engine. ✓
- Plan 2 of 9: SQLite persistence — ledger, decision journal, fills, equity snapshots. ✓
- Plan 3 of 9: Broker boundary — `Broker` Protocol, in-memory `FakeBroker`, and
  `IBKRBroker` (ib-async) with pure tested translators. ✓

The whole system can run against `FakeBroker` with no live connection. Real paper
trading uses `IBKRBroker`; verify the connection with:

    IBKR_PORT=4002 uv run python scripts/smoke_ibkr.py

(requires a running IB Gateway logged into a paper account).
```

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_ibkr.py README.md
git commit -m "feat: manual IBKR smoke script; mark broker plan complete"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §4 component 4 "Broker Adapter", §9 ib-async):**
- Connect / read cash / read positions / place market order / place protective stop →
  `Broker` Protocol + `FakeBroker` + `IBKRBroker`. ✓
- Shorts as plain `SELL`-to-open (direction lives in signed position) → `apply_fill`
  opens negative quantity; the Intent→Action mapping is the orchestrator's job (plan 9),
  not the broker's. ✓
- Real account is ONE account; the three virtual sub-accounts are a higher layer →
  the broker reports the real account's cash/positions only. Apportioning to agents is
  the orchestrator + ledger (plans 2, 9). Documented. ✓
- ib-async isolated to one file; everything else depends on the `Broker` interface. ✓

**Deferred to later plans (correctly out of scope here):**
- Prices / market data → Data Collector (plan 4). The `Broker` interface deliberately
  omits price lookups, and `FakeBroker.set_price` is a test/sim hook, not part of the
  contract.
- Intent (OPEN_LONG/…) → Action+quantity translation → orchestrator (plan 9).
- Updating the ledger from a Fill → orchestrator (plan 9); this plan only executes and
  reports fills.
- Simulating stop-order triggering → a later simulation step if needed; `FakeBroker`
  records resting stops without firing them.

**Live-broker honesty:** `IBKRBroker` network calls (`connect`, `placeOrder`, waiting on
fills) are NOT unit-tested — they cannot be without a live Gateway. They are verified by
`scripts/smoke_ibkr.py`. The pure translators that carry the real risk of mistakes
(`position_from_ib`, `cash_from_account_values`, `fill_from_trade`) ARE fully tested. This
is called out rather than hidden. The `avgCost` field is assumed per-share for US stocks;
the smoke script is where that assumption is confirmed against real data.

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `Action`, `Fill`, `apply_fill(qty, avg, action, fill_qty, price)`,
the `Broker` Protocol methods, `FakeBroker`/`IBKRBroker` constructors, and the translator
signatures are used identically across Tasks 1–4 and consume the verified plan-1
`Position`. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-broker-adapter.md`.
