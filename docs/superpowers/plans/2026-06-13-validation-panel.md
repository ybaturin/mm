# Validation Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second-opinion layer of role-diverse Claude validators that can VETO a proposal after it passes the deterministic guardrails but before it executes — catching plausible-but-wrong decisions. Subtractive only (it can block, never add or resize), with per-profile veto strictness, and every veto logged so its value can be measured on paper.

**Architecture:** `ValidationPanel.review()` runs N validators, each with a distinct adversarial role (risk skeptic, catalyst checker, devil's advocate) so they don't make correlated mistakes. Each validator is a Claude call returning a strict `Verdict` (`veto`, `reason`). The pure `apply_veto_rule()` aggregates verdicts by the profile's rule (`any` → conservative; `majority` → moderate/aggressive). The panel slots into `run_cycle` as an optional step; a blocked trade is skipped and recorded in a new `vetoes` table. Like the Agent Core, the Claude call is isolated and tests inject a stub.

**Tech Stack:** Python 3.12+, `anthropic` SDK (already a dependency), `pydantic`, `pytest`.

This is plan **7 of 10**. Depends on plans 1 (`TradeProposal`, `Intent`), 2 (`JournalRepository`, schema), 4 (`Briefing`), 5 (structured-output pattern), 6 (`run_cycle`). Spec: §5.1 of `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md`.

---

## Existing interfaces this plan consumes (verified)

```python
# plan 1
@dataclass(frozen=True) class TradeProposal: agent_id; symbol; intent; quantity; reference_price; stop_loss_price; rationale
# plan 2
class JournalRepository: __init__(conn); record_decision(...); record_fill(...); ...
SCHEMA_SQL  # in src/trading/persistence/schema.py — extended here with a vetoes table
# plan 4
@dataclass(frozen=True) class Briefing: agent_id; as_of_date; cash; equity; symbols: list[SymbolBrief]
# plan 5 — the structured-output call pattern (messages.parse + Pydantic output_format)
# plan 6
def run_cycle(agent_id, profile, broker, source, accounts, journal, strategy, universe, as_of_date, ts, confirm=None) -> AgentState
```

## File Structure

```
src/trading/validation/__init__.py
src/trading/validation/schema.py     # Pydantic Verdict
src/trading/validation/roles.py      # ROLES + build_validator_system/user (pure)
src/trading/validation/panel.py      # ValidationPanel.review + apply_veto_rule + PanelResult
src/trading/persistence/schema.py    # MODIFY: add vetoes table
src/trading/persistence/journal.py   # MODIFY: record_veto / vetoes_for
src/trading/orchestrator/cycle.py    # MODIFY: optional panel step
tests/test_validation_roles.py
tests/test_validation_panel.py
tests/test_journal_vetoes.py
tests/test_cycle_with_panel.py
```

---

## Task 1: Verdict schema, roles, and prompts

**Files:**
- Create: `src/trading/validation/__init__.py` (empty)
- Create: `src/trading/validation/schema.py`
- Create: `src/trading/validation/roles.py`
- Test: `tests/test_validation_roles.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_validation_roles.py
from trading.validation.roles import ROLES, build_validator_system, build_validator_user
from trading.validation.schema import Verdict
from trading.data.briefing import Briefing, SymbolBrief
from trading.domain import Intent, TradeProposal


def proposal():
    return TradeProposal(agent_id="aggressive", symbol="TSLA", intent=Intent.OPEN_SHORT,
                         quantity=5, reference_price=200.0, stop_loss_price=215.0,
                         rationale="overbought, momentum fading")


def briefing():
    return Briefing("aggressive", "2026-06-15", 2000.0, 5000.0,
                    [SymbolBrief("TSLA", 200.0, 210.0, 220.0, 72.0, 0.08, 0, None)])


def test_three_distinct_roles():
    keys = [r.key for r in ROLES]
    assert keys == ["risk_skeptic", "catalyst_checker", "devils_advocate"]


def test_verdict_schema():
    v = Verdict(veto=True, reason="earnings tomorrow")
    assert v.veto is True and v.reason == "earnings tomorrow"


def test_system_prompt_carries_role_and_subtractive_rule():
    sys = build_validator_system(ROLES[0])
    assert "risk" in sys.lower()
    assert "veto" in sys.lower()
    # the panel can only block, never resize or add
    assert "cannot" in sys.lower() or "only" in sys.lower()


def test_user_prompt_describes_the_trade_and_context():
    u = build_validator_user(proposal(), briefing())
    assert "TSLA" in u
    assert "open_short" in u or "short" in u.lower()
    assert "215" in u            # stop
    assert "rsi" in u.lower() or "72" in u   # market context from briefing
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_validation_roles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.validation.roles'`.

- [ ] **Step 3: Write the implementations**

```python
# src/trading/validation/__init__.py
```

```python
# src/trading/validation/schema.py
from __future__ import annotations

from pydantic import BaseModel


class Verdict(BaseModel):
    """One validator's judgment on a proposal. The model can only veto or allow."""
    veto: bool
    reason: str
```

```python
# src/trading/validation/roles.py
from __future__ import annotations

from dataclasses import dataclass

from trading.data.briefing import Briefing
from trading.domain import TradeProposal


@dataclass(frozen=True)
class Role:
    key: str
    title: str
    instruction: str


# Distinct angles of attack — diversity is the point; identical validators make
# correlated mistakes and add no value.
ROLES: list[Role] = [
    Role("risk_skeptic", "Risk Skeptic",
         "Assess the downside. What if the thesis is wrong? Is the stop-loss placed "
         "sensibly? Is the position too large for this risk profile? Veto only if the "
         "downside is concretely poorly controlled."),
    Role("catalyst_checker", "Catalyst Checker",
         "Check for a known near-term event that makes this trade ill-timed today — "
         "earnings, ex-dividend, a scheduled macro print, a known overhang. Veto only if "
         "a specific, foreseeable catalyst makes acting today reckless."),
    Role("devils_advocate", "Devil's Advocate",
         "Argue the opposite case as strongly as you can. Try to refute the rationale. "
         "Veto only if the case against this trade is concretely stronger than the case for it."),
]


def build_validator_system(role: Role) -> str:
    return (
        f"You are the '{role.title}' on a trade-review panel for an automated trading system.\n"
        f"{role.instruction}\n\n"
        f"You can ONLY veto (block) or allow a trade — you cannot resize it, add trades, or "
        f"relax any limit. The trade has already passed hard risk limits; your job is the "
        f"judgment call those limits cannot make.\n"
        f"Veto only with a concrete, specific reason. If you have no specific concern, allow it "
        f"(veto=false). Do not veto on vague unease."
    )


def build_validator_user(proposal: TradeProposal, briefing: Briefing) -> str:
    brief = next((s for s in briefing.symbols if s.symbol == proposal.symbol), None)
    context = (
        f"price={brief.price} sma20={brief.sma20} sma50={brief.sma50} "
        f"rsi14={brief.rsi14} return_5d={brief.return_5d} held={brief.held_quantity}"
        if brief else "no market context available"
    )
    return (
        f"Date: {briefing.as_of_date}\n"
        f"Proposed trade by the '{proposal.agent_id}' agent:\n"
        f"  {proposal.intent.value} {proposal.quantity} {proposal.symbol} "
        f"@ ~{proposal.reference_price}, stop={proposal.stop_loss_price}\n"
        f"  Rationale: {proposal.rationale}\n\n"
        f"Market context for {proposal.symbol}: {context}\n\n"
        f"Return your verdict."
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_validation_roles.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/validation/__init__.py src/trading/validation/schema.py src/trading/validation/roles.py tests/test_validation_roles.py
git commit -m "feat: validation verdict schema, roles, and prompts"
```

---

## Task 2: ValidationPanel and veto aggregation

**Files:**
- Create: `src/trading/validation/panel.py`
- Test: `tests/test_validation_panel.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_validation_panel.py
from types import SimpleNamespace

from trading.data.briefing import Briefing, SymbolBrief
from trading.domain import Intent, TradeProposal
from trading.validation.panel import PanelResult, ValidationPanel, apply_veto_rule
from trading.validation.schema import Verdict


def test_apply_veto_rule_any():
    assert apply_veto_rule([False, False, False], "any") is False
    assert apply_veto_rule([False, True, False], "any") is True


def test_apply_veto_rule_majority():
    assert apply_veto_rule([True, False, False], "majority") is False     # 1 of 3
    assert apply_veto_rule([True, True, False], "majority") is True       # 2 of 3
    assert apply_veto_rule([True, True], "majority") is True              # 2 of 2


def proposal():
    return TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
                         quantity=5, reference_price=160.0, stop_loss_price=145.0, rationale="x")


def briefing():
    return Briefing("moderate", "2026-06-15", 5000.0, 5000.0,
                    [SymbolBrief("AAPL", 160.0, 150.0, 140.0, 55.0, 0.03, 0, None)])


def stub_client(verdicts):
    it = iter(verdicts)

    def parse(**kwargs):
        return SimpleNamespace(parsed_output=next(it))

    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def test_review_allows_when_no_vetoes():
    client = stub_client([Verdict(veto=False, reason="") for _ in range(3)])
    panel = ValidationPanel(client=client, model="claude-opus-4-8")
    result = panel.review(proposal(), briefing(), veto_rule="majority")
    assert isinstance(result, PanelResult)
    assert result.blocked is False
    assert len(result.verdicts) == 3


def test_review_any_rule_blocks_on_single_veto():
    client = stub_client([
        Verdict(veto=False, reason=""),
        Verdict(veto=True, reason="earnings tomorrow"),
        Verdict(veto=False, reason=""),
    ])
    panel = ValidationPanel(client=client, model="claude-opus-4-8")
    result = panel.review(proposal(), briefing(), veto_rule="any")
    assert result.blocked is True
    assert any(v.veto and "earnings" in v.reason for v in result.verdicts)


def test_review_majority_rule_needs_two_vetoes():
    client = stub_client([
        Verdict(veto=True, reason="risky"),
        Verdict(veto=False, reason=""),
        Verdict(veto=False, reason=""),
    ])
    panel = ValidationPanel(client=client, model="claude-opus-4-8")
    result = panel.review(proposal(), briefing(), veto_rule="majority")
    assert result.blocked is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_validation_panel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.validation.panel'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/validation/panel.py
from __future__ import annotations

import os
from dataclasses import dataclass

from trading.data.briefing import Briefing
from trading.domain import TradeProposal
from trading.validation.roles import ROLES, Role, build_validator_system, build_validator_user
from trading.validation.schema import Verdict

DEFAULT_MODEL = os.environ.get("VALIDATOR_MODEL", "claude-opus-4-8")
MAX_TOKENS = 1024


@dataclass(frozen=True)
class RoleVerdict:
    role: str
    veto: bool
    reason: str


@dataclass(frozen=True)
class PanelResult:
    blocked: bool
    verdicts: list[RoleVerdict]


def apply_veto_rule(vetoes: list[bool], veto_rule: str) -> bool:
    """Whether the panel blocks. 'any': one veto blocks. 'majority': more than half block."""
    if veto_rule == "any":
        return any(vetoes)
    return sum(1 for v in vetoes if v) * 2 > len(vetoes)


class ValidationPanel:
    """Role-diverse second opinion. Subtractive only — blocks or allows, never resizes."""

    def __init__(self, client=None, model: str = DEFAULT_MODEL) -> None:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def review(self, proposal: TradeProposal, briefing: Briefing, veto_rule: str) -> PanelResult:
        verdicts = [self._verdict(role, proposal, briefing) for role in ROLES]
        blocked = apply_veto_rule([v.veto for v in verdicts], veto_rule)
        return PanelResult(blocked=blocked, verdicts=verdicts)

    def _verdict(self, role: Role, proposal: TradeProposal, briefing: Briefing) -> RoleVerdict:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=build_validator_system(role),
            messages=[{"role": "user", "content": build_validator_user(proposal, briefing)}],
            output_format=Verdict,
        )
        v: Verdict = response.parsed_output
        return RoleVerdict(role=role.key, veto=v.veto, reason=v.reason)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_validation_panel.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/validation/panel.py tests/test_validation_panel.py
git commit -m "feat: ValidationPanel with role-diverse verdicts and veto aggregation"
```

---

## Task 3: Persist vetoes

**Files:**
- Modify: `src/trading/persistence/schema.py`
- Modify: `src/trading/persistence/journal.py`
- Test: `tests/test_journal_vetoes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_journal_vetoes.py
import pytest
from trading.domain import Intent, TradeProposal
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.validation.panel import RoleVerdict


@pytest.fixture
def repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


def test_vetoes_table_exists(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "vetoes" in names


def test_record_and_read_veto(repo):
    proposal = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
                             quantity=5, reference_price=160.0, stop_loss_price=145.0, rationale="x")
    verdicts = [
        RoleVerdict("risk_skeptic", True, "stop too wide"),
        RoleVerdict("catalyst_checker", True, "earnings tomorrow"),
        RoleVerdict("devils_advocate", False, ""),
    ]
    repo.record_veto("2026-06-15T13:00:00Z", "moderate", proposal, quantity=5, verdicts=verdicts)

    rows = repo.vetoes_for("moderate")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["quantity"] == 5
    assert "earnings tomorrow" in rows[0]["verdicts"]      # JSON text contains the reasons
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_journal_vetoes.py -v`
Expected: FAIL — `vetoes` table missing / `record_veto` not defined.

- [ ] **Step 3: Extend the schema**

In `src/trading/persistence/schema.py`, append this table to the `SCHEMA_SQL` string
(before the closing `"""`). `init_db` uses `CREATE TABLE IF NOT EXISTS`, so re-running it
on an existing database simply adds the new table:

```sql

CREATE TABLE IF NOT EXISTS vetoes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    agent_id  TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    intent    TEXT NOT NULL,
    quantity  INTEGER NOT NULL,
    verdicts  TEXT NOT NULL          -- JSON: [{role, veto, reason}, ...]
);
```

- [ ] **Step 4: Add the journal methods**

Add to `src/trading/persistence/journal.py`. Extend the top imports with `dataclasses.asdict`:

```python
from dataclasses import asdict
```

Append these methods to the `JournalRepository` class:

```python
    def record_veto(self, ts: str, agent_id: str, proposal, quantity: int, verdicts) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO vetoes (ts, agent_id, symbol, intent, quantity, verdicts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts, agent_id, proposal.symbol, proposal.intent.value, quantity,
             json.dumps([asdict(v) for v in verdicts])),
        )
        self.conn.commit()
        return cur.lastrowid

    def vetoes_for(self, agent_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM vetoes WHERE agent_id = ? ORDER BY ts, id",
            (agent_id,),
        ).fetchall()
```

(`json` and `sqlite3` are already imported in `journal.py` from plan 2.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_journal_vetoes.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/trading/persistence/schema.py src/trading/persistence/journal.py tests/test_journal_vetoes.py
git commit -m "feat: persist panel vetoes to the journal"
```

---

## Task 4: Wire the panel into run_cycle

**Files:**
- Modify: `src/trading/orchestrator/cycle.py`
- Test: `tests/test_cycle_with_panel.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cycle_with_panel.py
import pytest
from trading.broker.fake import FakeBroker
from trading.config import RiskProfile
from trading.data.bars import Bar
from trading.data.fake_source import FakeMarketDataSource
from trading.orchestrator.cycle import run_cycle
from trading.orchestrator.strategy import FakeStrategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.validation.panel import PanelResult, RoleVerdict


def make_profile(**o):
    base = dict(name="moderate", budget=5000.0, max_position_pct=0.25, min_positions=5,
                allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
                daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
                auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority")
    base.update(o)
    return RiskProfile(**base)


def uptrend_bars(n=60):
    return [Bar(f"2026-04-{i+1:02d}", 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1000)
            for i in range(n)]


class BlockingPanel:
    def review(self, proposal, briefing, veto_rule):
        return PanelResult(blocked=True, verdicts=[RoleVerdict("risk_skeptic", True, "no")])


class AllowingPanel:
    def review(self, proposal, briefing, veto_rule):
        return PanelResult(blocked=False, verdicts=[RoleVerdict("risk_skeptic", False, "")])


@pytest.fixture
def repos(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return AccountRepository(conn), JournalRepository(conn)


def setup(profile):
    broker = FakeBroker(cash=profile.budget)
    source = FakeMarketDataSource({"AAPL": uptrend_bars()})
    broker.set_price("AAPL", source.latest_price("AAPL"))
    return broker, source


def test_blocking_panel_prevents_execution_and_records_veto(repos):
    accounts, journal = repos
    profile = make_profile()
    broker, source = setup(profile)

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              panel=BlockingPanel())

    assert broker.positions() == []                 # nothing executed
    assert journal.vetoes_for("moderate")           # veto recorded
    assert journal.fills_for("moderate") == []


def test_allowing_panel_lets_execution_through(repos):
    accounts, journal = repos
    profile = make_profile()
    broker, source = setup(profile)

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z",
              panel=AllowingPanel())

    assert broker.positions()                        # executed
    assert journal.vetoes_for("moderate") == []


def test_no_panel_behaves_as_before(repos):
    accounts, journal = repos
    profile = make_profile()
    broker, source = setup(profile)

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=FakeStrategy(),
              universe=["AAPL"], as_of_date="2026-06-15", ts="2026-06-15T13:30:00Z")

    assert broker.positions()                        # panel optional; default unchanged
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cycle_with_panel.py -v`
Expected: FAIL — `run_cycle()` has no `panel` parameter (`TypeError: unexpected keyword argument 'panel'`).

- [ ] **Step 3: Update `run_cycle`**

In `src/trading/orchestrator/cycle.py`, add `panel=None` to the signature (after `confirm`)
and a panel step in the execution loop. The full updated loop body:

Change the signature line:

```python
    confirm: ConfirmFn | None = None,
    panel=None,
) -> AgentState:
```

Then, inside the `for proposal in proposals:` loop, after the confirmation check and
**before** `broker.place_market_order(...)`, insert the panel step:

```python
        if decision.outcome is Outcome.NEEDS_CONFIRMATION and not confirm(proposal, decision):
            continue

        if panel is not None:
            result = panel.review(proposal, briefing, profile.veto_rule)
            if result.blocked:
                journal.record_veto(ts, agent_id, proposal, decision.quantity, result.verdicts)
                continue

        fill = broker.place_market_order(
            proposal.symbol, action_for(proposal.intent), decision.quantity)
```

This requires `briefing` to be in scope inside the loop. In the current `run_cycle`,
the briefing is built inline in the `strategy.propose(build_briefing(...))` call — change
that to bind it to a variable first:

```python
    briefing = build_briefing(state, universe, source, as_of_date)
    proposals = strategy.propose(briefing, profile)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cycle_with_panel.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite (nothing regressed)**

Run: `uv run pytest -q`
Expected: all tests pass (plans 1–7), including the plan-6 `run_cycle`/simulation tests
that call `run_cycle` without a panel.

- [ ] **Step 6: Commit**

```bash
git add src/trading/orchestrator/cycle.py tests/test_cycle_with_panel.py
git commit -m "feat: optional validation panel step in run_cycle"
```

---

## Task 5: README and full suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all tests pass, exit code 0.

- [ ] **Step 2: Update the Status section of `README.md`**

Replace the `## Status` section's plan list, adding:

```markdown
- Plan 7 of 10: Validation Panel — role-diverse Claude validators (skeptic / catalyst /
  devil's advocate) that can veto a proposal after guardrails; subtractive only,
  per-profile veto rule, every veto logged. Optional step in run_cycle. ✓
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: mark validation panel plan complete"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §5.1 Validation Panel):**
- Role-diverse validators to avoid correlated errors → `ROLES` (3 distinct angles). ✓
- Subtractive only (veto or allow; never resize/add/relax) → `ValidationPanel.review`
  returns a block/allow; `run_cycle` only skips on block, never changes size. ✓
- Per-profile veto strictness (`any` for conservative, `majority` otherwise) →
  `apply_veto_rule` + `profile.veto_rule` (set in `config/profiles.toml`, plan 2). ✓
- Runs after the cheap deterministic guardrails, only on survivors → panel step sits
  after the guardrail decision + confirmation in `run_cycle`. ✓
- Every veto logged to measure the panel's value on paper → `vetoes` table + `record_veto`. ✓
- Configurable / removable → `panel=None` default keeps the panel entirely optional. ✓

**Deferred to later plans (correctly out of scope here):**
- The "hypothetical outcome" of a vetoed trade (what its P&L *would* have been) — the
  veto and its verdicts are now persisted; computing the counterfactual P&L is an
  analysis/reporting step (Reporter plan 8 / go-live evaluation), not the panel itself.
- Wiring a real `ValidationPanel` (live Claude) into the simulation/production run — the
  sim still uses no panel by default (free, deterministic); the live daily run (plan 10)
  will pass a real panel.

**Live-LLM honesty:** `ValidationPanel.review` makes real network calls and is NOT unit-
tested against the live API — tests inject a stub client and `run_cycle` tests use fake
panels. The pure logic (`apply_veto_rule`, prompt builders) IS fully tested.

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `Verdict(veto, reason)`, `Role`, `build_validator_system(role)` /
`build_validator_user(proposal, briefing)`, `apply_veto_rule(vetoes: list[bool], veto_rule)`,
`ValidationPanel(client, model).review(proposal, briefing, veto_rule) -> PanelResult`,
`RoleVerdict(role, veto, reason)`, `record_veto(ts, agent_id, proposal, quantity, verdicts)`,
and the new `run_cycle(..., panel=None)` are used identically across Tasks 1–4 and consume
the verified plan-1/2/4/6 interfaces. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-validation-panel.md`.
