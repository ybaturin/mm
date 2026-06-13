# Foundation & Guardrails Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the project skeleton plus the deterministic Guardrails Engine — the safety core that validates every trade proposal and decides reject / auto-execute / needs-confirmation, with zero external dependencies.

**Architecture:** Pure Python domain models (frozen dataclasses + enums) describe trades, positions, agent state, and per-profile risk limits. A set of small pure-function checks (`checks.py`) implement each individual rule. The `GuardrailsEngine` composes them into a single decision. No IBKR, no LLM, no network — everything is unit-testable in-memory. Later plans (Broker Adapter, Agent Core, etc.) consume these types and this engine.

**Tech Stack:** Python 3.12, `uv` (env + deps), `pytest`, `tomllib` (stdlib) for config.

This is plan **1 of 9** for the IBKR Trading Agents system. Spec: `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md`.

---

## File Structure

```
pyproject.toml                         # uv project, pytest config
config/profiles.toml                   # the 3 risk profiles (spec §3, §6)
src/trading/__init__.py
src/trading/domain.py                  # enums + dataclasses: Intent, TradeProposal, Position, AgentState
src/trading/config.py                  # RiskProfile dataclass + load_profiles()
src/trading/guardrails/__init__.py
src/trading/guardrails/checks.py       # pure-function rules (one responsibility each)
src/trading/guardrails/engine.py       # GuardrailsEngine.evaluate() composes checks → decision
tests/__init__.py
tests/test_domain.py
tests/test_config.py
tests/test_checks.py
tests/test_engine.py
```

**Responsibilities:**
- `domain.py` — vocabulary of the system. No logic beyond trivial computed helpers (signed quantity, equity).
- `config.py` — the tunable risk numbers, loaded from TOML so they can change without code edits.
- `guardrails/checks.py` — each rule is a small pure function returning a bool or a trimmed quantity. Independently testable.
- `guardrails/engine.py` — orchestration + decision precedence only. Holds no rule logic itself beyond ordering.

---

## Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/trading/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Initialize the uv project with Python 3.12**

Run:
```bash
cd /Users/iurii-baturin/work/mm
uv init --bare --python 3.12
uv add --dev pytest
```

- [ ] **Step 2: Write `pyproject.toml`**

Replace the generated `pyproject.toml` with:

```toml
[project]
name = "trading"
version = "0.1.0"
description = "IBKR trading agents — guardrails core"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.hatch.build.targets.wheel]
packages = ["src/trading"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 3: Create package and test directories**

```bash
mkdir -p src/trading/guardrails tests config
touch src/trading/__init__.py src/trading/guardrails/__init__.py tests/__init__.py
```

- [ ] **Step 4: Verify pytest runs (no tests yet)**

Run: `uv run pytest -q`
Expected: exit code 0, output like `no tests ran`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/trading tests config .python-version 2>/dev/null
git commit -m "chore: scaffold uv project for trading guardrails core"
```

---

## Task 1: Domain models

**Files:**
- Create: `src/trading/domain.py`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_domain.py
import pytest
from trading.domain import Intent, Outcome, TradeProposal, Position, AgentState


def test_intent_is_string_enum():
    assert Intent.OPEN_SHORT.value == "open_short"
    assert Intent("close_long") is Intent.CLOSE_LONG


def test_trade_proposal_is_frozen():
    p = TradeProposal(
        agent_id="moderate",
        symbol="AAPL",
        intent=Intent.OPEN_LONG,
        quantity=10,
        reference_price=190.0,
        stop_loss_price=175.0,
        rationale="momentum",
    )
    assert p.symbol == "AAPL"
    with pytest.raises(Exception):
        p.quantity = 5  # frozen dataclass


def test_position_signed_quantity():
    long = Position(symbol="AAPL", quantity=10, avg_price=100.0)
    short = Position(symbol="TSLA", quantity=-4, avg_price=200.0)
    assert long.is_long
    assert short.is_short
    assert not short.is_long


def test_agent_state_equity_long_and_short():
    state = AgentState(
        agent_id="aggressive",
        cash=3000.0,
        positions=[
            Position(symbol="AAPL", quantity=10, avg_price=100.0),   # long
            Position(symbol="TSLA", quantity=-5, avg_price=200.0),   # short
        ],
        peak_equity=5000.0,
        equity_day_start=5000.0,
    )
    prices = {"AAPL": 110.0, "TSLA": 190.0}
    # equity = cash + 10*110 + (-5)*190 = 3000 + 1100 - 950 = 3150
    assert state.equity(prices) == pytest.approx(3150.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_domain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.domain'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/domain.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    """Direction of a proposed trade. Opening vs closing matters for guardrails."""
    OPEN_LONG = "open_long"
    CLOSE_LONG = "close_long"
    OPEN_SHORT = "open_short"
    CLOSE_SHORT = "close_short"

    @property
    def is_opening(self) -> bool:
        return self in (Intent.OPEN_LONG, Intent.OPEN_SHORT)

    @property
    def is_short_side(self) -> bool:
        return self in (Intent.OPEN_SHORT, Intent.CLOSE_SHORT)


class Outcome(str, Enum):
    """Result of evaluating a proposal through the guardrails."""
    APPROVED_AUTO = "approved_auto"
    NEEDS_CONFIRMATION = "needs_confirmation"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TradeProposal:
    """One trade an agent wants to make. Produced by the Agent Core, never by guardrails."""
    agent_id: str
    symbol: str
    intent: Intent
    quantity: int                  # always > 0; direction is carried by `intent`
    reference_price: float         # price the decision maker believed at proposal time
    stop_loss_price: float | None
    rationale: str


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int                  # signed: positive = long, negative = short
    avg_price: float

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0


@dataclass
class AgentState:
    """Snapshot of one virtual sub-account at decision time."""
    agent_id: str
    cash: float
    positions: list[Position] = field(default_factory=list)
    peak_equity: float = 0.0
    equity_day_start: float = 0.0

    def position_for(self, symbol: str) -> Position | None:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None

    def equity(self, prices: dict[str, float]) -> float:
        """Cash plus signed market value of all positions.

        Short proceeds are assumed already reflected in `cash`, so a short's
        signed value (negative) yields correct mark-to-market P&L.
        """
        total = self.cash
        for p in self.positions:
            total += p.quantity * prices[p.symbol]
        return total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_domain.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/domain.py tests/test_domain.py
git commit -m "feat: domain models for trades, positions, agent state"
```

---

## Task 2: Risk profiles config

**Files:**
- Create: `src/trading/config.py`
- Create: `config/profiles.toml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the config TOML**

```toml
# config/profiles.toml — risk limits per agent (spec §3, §6)
# All monetary values in USD. Percentages as fractions (0.15 = 15%).

[conservative]
budget = 5000.0
max_position_pct = 0.15
min_positions = 8
allow_shorts = false
stop_loss_pct = 0.08
max_trades_per_day = 2
daily_loss_limit_pct = 0.03
max_drawdown_pct = 0.10
auto_exec_threshold_usd = 500.0
auto_exec_threshold_pct = 0.25
veto_rule = "any"

[moderate]
budget = 5000.0
max_position_pct = 0.25
min_positions = 5
allow_shorts = false
stop_loss_pct = 0.10
max_trades_per_day = 4
daily_loss_limit_pct = 0.05
max_drawdown_pct = 0.15
auto_exec_threshold_usd = 500.0
auto_exec_threshold_pct = 0.25
veto_rule = "majority"

[aggressive]
budget = 5000.0
max_position_pct = 0.40
min_positions = 3
allow_shorts = true
stop_loss_pct = 0.12
max_trades_per_day = 8
daily_loss_limit_pct = 0.08
max_drawdown_pct = 0.25
auto_exec_threshold_usd = 500.0
auto_exec_threshold_pct = 0.25
veto_rule = "majority"
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_config.py
from pathlib import Path

import pytest
from trading.config import RiskProfile, load_profiles

CONFIG = Path(__file__).resolve().parents[1] / "config" / "profiles.toml"


def test_loads_three_profiles():
    profiles = load_profiles(CONFIG)
    assert set(profiles) == {"conservative", "moderate", "aggressive"}


def test_aggressive_values_match_spec():
    p = load_profiles(CONFIG)["aggressive"]
    assert p.name == "aggressive"
    assert p.budget == 5000.0
    assert p.max_position_pct == 0.40
    assert p.allow_shorts is True
    assert p.stop_loss_pct == 0.12
    assert p.max_trades_per_day == 8
    assert p.daily_loss_limit_pct == 0.08
    assert p.max_drawdown_pct == 0.25
    assert p.veto_rule == "majority"


def test_conservative_disallows_shorts_and_uses_any_veto():
    p = load_profiles(CONFIG)["conservative"]
    assert p.allow_shorts is False
    assert p.veto_rule == "any"


def test_invalid_veto_rule_rejected():
    with pytest.raises(ValueError):
        RiskProfile(
            name="bad", budget=1.0, max_position_pct=0.1, min_positions=1,
            allow_shorts=False, stop_loss_pct=0.1, max_trades_per_day=1,
            daily_loss_limit_pct=0.1, max_drawdown_pct=0.1,
            auto_exec_threshold_usd=1.0, auto_exec_threshold_pct=0.1,
            veto_rule="sometimes",
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.config'`.

- [ ] **Step 4: Write the implementation**

```python
# src/trading/config.py
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

VALID_VETO_RULES = {"any", "majority"}


@dataclass(frozen=True)
class RiskProfile:
    name: str
    budget: float
    max_position_pct: float
    min_positions: int
    allow_shorts: bool
    stop_loss_pct: float
    max_trades_per_day: int
    daily_loss_limit_pct: float
    max_drawdown_pct: float
    auto_exec_threshold_usd: float
    auto_exec_threshold_pct: float
    veto_rule: str

    def __post_init__(self) -> None:
        if self.veto_rule not in VALID_VETO_RULES:
            raise ValueError(
                f"veto_rule must be one of {VALID_VETO_RULES}, got {self.veto_rule!r}"
            )


def load_profiles(path: str | Path) -> dict[str, RiskProfile]:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return {name: RiskProfile(name=name, **values) for name, values in raw.items()}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add src/trading/config.py config/profiles.toml tests/test_config.py
git commit -m "feat: risk profiles config loaded from TOML"
```

---

## Task 3: Guardrail check functions

Pure functions, one rule each. No state, no I/O. The engine (Task 4–5) composes them.

**Files:**
- Create: `src/trading/guardrails/checks.py`
- Test: `tests/test_checks.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_checks.py
import pytest
from trading.domain import Intent, Position
from trading.guardrails.checks import (
    reference_price_ok,
    stop_loss_ok,
    capped_quantity,
    has_sufficient_cash,
    owns_enough_to_close,
    daily_loss_breached,
    drawdown_breached,
)


# --- reference price sanity ---
def test_reference_price_ok_within_tolerance():
    assert reference_price_ok(ref=100.0, market=103.0, tolerance=0.05) is True


def test_reference_price_rejected_when_stale():
    assert reference_price_ok(ref=50.0, market=190.0, tolerance=0.05) is False


# --- stop loss validity ---
def test_stop_required_for_opening_long_below_market():
    assert stop_loss_ok(Intent.OPEN_LONG, stop=90.0, market=100.0) is True
    assert stop_loss_ok(Intent.OPEN_LONG, stop=110.0, market=100.0) is False
    assert stop_loss_ok(Intent.OPEN_LONG, stop=None, market=100.0) is False


def test_stop_required_for_opening_short_above_market():
    assert stop_loss_ok(Intent.OPEN_SHORT, stop=110.0, market=100.0) is True
    assert stop_loss_ok(Intent.OPEN_SHORT, stop=90.0, market=100.0) is False
    assert stop_loss_ok(Intent.OPEN_SHORT, stop=None, market=100.0) is False


def test_stop_not_required_for_closing():
    assert stop_loss_ok(Intent.CLOSE_LONG, stop=None, market=100.0) is True
    assert stop_loss_ok(Intent.CLOSE_SHORT, stop=None, market=100.0) is True


# --- position sizing cap (trims quantity to fit max_position_pct of budget) ---
def test_capped_quantity_trims_to_fit():
    # cap = 0.40 * 5000 = 2000 USD; at price 100 -> max 20 shares
    assert capped_quantity(qty=50, price=100.0, max_position_pct=0.40, budget=5000.0) == 20


def test_capped_quantity_leaves_small_order_untouched():
    assert capped_quantity(qty=5, price=100.0, max_position_pct=0.40, budget=5000.0) == 5


# --- cash / holdings sufficiency ---
def test_has_sufficient_cash():
    assert has_sufficient_cash(cash=1000.0, qty=5, price=100.0) is True
    assert has_sufficient_cash(cash=400.0, qty=5, price=100.0) is False


def test_owns_enough_to_close_long():
    pos = Position(symbol="AAPL", quantity=10, avg_price=100.0)
    assert owns_enough_to_close(pos, Intent.CLOSE_LONG, qty=10) is True
    assert owns_enough_to_close(pos, Intent.CLOSE_LONG, qty=11) is False


def test_owns_enough_to_close_short():
    pos = Position(symbol="TSLA", quantity=-8, avg_price=200.0)
    assert owns_enough_to_close(pos, Intent.CLOSE_SHORT, qty=8) is True
    assert owns_enough_to_close(pos, Intent.CLOSE_SHORT, qty=9) is False


def test_owns_enough_to_close_no_position():
    assert owns_enough_to_close(None, Intent.CLOSE_LONG, qty=1) is False


# --- kill switches ---
def test_daily_loss_breached():
    # budget 5000, limit 5% -> 250 loss triggers
    assert daily_loss_breached(equity_now=4740.0, equity_day_start=5000.0,
                               budget=5000.0, limit_pct=0.05) is True
    assert daily_loss_breached(equity_now=4800.0, equity_day_start=5000.0,
                               budget=5000.0, limit_pct=0.05) is False


def test_drawdown_breached():
    # peak 6000, max dd 15% -> equity below 5100 triggers
    assert drawdown_breached(equity_now=5000.0, peak_equity=6000.0, max_drawdown_pct=0.15) is True
    assert drawdown_breached(equity_now=5200.0, peak_equity=6000.0, max_drawdown_pct=0.15) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_checks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.guardrails.checks'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/guardrails/checks.py
from __future__ import annotations

import math

from trading.domain import Intent, Position


def reference_price_ok(ref: float, market: float, tolerance: float) -> bool:
    """The decision maker's assumed price must be close to the real market price.

    Catches a stale/hallucinated LLM price before it sizes a trade wrongly.
    """
    if market <= 0:
        return False
    return abs(ref - market) / market <= tolerance


def stop_loss_ok(intent: Intent, stop: float | None, market: float) -> bool:
    """Opening trades require a stop on the correct side of the market.

    Long: stop below market. Short: stop above market (unbounded loss otherwise).
    Closing trades do not require a stop.
    """
    if not intent.is_opening:
        return True
    if stop is None:
        return False
    if intent == Intent.OPEN_LONG:
        return stop < market
    if intent == Intent.OPEN_SHORT:
        return stop > market
    return False


def capped_quantity(qty: int, price: float, max_position_pct: float, budget: float) -> int:
    """Trim share count so notional does not exceed max_position_pct of budget.

    Returns the largest allowed quantity (may be 0 if even one share is too big).
    """
    max_notional = max_position_pct * budget
    max_shares = math.floor(max_notional / price)
    return min(qty, max_shares)


def has_sufficient_cash(cash: float, qty: int, price: float) -> bool:
    return cash >= qty * price


def owns_enough_to_close(position: Position | None, intent: Intent, qty: int) -> bool:
    """A close must not exceed the held quantity on the matching side."""
    if position is None:
        return False
    if intent == Intent.CLOSE_LONG:
        return position.quantity >= qty
    if intent == Intent.CLOSE_SHORT:
        return -position.quantity >= qty
    return False


def daily_loss_breached(equity_now: float, equity_day_start: float,
                        budget: float, limit_pct: float) -> bool:
    loss = equity_day_start - equity_now
    return loss >= limit_pct * budget


def drawdown_breached(equity_now: float, peak_equity: float, max_drawdown_pct: float) -> bool:
    if peak_equity <= 0:
        return False
    drawdown = (peak_equity - equity_now) / peak_equity
    return drawdown >= max_drawdown_pct
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_checks.py -v`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/guardrails/checks.py tests/test_checks.py
git commit -m "feat: deterministic guardrail check functions"
```

---

## Task 4: GuardrailsEngine — rejections and decision type

The engine produces a `GuardrailDecision`. This task covers the decision type and all
**rejection** paths. Task 5 adds sizing trim + the auto/confirmation split.

**Files:**
- Create: `src/trading/guardrails/engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_engine.py
import pytest
from trading.config import RiskProfile
from trading.domain import AgentState, Intent, Outcome, Position, TradeProposal
from trading.guardrails.engine import GuardrailDecision, GuardrailsEngine


def make_profile(**overrides) -> RiskProfile:
    base = dict(
        name="moderate", budget=5000.0, max_position_pct=0.25, min_positions=5,
        allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
        daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
        auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority",
    )
    base.update(overrides)
    return RiskProfile(**base)


def make_state(**overrides) -> AgentState:
    base = dict(
        agent_id="moderate", cash=5000.0, positions=[],
        peak_equity=5000.0, equity_day_start=5000.0,
    )
    base.update(overrides)
    return AgentState(**base)


def open_long(qty=10, price=100.0, stop=90.0) -> TradeProposal:
    return TradeProposal(
        agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
        quantity=qty, reference_price=price, stop_loss_price=stop, rationale="x",
    )


def test_reject_unknown_symbol():
    engine = GuardrailsEngine()
    decision = engine.evaluate(open_long(), make_state(), make_profile(),
                               prices={}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("price" in r.lower() for r in decision.reasons)


def test_reject_stale_reference_price():
    engine = GuardrailsEngine()
    proposal = open_long(price=50.0)        # claims 50
    decision = engine.evaluate(proposal, make_state(), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("reference price" in r.lower() for r in decision.reasons)


def test_reject_short_when_profile_disallows():
    engine = GuardrailsEngine()
    short = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_SHORT,
                          quantity=5, reference_price=100.0, stop_loss_price=110.0, rationale="x")
    decision = engine.evaluate(short, make_state(), make_profile(allow_shorts=False),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("short" in r.lower() for r in decision.reasons)


def test_reject_missing_stop_on_open():
    engine = GuardrailsEngine()
    no_stop = open_long(stop=None)
    decision = engine.evaluate(no_stop, make_state(), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("stop" in r.lower() for r in decision.reasons)


def test_reject_insufficient_cash():
    engine = GuardrailsEngine()
    decision = engine.evaluate(open_long(qty=10, price=100.0),
                               make_state(cash=400.0), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("cash" in r.lower() for r in decision.reasons)


def test_reject_close_more_than_owned():
    engine = GuardrailsEngine()
    close = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.CLOSE_LONG,
                          quantity=10, reference_price=100.0, stop_loss_price=None, rationale="x")
    state = make_state(positions=[Position(symbol="AAPL", quantity=3, avg_price=90.0)])
    decision = engine.evaluate(close, state, make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("hold" in r.lower() or "own" in r.lower() for r in decision.reasons)


def test_reject_when_daily_loss_breached():
    engine = GuardrailsEngine()
    state = make_state(equity_day_start=5000.0, cash=4700.0)  # equity now 4700, loss 300 > 250
    decision = engine.evaluate(open_long(), state, make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("daily loss" in r.lower() for r in decision.reasons)


def test_reject_when_drawdown_breached():
    engine = GuardrailsEngine()
    state = make_state(peak_equity=6000.0, cash=5000.0)  # equity 5000, dd 16.7% > 15%
    decision = engine.evaluate(open_long(), state, make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("drawdown" in r.lower() for r in decision.reasons)


def test_reject_when_trade_limit_reached():
    engine = GuardrailsEngine()
    decision = engine.evaluate(open_long(), make_state(), make_profile(max_trades_per_day=4),
                               prices={"AAPL": 100.0}, trades_today=4)
    assert decision.outcome is Outcome.REJECTED
    assert any("trade limit" in r.lower() for r in decision.reasons)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.guardrails.engine'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/guardrails/engine.py
from __future__ import annotations

from dataclasses import dataclass, field

from trading.config import RiskProfile
from trading.domain import AgentState, Intent, Outcome, TradeProposal
from trading.guardrails import checks

REFERENCE_PRICE_TOLERANCE = 0.05


@dataclass(frozen=True)
class GuardrailDecision:
    outcome: Outcome
    quantity: int                       # final (possibly trimmed) share count
    reasons: list[str] = field(default_factory=list)


class GuardrailsEngine:
    """Deterministic evaluation of a single proposal. Never calls an LLM or network.

    Precedence: any hard violation -> REJECTED. Otherwise sizing is trimmed, then the
    notional decides APPROVED_AUTO vs NEEDS_CONFIRMATION (added in Task 5).
    """

    def evaluate(
        self,
        proposal: TradeProposal,
        state: AgentState,
        profile: RiskProfile,
        prices: dict[str, float],
        trades_today: int,
    ) -> GuardrailDecision:
        reasons: list[str] = []

        # 1. Symbol must have a known market price.
        market = prices.get(proposal.symbol)
        if market is None or market <= 0:
            return GuardrailDecision(Outcome.REJECTED, 0,
                                     [f"No market price for {proposal.symbol}"])

        # 2. Daily-loss kill switch (freezes new and closing activity for the day).
        equity_now = state.equity(prices)
        if checks.daily_loss_breached(equity_now, state.equity_day_start,
                                      profile.budget, profile.daily_loss_limit_pct):
            reasons.append("Daily loss limit reached — agent frozen for today")

        # 3. Drawdown kill switch (full suspension pending manual review).
        if checks.drawdown_breached(equity_now, state.peak_equity, profile.max_drawdown_pct):
            reasons.append("Max drawdown reached — agent suspended")

        # 4. Per-day trade count.
        if trades_today >= profile.max_trades_per_day:
            reasons.append("Daily trade limit reached")

        # 5. Shorts permission.
        if proposal.intent == Intent.OPEN_SHORT and not profile.allow_shorts:
            reasons.append("Shorting not allowed for this profile")

        # 6. Reference price sanity.
        if not checks.reference_price_ok(proposal.reference_price, market,
                                         REFERENCE_PRICE_TOLERANCE):
            reasons.append(
                f"Reference price {proposal.reference_price} too far from market {market}")

        # 7. Stop loss validity (opening trades).
        if not checks.stop_loss_ok(proposal.intent, proposal.stop_loss_price, market):
            reasons.append("Missing or invalid stop-loss for opening trade")

        # 8. Holdings sufficiency for closing trades.
        if proposal.intent in (Intent.CLOSE_LONG, Intent.CLOSE_SHORT):
            position = state.position_for(proposal.symbol)
            if not checks.owns_enough_to_close(position, proposal.intent, proposal.quantity):
                reasons.append(f"Does not hold enough {proposal.symbol} to close")

        # 9. Cash sufficiency for opening longs.
        if proposal.intent == Intent.OPEN_LONG:
            if not checks.has_sufficient_cash(state.cash, proposal.quantity, market):
                reasons.append("Insufficient cash for this buy")

        if reasons:
            return GuardrailDecision(Outcome.REJECTED, 0, reasons)

        # Sizing + auto/confirmation split added in Task 5.
        return GuardrailDecision(Outcome.APPROVED_AUTO, proposal.quantity, [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/guardrails/engine.py tests/test_engine.py
git commit -m "feat: guardrails engine rejection paths"
```

---

## Task 5: GuardrailsEngine — sizing trim and auto/confirmation split

Add position-size trimming and the final outcome split. A trimmed-to-zero order is
rejected; otherwise notional vs the auto-exec threshold decides auto vs confirmation.

**Files:**
- Modify: `src/trading/guardrails/engine.py`
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests (append to `tests/test_engine.py`)**

```python
def test_oversized_long_is_trimmed_and_approved():
    engine = GuardrailsEngine()
    # max_position_pct 0.25 * 5000 = 1250 -> at 100 -> 12 shares max
    proposal = open_long(qty=50, price=100.0)
    decision = engine.evaluate(proposal, make_state(cash=5000.0), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.quantity == 12
    # 12 * 100 = 1200 notional > 500 threshold -> confirmation
    assert decision.outcome is Outcome.NEEDS_CONFIRMATION


def test_trimmed_to_zero_is_rejected():
    engine = GuardrailsEngine()
    # price above max notional for even one share: 0.25*5000=1250 cap, price 2000 -> 0 shares
    proposal = TradeProposal(agent_id="moderate", symbol="BRK", intent=Intent.OPEN_LONG,
                             quantity=1, reference_price=2000.0, stop_loss_price=1800.0, rationale="x")
    decision = engine.evaluate(proposal, make_state(cash=5000.0), make_profile(),
                               prices={"BRK": 2000.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("position size" in r.lower() for r in decision.reasons)


def test_small_trade_is_auto_approved():
    engine = GuardrailsEngine()
    # 3 shares * 100 = 300 notional < 500 threshold -> auto
    proposal = open_long(qty=3, price=100.0)
    decision = engine.evaluate(proposal, make_state(cash=5000.0), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.APPROVED_AUTO
    assert decision.quantity == 3


def test_confirmation_threshold_uses_min_of_usd_and_pct():
    engine = GuardrailsEngine()
    # pct threshold 0.25 * 5000 = 1250; usd threshold 500 -> min is 500
    # 6 shares * 100 = 600 > 500 -> confirmation
    proposal = open_long(qty=6, price=100.0)
    decision = engine.evaluate(proposal, make_state(cash=5000.0), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.NEEDS_CONFIRMATION
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: the four new tests FAIL (e.g., oversized order returns quantity 50, outcome APPROVED_AUTO).

- [ ] **Step 3: Update the implementation**

Replace the final block of `evaluate` (everything after the `if reasons:` rejection
return) with:

```python
        if reasons:
            return GuardrailDecision(Outcome.REJECTED, 0, reasons)

        # Sizing: trim opening trades to the per-position cap. Closing trades keep size.
        quantity = proposal.quantity
        if proposal.intent.is_opening:
            quantity = checks.capped_quantity(
                proposal.quantity, market, profile.max_position_pct, profile.budget)
            if quantity <= 0:
                return GuardrailDecision(
                    Outcome.REJECTED, 0,
                    ["Position size cap leaves zero shares for this price"])

        # Auto-execute small trades; large ones need Telegram confirmation.
        notional = quantity * market
        threshold = min(profile.auto_exec_threshold_usd,
                        profile.auto_exec_threshold_pct * profile.budget)
        if notional > threshold:
            return GuardrailDecision(Outcome.NEEDS_CONFIRMATION, quantity, [])
        return GuardrailDecision(Outcome.APPROVED_AUTO, quantity, [])
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `uv run pytest -v`
Expected: PASS (all tests across the four files green).

- [ ] **Step 5: Commit**

```bash
git add src/trading/guardrails/engine.py tests/test_engine.py
git commit -m "feat: guardrails sizing trim and auto/confirmation split"
```

---

## Task 6: Whole-suite check and README note

**Files:**
- Create: `README.md`

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: all tests pass, exit code 0.

- [ ] **Step 2: Write `README.md`**

```markdown
# IBKR Trading Agents

Autonomous, risk-controlled trading agents on Interactive Brokers (paper first).
See `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md` for the full design.

## Status

Plan 1 of 9 complete: domain models, risk-profile config, and the deterministic
**Guardrails Engine** — the safety core that validates every trade proposal and
returns reject / auto-execute / needs-confirmation.

## Develop

```bash
uv run pytest        # run the test suite
```

Risk limits live in `config/profiles.toml` (no code changes needed to tune them).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: project README with status"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §3, §6 deterministic guardrails):**
- 3 risk profiles with budget, max position %, shorts flag, stop %, trades/day, kill-switch %s, auto-exec threshold, veto rule → `config/profiles.toml` + Task 2. ✓
- Per-position size cap with trimming → `capped_quantity` + Task 5. ✓
- Mandatory stop-loss on opens, correct side, required for shorts → `stop_loss_ok` + Task 4. ✓
- Shorts allowed only where profile permits → Task 4. ✓
- Reference-price sanity vs market (anti-stale-LLM) → `reference_price_ok` + Task 4. ✓
- Cash / holdings sufficiency → Task 4. ✓
- Daily-loss and drawdown kill switches → Task 4. ✓
- Per-day trade limit → Task 4. ✓
- Auto-exec threshold (min of USD and % of budget) → Task 5. ✓
- `min_positions` is intentionally NOT a hard per-trade guardrail: it is implied by
  `max_position_pct` (e.g. 40% cap ⇒ ≥3 names) and is used by the Agent Core as a
  target, not enforced here. Documented to avoid redundant logic.

**Deferred to later plans (correctly out of scope here):**
- Whitelist of allowed tickers, IBKR Precautionary Limits, Watchdog NAV floor,
  reconciliation → Broker Adapter / Watchdog plans (3, 8). The engine already rejects
  symbols with no price, which is the in-memory analogue.
- Persisting `peak_equity` / `equity_day_start` across days → Persistence plan (2).

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `GuardrailDecision(outcome, quantity, reasons)`, `Outcome`,
`Intent`, `RiskProfile`, `AgentState.equity(prices)`, and the `checks.*` signatures are
used identically across Tasks 3–5. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-foundation-and-guardrails.md`.
