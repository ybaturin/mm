# Agent Temperaments & Reference — Addendum to Plan 5

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the three agents genuinely different in *temperament*, not just in numeric limits — by injecting a per-profile `mandate` (trading-style sentence) into the one shared system-prompt template. Plus a human-readable `docs/agents.md` overview.

**Architecture:** One template, one extra config field. Each `RiskProfile` gains a `mandate` string (in `config/profiles.toml`); `build_system_prompt` injects it. No duplicated prompt files — the structure stays shared (DRY), only the temperament differs. `mandate` defaults to `""` so existing code/tests that construct `RiskProfile` manually keep working.

**Tech Stack:** Python 3.12+, `pytest`. No new dependencies.

This is **plan 7a** (addendum to plan 5; does not change the 8/9/10 numbering). Depends on plans 2 (`RiskProfile`, `config/profiles.toml`) and 5 (`build_system_prompt`). Spec §3.

---

## Existing interfaces this plan modifies (verified)

```python
# plan 2 — src/trading/config.py
@dataclass(frozen=True)
class RiskProfile:
    name; budget; max_position_pct; min_positions; allow_shorts; stop_loss_pct
    max_trades_per_day; daily_loss_limit_pct; max_drawdown_pct
    auto_exec_threshold_usd; auto_exec_threshold_pct; veto_rule
    # __post_init__ validates veto_rule

# plan 5 — src/trading/agent/prompts.py
def build_system_prompt(profile: RiskProfile) -> str
```

## File Structure

```
src/trading/config.py             # MODIFY: add mandate field (default "")
config/profiles.toml              # MODIFY: add mandate per profile
src/trading/agent/prompts.py      # MODIFY: inject mandate
docs/agents.md                    # CREATE: human-readable overview
tests/test_config.py              # MODIFY: assert mandate loads
tests/test_agent_prompts.py       # MODIFY: assert mandate appears, profiles differ
```

---

## Task 1: Add the `mandate` field

**Files:**
- Modify: `src/trading/config.py`
- Modify: `config/profiles.toml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add failing tests (append to `tests/test_config.py`)**

```python
def test_each_profile_has_a_mandate():
    profiles = load_profiles(CONFIG)
    for name, p in profiles.items():
        assert p.mandate, f"{name} is missing a mandate"
    # the three mandates are distinct temperaments, not copies
    mandates = {p.mandate for p in profiles.values()}
    assert len(mandates) == 3


def test_mandate_defaults_to_empty_for_manual_construction():
    p = RiskProfile(
        name="x", budget=1.0, max_position_pct=0.1, min_positions=1,
        allow_shorts=False, stop_loss_pct=0.1, max_trades_per_day=1,
        daily_loss_limit_pct=0.1, max_drawdown_pct=0.1,
        auto_exec_threshold_usd=1.0, auto_exec_threshold_pct=0.1, veto_rule="any",
    )
    assert p.mandate == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_config.py -k mandate -v`
Expected: FAIL — `RiskProfile` has no `mandate` field.

- [ ] **Step 3: Add the field**

In `src/trading/config.py`, add `mandate` as the **last** field of `RiskProfile`, with a
default so existing manual constructions still work:

```python
    auto_exec_threshold_usd: float
    auto_exec_threshold_pct: float
    veto_rule: str
    mandate: str = ""
```

- [ ] **Step 4: Add mandates to `config/profiles.toml`**

Add a `mandate` line to each profile:

```toml
[conservative]
# ... existing keys ...
mandate = "Capital preservation comes first. Trade rarely and only on strong, confirmed signals. Holding cash is a perfectly good position. Avoid concentration; prefer broad, liquid names and tight risk."

[moderate]
# ... existing keys ...
mandate = "Balanced swing trading over days to weeks. Take reasonable trends and mean-reversions, neither timid nor reckless. Size positions sensibly and cut losers at the stop without hesitation."

[aggressive]
# ... existing keys ...
mandate = "Hunt momentum and decisive moves. Accept higher concentration and use shorts when the setup is clear. Be opportunistic and act faster than the others — but every position still carries a hard stop."
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests green).

- [ ] **Step 6: Commit**

```bash
git add src/trading/config.py config/profiles.toml tests/test_config.py
git commit -m "feat: per-profile mandate (trading temperament) in config"
```

---

## Task 2: Inject the mandate into the system prompt

**Files:**
- Modify: `src/trading/agent/prompts.py`
- Modify: `tests/test_agent_prompts.py`

- [ ] **Step 1: Add failing tests (append to `tests/test_agent_prompts.py`)**

```python
def test_mandate_appears_in_system_prompt():
    p = make_profile(mandate="Hunt momentum and act faster than the others.")
    assert "Hunt momentum and act faster than the others." in build_system_prompt(p)


def test_different_mandates_produce_different_prompts():
    a = build_system_prompt(make_profile(name="conservative", mandate="Preserve capital; trade rarely."))
    b = build_system_prompt(make_profile(name="aggressive", mandate="Hunt momentum; concentrate."))
    assert a != b
    assert "Preserve capital" in a and "Hunt momentum" in b
```

Note: `make_profile` in this test file builds a `RiskProfile` — since `mandate` now has a
default, add `mandate` to its `base` dict so it can be overridden:

```python
def make_profile(**o):
    base = dict(name="aggressive", budget=5000.0, max_position_pct=0.40, min_positions=3,
                allow_shorts=True, stop_loss_pct=0.12, max_trades_per_day=8,
                daily_loss_limit_pct=0.08, max_drawdown_pct=0.25,
                auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25,
                veto_rule="majority", mandate="")
    base.update(o)
    return RiskProfile(**base)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_agent_prompts.py -k mandate -v`
Expected: FAIL — mandate not in the prompt.

- [ ] **Step 3: Inject it**

In `src/trading/agent/prompts.py`, add a mandate line right after the role sentence in
`build_system_prompt`:

```python
    mandate_line = f"Your trading mandate: {profile.mandate}\n" if profile.mandate else ""
    return (
        f"You are a disciplined trading analyst for the '{profile.name}' risk profile.\n"
        f"{mandate_line}"
        f"You PROPOSE trades as structured data; you do NOT execute anything. A separate "
        # ... rest unchanged ...
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_agent_prompts.py -v`
Expected: PASS (all prompt tests green).

- [ ] **Step 5: Commit**

```bash
git add src/trading/agent/prompts.py tests/test_agent_prompts.py
git commit -m "feat: inject per-profile mandate into the agent system prompt"
```

---

## Task 3: Human-readable agents reference

**Files:**
- Create: `docs/agents.md`

- [ ] **Step 1: Write `docs/agents.md`**

```markdown
# The Three Agents

All three are the **same Claude model** (`claude-opus-4-8`) run once a day, pre-market,
against the **same briefing** (cash, positions, and per-symbol price + SMA20 / SMA50 /
RSI14 / 5-day return). They differ in two ways only: their **hard limits** (numbers) and
their **mandate** (temperament, injected into the system prompt). They are not three
different brains — three configs of one.

Each agent only *proposes* trades. Every proposal then passes through the deterministic
Guardrails Engine and (optionally) the Validation Panel before anything executes.

## How a decision is made

1. Data Collector builds the briefing for the agent.
2. Claude reads it and proposes trades as structured data, guided by the agent's mandate
   and limits. An empty list is a valid answer.
3. Guardrails validate, size, and reject/route each proposal (hard limits).
4. Validation Panel (optional) can veto on judgment grounds.
5. Approved trades execute; everything is recorded.

The decision *principle* is Claude's reasoning over technical indicators within the
agent's mandate — discretionary, not a fixed formula and not a proven edge. This is why
the system runs months on paper and gates go-live on beating SPY.

## Profiles

| | Conservative | Moderate | Aggressive |
|---|---|---|---|
| Budget | $5k | $5k | $5k |
| Max per position | 15% | 25% | 40% |
| Min positions | 8 | 5 | 3 |
| Shorts | no | no | yes (with stop) |
| Stop-loss target | 8% | 10% | 12% |
| Max trades/day | 2 | 4 | 8 |
| Daily-loss kill | −3% | −5% | −8% |
| Drawdown kill | −10% | −15% | −25% |
| Panel veto rule | any | majority | majority |

### Mandates (temperament)

- **Conservative** — Capital preservation first. Trades rarely, only on strong confirmed
  signals; cash is a fine position; avoids concentration.
- **Moderate** — Balanced swing trading over days to weeks; neither timid nor reckless;
  cuts losers at the stop.
- **Aggressive** — Hunts momentum and decisive moves; accepts concentration and shorts on
  clear setups; acts faster — but always with a hard stop.

The operative text lives in `config/profiles.toml` (`mandate`) and is injected by
`src/trading/agent/prompts.py`. Edit it there, not here.
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add docs/agents.md
git commit -m "docs: human-readable three-agents reference"
```

---

## Self-Review

**Spec coverage:** §3's three profiles now differ in temperament as well as limits, via one
shared template + a `mandate` field — no duplicated prompts. `docs/agents.md` documents the
set for humans while the operative text stays single-sourced in config. ✓

**Backward compatibility:** `mandate` defaults to `""`, so every existing manual
`RiskProfile(...)` construction in the test suite keeps working; the prompt simply omits the
mandate line when empty. ✓

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `RiskProfile.mandate: str` is read only by `build_system_prompt`; no
other call site changes. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-agent-temperaments.md`.
