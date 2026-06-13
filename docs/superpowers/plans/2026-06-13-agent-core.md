# Agent Core (Claude decision maker) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a `Briefing` into a list of `TradeProposal`s by asking Claude — using strict structured output so the model can only ever return well-formed proposals, never execute anything.

**Architecture:** The Claude call is isolated in `AgentCore.propose()`, which takes an injectable client (so tests use a stub, never the network). The risky logic is split into pure, fully-tested functions: `build_system_prompt(profile)`, `build_user_prompt(briefing)`, and `to_domain_proposals(batch, agent_id)`. Claude returns a Pydantic `ProposalBatch` via `messages.parse()` — the model fills a fixed schema (`symbol`, `intent`, `quantity`, `reference_price`, `stop_loss_price`, `rationale`); there is no field through which it could request a withdrawal, an option, or arbitrary code. Its output is inert data that the orchestrator (plan 9) later feeds through the Guardrails Engine.

**Tech Stack:** Python 3.12+, `anthropic` SDK (new dependency; pulls `pydantic`), model `claude-opus-4-8`, `messages.parse()` structured outputs, adaptive thinking, `pytest`.

This is plan **5 of 9**. Depends on plan 1 (`Intent`, `TradeProposal`), plan 2 (`RiskProfile` via config), plan 4 (`Briefing`, `SymbolBrief`). Spec: `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md`.

---

## Existing interfaces this plan consumes (verified)

```python
# plan 1 — src/trading/domain.py
class Intent(str, Enum): OPEN_LONG="open_long"; CLOSE_LONG="close_long"; OPEN_SHORT="open_short"; CLOSE_SHORT="close_short"
@dataclass(frozen=True)
class TradeProposal:
    agent_id: str; symbol: str; intent: Intent; quantity: int
    reference_price: float; stop_loss_price: float | None; rationale: str

# plan 2 — src/trading/config.py
@dataclass(frozen=True)
class RiskProfile:
    name: str; budget: float; max_position_pct: float; min_positions: int
    allow_shorts: bool; stop_loss_pct: float; max_trades_per_day: int
    daily_loss_limit_pct: float; max_drawdown_pct: float
    auto_exec_threshold_usd: float; auto_exec_threshold_pct: float; veto_rule: str

# plan 4 — src/trading/data/briefing.py
@dataclass(frozen=True)
class SymbolBrief:
    symbol: str; price: float; sma20: float | None; sma50: float | None
    rsi14: float | None; return_5d: float | None
    held_quantity: int; held_avg_price: float | None
@dataclass(frozen=True)
class Briefing:
    agent_id: str; as_of_date: str; cash: float; equity: float; symbols: list[SymbolBrief]
```

## File Structure

```
src/trading/agent/__init__.py
src/trading/agent/schema.py      # Pydantic ProposedTrade/ProposalBatch + to_domain_proposals()
src/trading/agent/prompts.py     # build_system_prompt() + build_user_prompt() (pure)
src/trading/agent/core.py        # AgentCore.propose() — the isolated Claude call
scripts/smoke_agent.py           # manual: run a real briefing through Claude
tests/test_agent_schema.py
tests/test_agent_prompts.py
tests/test_agent_core.py
```

**Responsibilities:**
- `schema.py` — the strict contract for what Claude may return + the pure mapping to domain objects.
- `prompts.py` — deterministic prompt construction. No I/O.
- `core.py` — the only file that talks to the Anthropic SDK. Thin; everything testable lives elsewhere.

---

## Task 1: LLM schema and domain mapping

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `src/trading/agent/__init__.py` (empty)
- Create: `src/trading/agent/schema.py`
- Test: `tests/test_agent_schema.py`

- [ ] **Step 1: Add the dependency**

Run: `uv add anthropic`
Expected: `pyproject.toml` gains `anthropic`; `uv.lock` updates (pulls `pydantic`).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_agent_schema.py
from trading.agent.schema import ProposalBatch, ProposedTrade, to_domain_proposals
from trading.domain import Intent


def test_proposed_trade_accepts_valid_intent():
    t = ProposedTrade(symbol="AAPL", intent="open_long", quantity=10,
                      reference_price=190.0, stop_loss_price=175.0, rationale="momentum")
    assert t.intent == "open_long"


def test_to_domain_proposals_maps_fields_and_sets_agent_id():
    batch = ProposalBatch(trades=[
        ProposedTrade(symbol="AAPL", intent="open_long", quantity=10,
                      reference_price=190.0, stop_loss_price=175.0, rationale="momentum"),
        ProposedTrade(symbol="TSLA", intent="open_short", quantity=4,
                      reference_price=200.0, stop_loss_price=215.0, rationale="overbought"),
    ])
    proposals = to_domain_proposals(batch, agent_id="aggressive")

    assert len(proposals) == 2
    assert proposals[0].agent_id == "aggressive"
    assert proposals[0].symbol == "AAPL"
    assert proposals[0].intent is Intent.OPEN_LONG
    assert proposals[1].intent is Intent.OPEN_SHORT
    assert proposals[1].stop_loss_price == 215.0


def test_to_domain_proposals_allows_null_stop_for_close():
    batch = ProposalBatch(trades=[
        ProposedTrade(symbol="AAPL", intent="close_long", quantity=10,
                      reference_price=190.0, stop_loss_price=None, rationale="take profit"),
    ])
    proposals = to_domain_proposals(batch, agent_id="moderate")
    assert proposals[0].intent is Intent.CLOSE_LONG
    assert proposals[0].stop_loss_price is None


def test_empty_batch_maps_to_empty_list():
    assert to_domain_proposals(ProposalBatch(trades=[]), agent_id="conservative") == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.agent.schema'`.

- [ ] **Step 4: Write the implementation**

```python
# src/trading/agent/__init__.py
```

```python
# src/trading/agent/schema.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from trading.domain import Intent, TradeProposal


class ProposedTrade(BaseModel):
    """One trade Claude proposes. The fixed shape Claude is constrained to return.

    There is deliberately no field for withdrawals, options, leverage, or free-form
    actions — the model can only express a stock/ETF buy or sell within this schema.
    """
    symbol: str
    intent: Literal["open_long", "close_long", "open_short", "close_short"]
    quantity: int
    reference_price: float          # the price Claude believes it is acting on
    stop_loss_price: float | None
    rationale: str


class ProposalBatch(BaseModel):
    trades: list[ProposedTrade]


def to_domain_proposals(batch: ProposalBatch, agent_id: str) -> list[TradeProposal]:
    """Pure mapping from the LLM schema to domain TradeProposals, stamping the agent_id."""
    return [
        TradeProposal(
            agent_id=agent_id,
            symbol=t.symbol,
            intent=Intent(t.intent),
            quantity=t.quantity,
            reference_price=t.reference_price,
            stop_loss_price=t.stop_loss_price,
            rationale=t.rationale,
        )
        for t in batch.trades
    ]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_schema.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/trading/agent/__init__.py src/trading/agent/schema.py tests/test_agent_schema.py
git commit -m "feat: LLM proposal schema and pure mapping to domain proposals"
```

---

## Task 2: Prompt builders

**Files:**
- Create: `src/trading/agent/prompts.py`
- Test: `tests/test_agent_prompts.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent_prompts.py
from trading.agent.prompts import build_system_prompt, build_user_prompt
from trading.config import RiskProfile
from trading.data.briefing import Briefing, SymbolBrief


def make_profile(**o):
    base = dict(name="aggressive", budget=5000.0, max_position_pct=0.40, min_positions=3,
                allow_shorts=True, stop_loss_pct=0.12, max_trades_per_day=8,
                daily_loss_limit_pct=0.08, max_drawdown_pct=0.25,
                auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority")
    base.update(o)
    return RiskProfile(**base)


def briefing():
    return Briefing(
        agent_id="aggressive", as_of_date="2026-06-15", cash=2000.0, equity=2795.0,
        symbols=[
            SymbolBrief("AAPL", 159.0, 150.0, 140.0, 60.0, 0.03, 5, 120.0),
            SymbolBrief("MSFT", 410.0, 400.0, 390.0, 55.0, 0.01, 0, None),
        ],
    )


def test_system_prompt_states_profile_and_constraints():
    p = build_system_prompt(make_profile())
    assert "aggressive" in p
    assert "40%" in p or "0.40" in p or "40 %" in p     # max position
    assert "short" in p.lower()                          # shorts allowed mention
    assert "propose" in p.lower()                        # it proposes, does not execute


def test_system_prompt_forbids_shorts_when_disallowed():
    p = build_system_prompt(make_profile(name="conservative", allow_shorts=False))
    assert "short" in p.lower()
    assert "not" in p.lower() or "no shorting" in p.lower() or "long only" in p.lower()


def test_user_prompt_includes_account_and_symbols():
    u = build_user_prompt(briefing())
    assert "2026-06-15" in u
    assert "AAPL" in u and "MSFT" in u
    assert "159" in u                                    # AAPL price
    assert "2000" in u                                   # cash


def test_user_prompt_marks_held_positions():
    u = build_user_prompt(briefing())
    # AAPL is held (5 @ 120), MSFT is not — the prompt must distinguish them
    assert "AAPL" in u
    aapl_line = next(line for line in u.splitlines() if line.startswith("AAPL"))
    assert "5" in aapl_line
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.agent.prompts'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/agent/prompts.py
from __future__ import annotations

from trading.config import RiskProfile
from trading.data.briefing import Briefing


def build_system_prompt(profile: RiskProfile) -> str:
    """Construct the analyst instructions for one risk profile. Deterministic."""
    shorts = (
        "Shorting IS allowed; every short MUST include a stop-loss above the entry price."
        if profile.allow_shorts
        else "Shorting is NOT allowed — long only. Do not propose open_short or close_short."
    )
    return (
        f"You are a disciplined trading analyst for the '{profile.name}' risk profile.\n"
        f"You PROPOSE trades as structured data; you do NOT execute anything. A separate "
        f"deterministic risk engine validates, sizes, and may reject every proposal.\n\n"
        f"Hard constraints for this profile:\n"
        f"- Budget: ${profile.budget:.0f}. Max {profile.max_position_pct:.0%} of budget in any "
        f"one symbol. Aim for at least {profile.min_positions} positions for diversification.\n"
        f"- Stop-loss: target {profile.stop_loss_pct:.0%} from entry. Opening trades MUST set "
        f"stop_loss_price on the correct side (below for longs, above for shorts).\n"
        f"- {shorts}\n"
        f"- Trade only symbols present in the briefing. Set reference_price to that symbol's "
        f"current price from the briefing.\n"
        f"- At most {profile.max_trades_per_day} trades. Propose nothing if nothing is "
        f"compelling — an empty list is a valid, often correct answer.\n"
        f"- Every proposal needs a concise, concrete rationale.\n"
    )


def build_user_prompt(briefing: Briefing) -> str:
    """Serialize the briefing into a compact, readable snapshot for the model."""
    lines = [
        f"Date: {briefing.as_of_date}",
        f"Agent: {briefing.agent_id}",
        f"Cash: ${briefing.cash:.2f}    Equity: ${briefing.equity:.2f}",
        "",
        "Symbols (symbol price sma20 sma50 rsi14 return_5d | holding):",
    ]
    for s in briefing.symbols:
        holding = (
            f"held {s.held_quantity} @ {s.held_avg_price:.2f}"
            if s.held_quantity != 0 and s.held_avg_price is not None
            else "not held"
        )
        lines.append(
            f"{s.symbol}  price={s.price:.2f}  sma20={s.sma20}  sma50={s.sma50}  "
            f"rsi14={s.rsi14}  ret5d={s.return_5d}  | {holding}"
        )
    lines.append("")
    lines.append("Propose trades for today as structured data, or an empty list.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_prompts.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/agent/prompts.py tests/test_agent_prompts.py
git commit -m "feat: deterministic system and user prompt builders"
```

---

## Task 3: AgentCore — the isolated Claude call

**Files:**
- Create: `src/trading/agent/core.py`
- Test: `tests/test_agent_core.py`

- [ ] **Step 1: Write the failing tests**

The Anthropic client is injected, so the test passes a stub shaped like
`client.messages.parse(...) -> object with .parsed_output`. No network, no API key.

```python
# tests/test_agent_core.py
from types import SimpleNamespace

from trading.agent.core import AgentCore
from trading.agent.schema import ProposalBatch, ProposedTrade
from trading.config import RiskProfile
from trading.data.briefing import Briefing, SymbolBrief
from trading.domain import Intent


def make_profile():
    return RiskProfile(
        name="moderate", budget=5000.0, max_position_pct=0.25, min_positions=5,
        allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
        daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
        auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority",
    )


def make_briefing():
    return Briefing(
        agent_id="moderate", as_of_date="2026-06-15", cash=5000.0, equity=5000.0,
        symbols=[SymbolBrief("AAPL", 159.0, 150.0, 140.0, 60.0, 0.03, 0, None)],
    )


def stub_client(batch):
    """A fake Anthropic client whose messages.parse returns a fixed parsed_output."""
    captured = {}

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(parsed_output=batch)

    client = SimpleNamespace(messages=SimpleNamespace(parse=parse))
    return client, captured


def test_propose_returns_domain_proposals_with_agent_id():
    batch = ProposalBatch(trades=[
        ProposedTrade(symbol="AAPL", intent="open_long", quantity=10,
                      reference_price=159.0, stop_loss_price=146.0, rationale="uptrend"),
    ])
    client, _ = stub_client(batch)
    core = AgentCore(client=client, model="claude-opus-4-8")

    proposals = core.propose(make_briefing(), make_profile())

    assert len(proposals) == 1
    assert proposals[0].agent_id == "moderate"
    assert proposals[0].intent is Intent.OPEN_LONG
    assert proposals[0].symbol == "AAPL"


def test_propose_sends_model_and_structured_output_format():
    batch = ProposalBatch(trades=[])
    client, captured = stub_client(batch)
    core = AgentCore(client=client, model="claude-opus-4-8")

    core.propose(make_briefing(), make_profile())

    assert captured["model"] == "claude-opus-4-8"
    assert captured["output_format"] is ProposalBatch     # strict structured output
    assert "system" in captured and "messages" in captured


def test_propose_empty_batch_returns_empty_list():
    client, _ = stub_client(ProposalBatch(trades=[]))
    core = AgentCore(client=client, model="claude-opus-4-8")
    assert core.propose(make_briefing(), make_profile()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.agent.core'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/agent/core.py
from __future__ import annotations

import os

from trading.agent.prompts import build_system_prompt, build_user_prompt
from trading.agent.schema import ProposalBatch, to_domain_proposals
from trading.config import RiskProfile
from trading.data.briefing import Briefing
from trading.domain import TradeProposal

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "claude-opus-4-8")
MAX_TOKENS = 8192


class AgentCore:
    """Asks Claude for trade proposals. The only component that calls the LLM.

    Claude returns a strict ProposalBatch (it cannot express anything outside that
    schema). The result is inert data — execution happens elsewhere, behind guardrails.
    """

    def __init__(self, client=None, model: str = DEFAULT_MODEL) -> None:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def propose(self, briefing: Briefing, profile: RiskProfile) -> list[TradeProposal]:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=build_system_prompt(profile),
            messages=[{"role": "user", "content": build_user_prompt(briefing)}],
            output_format=ProposalBatch,
        )
        batch: ProposalBatch = response.parsed_output
        return to_domain_proposals(batch, briefing.agent_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_core.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/agent/core.py tests/test_agent_core.py
git commit -m "feat: AgentCore proposes trades via Claude structured output"
```

---

## Task 4: Smoke script, README, full suite

**Files:**
- Create: `scripts/smoke_agent.py`
- Modify: `README.md`

- [ ] **Step 1: Write the smoke script**

```python
# scripts/smoke_agent.py
"""Manual check: run a fake briefing through the real Claude API.

Requires ANTHROPIC_API_KEY in the environment. Uses canned market data, so it does
NOT need IBKR or live prices.

Run:  uv run python scripts/smoke_agent.py
"""
from __future__ import annotations

from trading.agent.core import AgentCore
from trading.config import load_profiles
from trading.data.briefing import Briefing, SymbolBrief


def main() -> None:
    profile = load_profiles("config/profiles.toml")["moderate"]
    briefing = Briefing(
        agent_id="moderate", as_of_date="2026-06-15", cash=5000.0, equity=5000.0,
        symbols=[
            SymbolBrief("AAPL", 159.0, 150.0, 140.0, 62.0, 0.03, 0, None),
            SymbolBrief("MSFT", 410.0, 405.0, 395.0, 48.0, -0.01, 0, None),
            SymbolBrief("SPY", 540.0, 535.0, 520.0, 55.0, 0.02, 0, None),
        ],
    )
    proposals = AgentCore().propose(briefing, profile)
    if not proposals:
        print("No trades proposed.")
    for p in proposals:
        print(f"{p.intent.value} {p.quantity} {p.symbol} @ ~{p.reference_price} "
              f"stop={p.stop_loss_price} — {p.rationale}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full unit suite (no API key needed)**

Run: `uv run pytest -q`
Expected: all tests pass (plans 1–5), exit code 0.

- [ ] **Step 3: Update the Status section of `README.md`**

Replace the `## Status` section with:

```markdown
## Status

- Plan 1 of 9: domain models, risk-profile config, deterministic Guardrails Engine. ✓
- Plan 2 of 9: SQLite persistence — ledger, decision journal, fills, equity snapshots. ✓
- Plan 3 of 9: Broker boundary — Protocol, FakeBroker, IBKRBroker (ib-async). ✓
- Plan 4 of 9: Data Collector — MarketDataSource (yfinance), indicators, briefing. ✓
- Plan 5 of 9: Agent Core — Claude (`claude-opus-4-8`) turns a briefing into trade
  proposals via strict structured output. The model proposes only; it executes nothing. ✓

Try a live proposal (needs ANTHROPIC_API_KEY; uses canned prices, no IBKR):

    uv run python scripts/smoke_agent.py
```

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_agent.py README.md
git commit -m "feat: agent smoke script; mark agent-core plan complete"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §4 component 2 "Agent Core", §5 decision engine):**
- Claude turns a briefing into structured proposals → `AgentCore.propose` + `messages.parse`. ✓
- Strict schema constrains the model to well-formed proposals only; no field for
  withdrawals/options/code → `ProposedTrade`/`ProposalBatch`. ✓ (spec §6 execution boundary)
- LLM proposes, never executes → `propose` returns inert `TradeProposal` data; the
  orchestrator (plan 9) runs them through guardrails. ✓
- `reference_price` carried so guardrails can sanity-check against the real market
  (spec §6 Class 1) → field on `ProposedTrade`, mapped through. ✓
- Per-profile instructions (budget, position cap, shorts, stops, trade limit) →
  `build_system_prompt`. ✓
- Model defaults to `claude-opus-4-8`, overridable via `AGENT_MODEL` → `core.py`. ✓

**Deferred to later plans (correctly out of scope here):**
- Running proposals through the Guardrails Engine and executing approved ones →
  orchestrator (plan 9). This plan stops at producing proposals.
- The adversarial Validation Panel (a second opinion on these proposals) → plan 6.
- News in the briefing → still deferred (plan 4 note).

**Live-LLM honesty:** `AgentCore.propose` makes a real network call and is NOT unit-tested
against the live API — tests inject a stub client. The pure logic that carries the real
mistake risk (`to_domain_proposals`, both prompt builders) IS fully tested. The live path
is exercised by `scripts/smoke_agent.py`.

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `ProposedTrade`/`ProposalBatch`, `to_domain_proposals(batch, agent_id)`,
`build_system_prompt(profile)`, `build_user_prompt(briefing)`, and
`AgentCore(client, model).propose(briefing, profile) -> list[TradeProposal]` are used
identically across Tasks 1–4 and consume the verified plan-1/2/4 types. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-agent-core.md`.
