# Reporter (Telegram) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell the owner what's happening over Telegram — a morning digest, per-fill notifications, alerts, a P&L report — and let them approve large trades with inline buttons. Replace the simulation's auto-approve `confirm` with a real human-in-the-loop gate.

**Architecture:** A `Notifier` Protocol abstracts the channel. `TelegramNotifier` talks to the Telegram Bot API over `httpx`; `FakeNotifier` records messages and auto-answers confirmations for tests/simulation. All message text comes from pure `format_*` functions (fully tested). `make_confirm(notifier)` adapts a `Notifier` into the `confirm(proposal, decision) -> bool` callback `run_cycle` already accepts (plan 6), so wiring Telegram into the daily run is a one-liner later (plan 10). The live Bot API calls are verified by a smoke script, not unit tests.

**Tech Stack:** Python 3.12+, `httpx` (new explicit dependency), `pytest`.

This is plan **8 of 10**. Depends on plans 1 (`TradeProposal`), 3 (`Fill`, `Action`), 6 (`run_cycle`'s `confirm` signature, `GuardrailDecision`). Spec §7.

---

## Existing interfaces this plan consumes (verified)

```python
# plan 1
@dataclass(frozen=True) class TradeProposal: agent_id; symbol; intent; quantity; reference_price; stop_loss_price; rationale
@dataclass(frozen=True) class GuardrailDecision: outcome; quantity; reasons
# plan 3
class Action(str, Enum): BUY; SELL
@dataclass(frozen=True) class Fill: symbol; action: Action; quantity: int; price: float
# plan 6
ConfirmFn = Callable[[TradeProposal, GuardrailDecision], bool]   # run_cycle's `confirm` param
```

## File Structure

```
src/trading/reporting/__init__.py
src/trading/reporting/format.py     # pure: format_confirmation/fill/digest/alert/pnl
src/trading/reporting/notifier.py   # Notifier Protocol + FakeNotifier + make_confirm
src/trading/reporting/telegram.py   # TelegramNotifier (httpx Bot API)
scripts/smoke_telegram.py           # manual: send a message + a confirmation round-trip
tests/test_report_format.py
tests/test_notifier.py
```

---

## Task 1: Pure message formatters

**Files:**
- Create: `src/trading/reporting/__init__.py` (empty)
- Create: `src/trading/reporting/format.py`
- Test: `tests/test_report_format.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report_format.py
from trading.broker.types import Action, Fill
from trading.domain import Intent, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.domain import Outcome
from trading.reporting.format import (
    format_alert, format_confirmation, format_digest, format_fill, format_pnl,
)


def proposal():
    return TradeProposal(agent_id="aggressive", symbol="TSLA", intent=Intent.OPEN_SHORT,
                         quantity=5, reference_price=200.0, stop_loss_price=215.0,
                         rationale="overbought")


def test_format_confirmation_has_agent_trade_notional_and_reason():
    decision = GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 5, [])
    msg = format_confirmation(proposal(), decision)
    assert "aggressive" in msg
    assert "TSLA" in msg
    assert "5" in msg
    assert "1000" in msg or "1,000" in msg        # 5 * 200 notional
    assert "overbought" in msg


def test_format_fill_reads_naturally():
    fill = Fill(symbol="AAPL", action=Action.BUY, quantity=3, price=101.5)
    msg = format_fill("moderate", fill)
    assert "moderate" in msg and "AAPL" in msg and "3" in msg and "101.5" in msg


def test_format_digest_summarizes_counts():
    msg = format_digest("conservative", "2026-06-15",
                        executed=["BUY 2 SPY @ 540.0"], rejected=1, vetoed=2)
    assert "conservative" in msg
    assert "2026-06-15" in msg
    assert "SPY" in msg
    assert "1" in msg and "2" in msg              # rejected / vetoed counts


def test_format_digest_handles_quiet_day():
    msg = format_digest("moderate", "2026-06-15", executed=[], rejected=0, vetoed=0)
    assert "no trades" in msg.lower() or "nothing" in msg.lower()


def test_format_alert_is_marked():
    msg = format_alert("kill-switch", "moderate hit -5% daily loss; frozen for today")
    assert "kill-switch" in msg
    assert "moderate" in msg


def test_format_pnl_shows_change_and_percent():
    msg = format_pnl("aggressive", start=5000.0, end=5472.45)
    assert "aggressive" in msg
    assert "5,472" in msg or "5472" in msg
    assert "+472" in msg or "472.45" in msg
    assert "9.4%" in msg or "+9.4" in msg
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_report_format.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.reporting.format'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/reporting/__init__.py
```

```python
# src/trading/reporting/format.py
from __future__ import annotations

from trading.broker.types import Fill
from trading.domain import TradeProposal
from trading.guardrails.engine import GuardrailDecision


def format_confirmation(proposal: TradeProposal, decision: GuardrailDecision) -> str:
    notional = decision.quantity * proposal.reference_price
    stop = "—" if proposal.stop_loss_price is None else f"{proposal.stop_loss_price:g}"
    return (
        f"Confirm trade? [{proposal.agent_id}]\n"
        f"{proposal.intent.value} {decision.quantity} {proposal.symbol} "
        f"@ ~{proposal.reference_price:g}  (≈${notional:,.0f})\n"
        f"stop: {stop}\n"
        f"why: {proposal.rationale}"
    )


def format_fill(agent_id: str, fill: Fill) -> str:
    return (f"[{agent_id}] {fill.action.value} {fill.quantity} {fill.symbol} "
            f"@ {fill.price:g}")


def format_digest(agent_id: str, date: str, executed: list[str],
                  rejected: int, vetoed: int) -> str:
    if not executed:
        body = "no trades today"
    else:
        body = "\n".join(f"  • {line}" for line in executed)
    return (
        f"📊 {agent_id} — {date}\n"
        f"{body}\n"
        f"(rejected: {rejected}, vetoed: {vetoed})"
    )


def format_alert(kind: str, detail: str) -> str:
    return f"⚠️ [{kind}] {detail}"


def format_pnl(agent_id: str, start: float, end: float) -> str:
    pnl = end - start
    pct = (pnl / start) if start else 0.0
    return f"💰 {agent_id}: ${start:,.0f} → ${end:,.2f}  ({pnl:+,.2f}, {pct:+.1%})"
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_report_format.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/__init__.py src/trading/reporting/format.py tests/test_report_format.py
git commit -m "feat: pure Telegram message formatters"
```

---

## Task 2: Notifier protocol, FakeNotifier, and confirm adapter

**Files:**
- Create: `src/trading/reporting/notifier.py`
- Test: `tests/test_notifier.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notifier.py
from trading.domain import Intent, Outcome, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.reporting.notifier import FakeNotifier, make_confirm


def proposal():
    return TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
                         quantity=10, reference_price=160.0, stop_loss_price=145.0,
                         rationale="uptrend")


def test_fake_notifier_records_messages():
    n = FakeNotifier()
    n.notify("hello")
    n.notify("world")
    assert n.messages == ["hello", "world"]


def test_fake_notifier_confirmation_default_true_and_recorded():
    n = FakeNotifier()
    assert n.request_confirmation("approve?") is True
    assert n.confirmations == ["approve?"]


def test_fake_notifier_can_decline():
    n = FakeNotifier(confirm_result=False)
    assert n.request_confirmation("approve?") is False


def test_make_confirm_adapts_notifier_to_run_cycle_callback():
    n = FakeNotifier(confirm_result=True)
    confirm = make_confirm(n)
    decision = GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 10, [])

    result = confirm(proposal(), decision)

    assert result is True
    assert len(n.confirmations) == 1
    assert "AAPL" in n.confirmations[0]            # the confirmation text was the proposal
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.reporting.notifier'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/reporting/notifier.py
from __future__ import annotations

from typing import Protocol

from trading.domain import TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.reporting.format import format_confirmation


class Notifier(Protocol):
    def notify(self, text: str) -> None: ...
    def request_confirmation(self, text: str) -> bool: ...


class FakeNotifier:
    """Records messages and auto-answers confirmations. For tests and simulation."""

    def __init__(self, confirm_result: bool = True) -> None:
        self.messages: list[str] = []
        self.confirmations: list[str] = []
        self._confirm_result = confirm_result

    def notify(self, text: str) -> None:
        self.messages.append(text)

    def request_confirmation(self, text: str) -> bool:
        self.confirmations.append(text)
        return self._confirm_result


def make_confirm(notifier: Notifier):
    """Adapt a Notifier into run_cycle's confirm(proposal, decision) -> bool callback."""
    def confirm(proposal: TradeProposal, decision: GuardrailDecision) -> bool:
        return notifier.request_confirmation(format_confirmation(proposal, decision))
    return confirm
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_notifier.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/notifier.py tests/test_notifier.py
git commit -m "feat: Notifier protocol, FakeNotifier, and confirm adapter"
```

---

## Task 3: TelegramNotifier and smoke script

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `src/trading/reporting/telegram.py`
- Create: `scripts/smoke_telegram.py`

- [ ] **Step 1: Add the dependency**

Run: `uv add httpx`
Expected: `pyproject.toml` gains `httpx`; `uv.lock` updates.

- [ ] **Step 2: Write `TelegramNotifier`**

Live Bot API calls are not unit-tested (no network in tests) — verified by the smoke
script. `notify` sends a message; `request_confirmation` sends Yes/No inline buttons and
long-polls `getUpdates` for the tap, defaulting to a safe **decline** on timeout.

```python
# src/trading/reporting/telegram.py
from __future__ import annotations

import os
import time


class TelegramNotifier:
    """Sends messages and asks for confirmations over the Telegram Bot API."""

    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 client=None, confirm_timeout: float = 600.0) -> None:
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        self.chat_id = chat_id or os.environ["TELEGRAM_CHAT_ID"]
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.confirm_timeout = confirm_timeout
        if client is None:
            import httpx
            client = httpx.Client(timeout=30.0)
        self.client = client

    def notify(self, text: str) -> None:
        self.client.post(f"{self.base}/sendMessage",
                         json={"chat_id": self.chat_id, "text": text})

    def request_confirmation(self, text: str) -> bool:
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": "approve"},
            {"text": "❌ Decline", "callback_data": "decline"},
        ]]}
        sent = self.client.post(
            f"{self.base}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "reply_markup": keyboard},
        ).json()
        message_id = sent["result"]["message_id"]

        deadline = time.monotonic() + self.confirm_timeout
        offset = None
        while time.monotonic() < deadline:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            updates = self.client.get(f"{self.base}/getUpdates", params=params).json()
            for upd in updates.get("result", []):
                offset = upd["update_id"] + 1
                cb = upd.get("callback_query")
                if cb and cb.get("message", {}).get("message_id") == message_id:
                    self.client.post(f"{self.base}/answerCallbackQuery",
                                     json={"callback_query_id": cb["id"]})
                    return cb["data"] == "approve"
        return False  # timed out → safe default: do not trade
```

- [ ] **Step 3: Write the smoke script**

```python
# scripts/smoke_telegram.py
"""Manual check: send a message and ask for a confirmation over Telegram.

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.
Run:  uv run python scripts/smoke_telegram.py
"""
from __future__ import annotations

from trading.reporting.telegram import TelegramNotifier


def main() -> None:
    n = TelegramNotifier(confirm_timeout=120.0)
    n.notify("✅ Trading system smoke test: hello from the Reporter.")
    print("Sent a test message. Now requesting a confirmation — tap a button in Telegram.")
    approved = n.request_confirmation("Smoke test: approve this pretend trade?")
    print(f"You {'approved' if approved else 'declined (or timed out)'}.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the unit suite (no token needed)**

Run: `uv run pytest -q`
Expected: all tests pass (Telegram code is import-clean; its network paths aren't unit-tested).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/trading/reporting/telegram.py scripts/smoke_telegram.py
git commit -m "feat: TelegramNotifier over the Bot API + smoke script"
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
- Plan 8 of 10: Reporter — Telegram digests, fill notifications, alerts, P&L, and
  inline-button confirmation of large trades (`make_confirm` plugs into run_cycle).
  Pure formatters + FakeNotifier are tested; live Bot API via a smoke script. ✓
```

And add, after the simulation line:

```markdown
Verify Telegram (needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):

    uv run python scripts/smoke_telegram.py
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: mark reporter (telegram) plan complete"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §7 reporting):**
- Morning digest of what each agent did → `format_digest`. ✓
- Per-fill notification → `format_fill`. ✓
- Confirmation of large trades with inline buttons → `TelegramNotifier.request_confirmation`
  + `make_confirm` plugging into `run_cycle`'s existing `confirm` gate. ✓ (spec §6 auto-small/confirm-large)
- Alerts (guardrails / kill-switch / reconciliation / watchdog) → `format_alert`
  (the watchdog/reconciliation events that trigger them arrive in plan 9). ✓
- Periodic P&L report → `format_pnl`. ✓
- Channel abstracted so simulation stays auto-approve and free → `Notifier` Protocol +
  `FakeNotifier`. ✓

**Deferred to later plans (correctly out of scope here):**
- Actually *calling* the formatters/notifier inside the daily run (sending the digest,
  wiring `make_confirm` as `run_cycle`'s `confirm`) → the daily orchestrator (plan 10).
  This plan provides the pieces and the adapter.
- Watchdog/reconciliation alert *sources* → plan 9 (this plan provides `format_alert`).

**Live-channel honesty:** `TelegramNotifier` makes real Bot API calls and is NOT unit-tested
(no network/token in tests) — verified by `scripts/smoke_telegram.py`. The pure formatters
and the `FakeNotifier`/`make_confirm` logic ARE fully tested. On confirmation timeout the
notifier returns `False` (do not trade) — a safe default.

**Placeholder scan:** none — every step has runnable code/commands and expected output.

**Type consistency:** `format_confirmation(proposal, decision)`, `format_fill(agent_id, fill)`,
`format_digest(agent_id, date, executed, rejected, vetoed)`, `format_alert(kind, detail)`,
`format_pnl(agent_id, start, end)`, the `Notifier` Protocol (`notify`, `request_confirmation`),
`FakeNotifier`, and `make_confirm(notifier) -> ConfirmFn` are used identically across Tasks
1–3 and produce a callback matching `run_cycle`'s `confirm` signature from plan 6. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-reporter-telegram.md`.
