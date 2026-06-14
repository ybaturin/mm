# Telegram Analytics Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Telegram bot readable — HTML formatting, bot-grouped monospace tables, a plain-language confirmation prompt with expected profit/horizon/why, captured target-price+horizon forecasts, and forecast-vs-actual in `/positions`, `/pnl` (with SPY benchmark) and a close retrospective.

**Architecture:** Three coordinated layers. (1) A formatting layer in `reporting/format.py` plus `parse_mode="HTML"` plumbing. (2) Forecast capture in `agent/schema.py`, `agent/prompts.py`, `domain.py`. (3) Storage + fact computation in `persistence/*` (new `theses` table + `decisions` migration) and `reporting/queries.py`, wired into `orchestrator/cycle.py`.

**Tech Stack:** Python 3.13, `uv`, pytest, pydantic, sqlite3, httpx, Telegram Bot API.

**Spec:** `docs/superpowers/specs/2026-06-14-telegram-analytics-redesign-design.md`

**Conventions:** every module starts with `from __future__ import annotations`. Tests are flat files under `tests/`, named `test_*.py`. Run a single test with `uv run pytest tests/test_x.py::test_name -v`. In-code comments/docstrings are English-only.

---

## File structure

- `src/trading/reporting/format.py` — **modify.** New helpers (`html_escape`, `_plural`, `human_horizon`, `human_days_left`, `mono_table`, `pnl_color`), rewritten `format_confirmation`, new `format_retro`, regrouped `format_trades`/`format_positions`/`format_pnl_report`.
- `src/trading/reporting/telegram.py` — **modify.** `parse_mode="HTML"` in `notify` and `request_confirmation`.
- `src/trading/bot.py` — **modify.** `parse_mode="HTML"` in `_send`/`_edit`; wire thesis store + benchmark into reports.
- `src/trading/domain.py` — **modify.** `target_price`/`horizon_days` on `TradeProposal`.
- `src/trading/agent/schema.py` — **modify.** Forecast fields on `ProposedTrade`; side validation in `to_domain_proposals`.
- `src/trading/agent/prompts.py` — **modify.** Instruct target/horizon + jargon-free rationale.
- `src/trading/persistence/schema.py` — **modify.** Add `theses` table; `migrate_db` for `decisions` columns.
- `src/trading/persistence/journal.py` — **modify.** `record_decision` writes forecast columns.
- `src/trading/persistence/theses.py` — **create.** `ThesisStore` repo.
- `src/trading/reporting/queries.py` — **modify.** Forecast progress on positions; `benchmark_pct` on P&L; pure calc helpers.
- `src/trading/orchestrator/cycle.py` — **modify.** Thesis lifecycle + retro emission around fills.
- `src/trading/orchestrator/daily.py`, `src/trading/run.py` — **modify.** Build and pass `ThesisStore`.
- Tests: `tests/test_report_format.py`, `tests/test_telegram.py`, `tests/test_bot.py`, `tests/test_agent_schema.py`, `tests/test_agent_prompts.py`, `tests/test_journal.py`, `tests/test_theses.py` (new), `tests/test_queries.py`, `tests/test_cycle.py`.

Task order respects dependencies: helpers → plumbing → forecast capture → storage → confirmation → queries → formatters → cycle wiring → app wiring.

---

## Task 1: Formatting primitives in `format.py`

**Files:**
- Modify: `src/trading/reporting/format.py`
- Test: `tests/test_report_format.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_format.py`:

```python
from trading.reporting.format import (
    html_escape, human_horizon, human_days_left, mono_table, pnl_color,
)


def test_html_escape_neutralizes_markup():
    assert html_escape("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_human_horizon_buckets():
    assert human_horizon(3) == "3 дня"
    assert human_horizon(7) == "~1 неделя"
    assert human_horizon(14) == "~2 недели"
    assert human_horizon(30) == "~1 месяц"


def test_human_days_left_handles_overdue():
    assert human_days_left(9) == "~9 дн."
    assert human_days_left(0) == "сегодня"
    assert human_days_left(-2) == "просрочено"


def test_pnl_color_by_sign():
    assert pnl_color(5.0) == "🟢"
    assert pnl_color(-5.0) == "🔴"
    assert pnl_color(0.0) == "🟢"


def test_mono_table_aligns_columns_and_wraps_in_pre():
    out = mono_table(
        [["14.06", "+3", "IWM", "292.95"],
         ["13.06", "-1", "TSLA", "406.43"]],
        aligns="lllr",
    )
    assert out.startswith("<pre>") and out.endswith("</pre>")
    lines = out[len("<pre>"):-len("</pre>")].strip("\n").split("\n")
    # Every line is padded to the same width.
    assert len({len(l) for l in lines}) == 1
    # Symbol column is left-aligned, price column right-aligned.
    assert "IWM " in lines[0]
    assert lines[0].endswith("292.95")


def test_mono_table_escapes_cells():
    out = mono_table([["a<b"]], aligns="l")
    assert "a&lt;b" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report_format.py -k "html_escape or human_horizon or human_days_left or pnl_color or mono_table" -v`
Expected: FAIL with `ImportError` (helpers not defined).

- [ ] **Step 3: Implement the helpers**

Add near the top of `src/trading/reporting/format.py`, after the existing imports:

```python
def html_escape(s: str) -> str:
    """Neutralize the three characters Telegram's HTML parse_mode treats as markup."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def pnl_color(value: float) -> str:
    """Green dot for non-negative money, red for negative."""
    return "🟢" if value >= 0 else "🔴"


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    """Russian plural: forms = (one, few, many). E.g. (1,'день') (2,'дня') (5,'дней')."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def human_horizon(days: int) -> str:
    """Render a horizon in days as a human phrase: '3 дня', '~1 неделя', '~2 недели'."""
    if days < 6:
        return f"{days} {_plural(days, ('день', 'дня', 'дней'))}"
    if days <= 9:
        return "~1 неделя"
    if days <= 24:
        w = round(days / 7)
        return f"~{w} {_plural(w, ('неделя', 'недели', 'недель'))}"
    m = round(days / 30)
    return f"~{m} {_plural(m, ('месяц', 'месяца', 'месяцев'))}"


def human_days_left(days: int) -> str:
    """Render days remaining to a horizon. Non-positive means due/overdue."""
    if days < 0:
        return "просрочено"
    if days == 0:
        return "сегодня"
    return f"~{days} дн."


def mono_table(rows: list[list[str]], aligns: str) -> str:
    """Build a width-aligned monospace table wrapped in <pre>. `aligns` is one char per
    column: 'l' left, 'r' right. Cells are HTML-escaped; no emoji inside (breaks width)."""
    if not rows:
        return "<pre></pre>"
    cells = [[html_escape(c) for c in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(cells[0]))]
    out_lines = []
    for row in cells:
        parts = []
        for i, cell in enumerate(row):
            pad = widths[i] - len(cell)
            parts.append(cell + " " * pad if aligns[i] == "l" else " " * pad + cell)
        out_lines.append(" ".join(parts).rstrip())
    # Re-pad to equal visible width so the block reads as a clean rectangle.
    width = max(len(l) for l in out_lines)
    body = "\n".join(l.ljust(width) for l in out_lines)
    return f"<pre>{body}</pre>"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report_format.py -k "html_escape or human_horizon or human_days_left or pnl_color or mono_table" -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/format.py tests/test_report_format.py
git commit -m "feat: formatting primitives (html escape, horizon, mono tables)"
```

---

## Task 2: `parse_mode="HTML"` plumbing

**Files:**
- Modify: `src/trading/reporting/telegram.py:39-41` (`notify`), `:60-63` (sendMessage in `request_confirmation`), `:70-74` (editMessageText), `:76-77` (answerCallbackQuery unaffected)
- Modify: `src/trading/bot.py:53-62` (`_send`, `_edit`)
- Test: `tests/test_telegram.py`, `tests/test_bot.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram.py`:

```python
def test_notify_sends_html_parse_mode():
    sent = {}

    class C:
        def post(self, url, json=None):
            sent.update(json or {})
            return _Resp({"ok": True})

    n = TelegramNotifier(token="t", chat_id=str(ADMIN), client=C())
    n.notify("hello")
    assert sent["parse_mode"] == "HTML"
```

Append to `tests/test_bot.py` (reuse its existing fake client/bot fixtures; add a focused test). If `tests/test_bot.py` builds a `Bot` via a helper, mirror it; otherwise add:

```python
def test_send_uses_html_parse_mode(monkeypatch):
    from trading.bot import Bot

    sent = []

    class C:
        def post(self, url, json=None):
            sent.append(json)
            class R:
                def json(self_inner):
                    return {"ok": True, "result": {}}
            return R()
        def get(self, url, params=None):
            class R:
                def json(self_inner):
                    return {"ok": True, "result": []}
            return R()

    bot = Bot(client=C(), base="https://api.telegram.org/botX",
              accounts=None, journal=None, freezes=None, run_lock=None,
              agent_ids=[], price_fn=lambda s: 0.0, chat_id=str(ADMIN_ID),
              admin_ids={ADMIN_ID})
    bot._send("hi")
    assert sent[-1]["parse_mode"] == "HTML"
```

Add at the top of `tests/test_bot.py` if not present: `ADMIN_ID = 12345`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_telegram.py::test_notify_sends_html_parse_mode tests/test_bot.py::test_send_uses_html_parse_mode -v`
Expected: FAIL (no `parse_mode` key).

- [ ] **Step 3: Implement**

In `src/trading/reporting/telegram.py`, `notify`:

```python
    def notify(self, text: str) -> None:
        self.client.post(f"{self.base}/sendMessage",
                         json={"chat_id": self.chat_id, "text": f"{self.prefix}{text}",
                               "parse_mode": "HTML"})
```

In `request_confirmation`, the `sendMessage` call (currently lines 60-63):

```python
        sent = self.client.post(
            f"{self.base}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "reply_markup": keyboard,
                  "parse_mode": "HTML"},
        ).json()
```

And the `editMessageText` call inside `finish` (currently lines 71-74):

```python
            self.client.post(
                f"{self.base}/editMessageText",
                json={"chat_id": self.chat_id, "message_id": message_id,
                      "text": f"{text}\n\n— {verdict}", "parse_mode": "HTML"},
            )
```

In `src/trading/bot.py`, `_send` and `_edit`:

```python
    def _send(self, text: str, reply_markup=None) -> None:
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.client.post(f"{self.base}/sendMessage", json=payload)

    def _edit(self, message_id: int, text: str) -> None:
        self.client.post(f"{self.base}/editMessageText",
                         json={"chat_id": self.chat_id, "message_id": message_id,
                               "text": text, "parse_mode": "HTML"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_telegram.py tests/test_bot.py -v`
Expected: PASS (new tests pass; existing tests still pass — they assert on `text`/`callback_data`, not `parse_mode`).

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/telegram.py src/trading/bot.py tests/test_telegram.py tests/test_bot.py
git commit -m "feat: send all Telegram messages with HTML parse_mode"
```

---

## Task 3: Forecast fields in the agent schema and domain

**Files:**
- Modify: `src/trading/domain.py:30-40` (`TradeProposal`)
- Modify: `src/trading/agent/schema.py` (`ProposedTrade`, `to_domain_proposals`)
- Test: `tests/test_agent_schema.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_schema.py`:

```python
from trading.agent.schema import ProposalBatch, ProposedTrade, to_domain_proposals


def _batch(**over):
    base = dict(symbol="AAPL", intent="open_long", quantity=10,
                reference_price=185.0, stop_loss_price=176.0, rationale="rebound")
    base.update(over)
    return ProposalBatch(trades=[ProposedTrade(**base)])


def test_forecast_fields_pass_through():
    p = to_domain_proposals(_batch(target_price=200.0, horizon_days=14), "aggressive")[0]
    assert p.target_price == 200.0
    assert p.horizon_days == 14


def test_wrong_side_target_is_dropped_for_long():
    # Target below entry on a long is incoherent — drop it, keep the trade.
    p = to_domain_proposals(_batch(target_price=170.0, horizon_days=14), "aggressive")[0]
    assert p.target_price is None
    assert p.horizon_days is None


def test_missing_forecast_is_tolerated():
    p = to_domain_proposals(_batch(), "aggressive")[0]
    assert p.target_price is None
    assert p.horizon_days is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_schema.py -k forecast -v` (and the wrong-side/missing tests)
Expected: FAIL (`ProposedTrade` rejects `target_price`; `TradeProposal` has no such attr).

- [ ] **Step 3: Implement**

In `src/trading/domain.py`, extend `TradeProposal` (defaults keep existing constructions valid):

```python
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
    target_price: float | None = None     # forecast: where the agent expects price to go
    horizon_days: int | None = None       # forecast: by when, in calendar days
```

In `src/trading/agent/schema.py`, add the fields and a side-validation helper:

```python
class ProposedTrade(BaseModel):
    symbol: str
    intent: Literal["open_long", "close_long", "open_short", "close_short"]
    quantity: int
    reference_price: float
    stop_loss_price: float | None
    rationale: str
    target_price: float | None = None
    horizon_days: int | None = None


def _coherent_forecast(intent: Intent, reference_price: float,
                       target_price: float | None, horizon_days: int | None) -> bool:
    """A forecast is usable only on an opening trade, with both fields set and the
    target on the correct side: above entry for a long, below for a short."""
    if not intent.is_opening or target_price is None or horizon_days is None:
        return False
    if horizon_days <= 0:
        return False
    return target_price < reference_price if intent.is_short_side else target_price > reference_price


def to_domain_proposals(batch: ProposalBatch, agent_id: str) -> list[TradeProposal]:
    """Pure mapping from the LLM schema to domain TradeProposals, stamping the agent_id.
    Incoherent forecasts (missing, wrong side, non-opening) are dropped to None."""
    out = []
    for t in batch.trades:
        intent = Intent(t.intent)
        keep = _coherent_forecast(intent, t.reference_price, t.target_price, t.horizon_days)
        out.append(TradeProposal(
            agent_id=agent_id,
            symbol=t.symbol,
            intent=intent,
            quantity=t.quantity,
            reference_price=t.reference_price,
            stop_loss_price=t.stop_loss_price,
            rationale=t.rationale,
            target_price=t.target_price if keep else None,
            horizon_days=t.horizon_days if keep else None,
        ))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_schema.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/trading/domain.py src/trading/agent/schema.py tests/test_agent_schema.py
git commit -m "feat: capture target_price and horizon_days on trade proposals"
```

---

## Task 4: Prompt instructions for forecast + plain-language rationale

**Files:**
- Modify: `src/trading/agent/prompts.py:15-32` (`build_system_prompt`)
- Test: `tests/test_agent_prompts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_prompts.py`:

```python
from trading.agent.prompts import build_system_prompt
from trading.config import RiskProfile


def _profile():
    return RiskProfile(
        name="aggressive", budget=10000.0, max_position_pct=0.3, min_positions=2,
        stop_loss_pct=0.05, max_trades_per_day=5, allow_shorts=True,
        veto_rule="any", mandate="")


def test_prompt_demands_target_and_horizon():
    p = build_system_prompt(_profile())
    assert "target_price" in p
    assert "horizon_days" in p


def test_prompt_forbids_indicator_jargon_in_rationale():
    p = build_system_prompt(_profile()).lower()
    assert "rsi" in p or "indicator" in p   # the instruction references jargon to avoid
    assert "plain" in p or "without" in p
```

(If `RiskProfile`'s constructor differs, copy the construction from the existing top of `tests/test_agent_prompts.py` instead of `_profile()`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_prompts.py -k "target or jargon" -v`
Expected: FAIL (strings absent).

- [ ] **Step 3: Implement**

In `src/trading/agent/prompts.py`, add two bullet lines to the constraints block in
`build_system_prompt`, just before the rationale line:

```python
        f"- For every OPENING trade you MUST set target_price (where you expect the price "
        f"to go) and horizon_days (by when, in calendar days). target_price must be on the "
        f"correct side: above reference_price for a long, below it for a short. Closing "
        f"trades leave both null.\n"
        f"- Every proposal needs a concise, concrete rationale.\n"
        f"- Write the rationale in plain language for a non-technical owner: explain the "
        f"idea in words, WITHOUT indicator names or numbers (no 'RSI', 'SMA', 'MACD'). "
        f"Say 'the stock is heavily oversold, I expect a bounce', not 'RSI14=28'.\n"
```

Replace the two existing lines (`- Every proposal needs a concise, concrete rationale.` and
`- Write the rationale field in Russian ...`) with the block above, and keep a Russian-language
instruction:

```python
        f"- Write the rationale field in Russian (the owner reads Russian).\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading/agent/prompts.py tests/test_agent_prompts.py
git commit -m "feat: prompt agent for target/horizon and jargon-free rationale"
```

---

## Task 5: `decisions` migration + record forecast columns

**Files:**
- Modify: `src/trading/persistence/schema.py`
- Modify: `src/trading/persistence/journal.py:17-35` (`record_decision`)
- Test: `tests/test_journal.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_journal.py` (mirror its existing connection/setup helpers — it uses an in-memory sqlite via `init_db`):

```python
import sqlite3

from trading.domain import Intent, Outcome, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db, migrate_db


def test_record_decision_persists_forecast_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    j = JournalRepository(conn)
    p = TradeProposal("aggressive", "AAPL", Intent.OPEN_LONG, 10, 185.0, 176.0,
                      "rebound", target_price=200.0, horizon_days=14)
    j.record_decision("2026-06-14T13:00:00Z", p,
                      GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 10, []))
    row = j.decisions_for("aggressive")[0]
    assert row["target_price"] == 200.0
    assert row["horizon_days"] == 14


def test_migrate_db_adds_columns_to_legacy_decisions():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Legacy decisions table without the forecast columns.
    conn.execute("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent_id TEXT, symbol TEXT,
            intent TEXT, proposed_qty INTEGER, reference_price REAL, stop_loss_price REAL,
            rationale TEXT, outcome TEXT, final_qty INTEGER, reasons TEXT)
    """)
    conn.execute("INSERT INTO decisions (agent_id, reasons) VALUES ('a', '[]')")
    conn.commit()
    migrate_db(conn)
    migrate_db(conn)   # idempotent: second call must not raise
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    assert "target_price" in cols and "horizon_days" in cols
    # Existing row preserved.
    assert conn.execute("SELECT agent_id FROM decisions").fetchone()[0] == "a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_journal.py -k "forecast or migrate" -v`
Expected: FAIL (`migrate_db` undefined; columns missing).

- [ ] **Step 3: Implement**

In `src/trading/persistence/schema.py`, add `target_price`/`horizon_days` to the `decisions`
`CREATE TABLE` (for fresh DBs), then add `migrate_db` and call it from `init_db`:

```python
CREATE TABLE IF NOT EXISTS decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    intent           TEXT NOT NULL,
    proposed_qty     INTEGER NOT NULL,
    reference_price  REAL NOT NULL,
    stop_loss_price  REAL,
    rationale        TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    final_qty        INTEGER NOT NULL,
    reasons          TEXT NOT NULL,
    target_price     REAL,
    horizon_days     INTEGER
);
```

At the bottom of the file:

```python
def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate_db(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release. The SQLite file is the whole
    track record, so we ALTER in place rather than recreate. Idempotent."""
    cols = _columns(conn, "decisions")
    if "target_price" not in cols:
        conn.execute("ALTER TABLE decisions ADD COLUMN target_price REAL")
    if "horizon_days" not in cols:
        conn.execute("ALTER TABLE decisions ADD COLUMN horizon_days INTEGER")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    migrate_db(conn)
    conn.commit()
```

In `src/trading/persistence/journal.py`, `record_decision` writes the two columns:

```python
        cur = self.conn.execute(
            """
            INSERT INTO decisions (
                ts, agent_id, symbol, intent, proposed_qty, reference_price,
                stop_loss_price, rationale, outcome, final_qty, reasons,
                target_price, horizon_days
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts, proposal.agent_id, proposal.symbol, proposal.intent.value,
                proposal.quantity, proposal.reference_price, proposal.stop_loss_price,
                proposal.rationale, decision.outcome.value, decision.quantity,
                json.dumps(decision.reasons),
                proposal.target_price, proposal.horizon_days,
            ),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_journal.py tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading/persistence/schema.py src/trading/persistence/journal.py tests/test_journal.py
git commit -m "feat: persist forecast on decisions with an idempotent migration"
```

---

## Task 6: `ThesisStore` — live forecast per open position

**Files:**
- Modify: `src/trading/persistence/schema.py` (add `theses` table to `SCHEMA_SQL`)
- Create: `src/trading/persistence/theses.py`
- Test: `tests/test_theses.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_theses.py`:

```python
import sqlite3

from trading.persistence.schema import init_db
from trading.persistence.theses import ThesisStore


def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return ThesisStore(conn)


def test_upsert_then_get():
    s = _store()
    s.upsert("aggressive", "AAPL", entry_price=185.0, target_price=200.0,
             horizon_days=14, opened_on="2026-06-14", rationale="rebound")
    row = s.get("aggressive", "AAPL")
    assert row["target_price"] == 200.0
    assert row["entry_price"] == 185.0
    assert row["opened_on"] == "2026-06-14"


def test_upsert_overwrites_existing():
    s = _store()
    s.upsert("aggressive", "AAPL", 185.0, 200.0, 14, "2026-06-14", "v1")
    s.upsert("aggressive", "AAPL", 190.0, 205.0, 10, "2026-06-15", "v2")
    row = s.get("aggressive", "AAPL")
    assert row["entry_price"] == 190.0
    assert row["target_price"] == 205.0
    assert row["rationale"] == "v2"


def test_delete_removes_row():
    s = _store()
    s.upsert("aggressive", "AAPL", 185.0, 200.0, 14, "2026-06-14", "x")
    s.delete("aggressive", "AAPL")
    assert s.get("aggressive", "AAPL") is None


def test_all_for_returns_symbol_map():
    s = _store()
    s.upsert("aggressive", "AAPL", 185.0, 200.0, 14, "2026-06-14", "x")
    s.upsert("aggressive", "IWM", 290.0, 315.0, 9, "2026-06-14", "y")
    s.upsert("moderate", "DIA", 500.0, 540.0, 20, "2026-06-14", "z")
    by_symbol = s.all_for("aggressive")
    assert set(by_symbol) == {"AAPL", "IWM"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_theses.py -v`
Expected: FAIL (`trading.persistence.theses` missing).

- [ ] **Step 3: Implement**

Add to `SCHEMA_SQL` in `src/trading/persistence/schema.py`:

```python
CREATE TABLE IF NOT EXISTS theses (
    agent_id      TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    target_price  REAL NOT NULL,
    horizon_days  INTEGER NOT NULL,
    opened_on     TEXT NOT NULL,        -- YYYY-MM-DD
    rationale     TEXT NOT NULL,
    PRIMARY KEY (agent_id, symbol)
);
```

Create `src/trading/persistence/theses.py`:

```python
from __future__ import annotations

import sqlite3


class ThesisStore:
    """The live forecast for each open position: target, horizon, entry, opened date.
    One row per (agent_id, symbol). Written on open, deleted on full close."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, agent_id: str, symbol: str, entry_price: float, target_price: float,
               horizon_days: int, opened_on: str, rationale: str) -> None:
        self.conn.execute(
            """
            INSERT INTO theses
                (agent_id, symbol, entry_price, target_price, horizon_days, opened_on, rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id, symbol) DO UPDATE SET
                entry_price = excluded.entry_price,
                target_price = excluded.target_price,
                horizon_days = excluded.horizon_days,
                opened_on = excluded.opened_on,
                rationale = excluded.rationale
            """,
            (agent_id, symbol, entry_price, target_price, horizon_days, opened_on, rationale),
        )
        self.conn.commit()

    def get(self, agent_id: str, symbol: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM theses WHERE agent_id = ? AND symbol = ?",
            (agent_id, symbol),
        ).fetchone()

    def delete(self, agent_id: str, symbol: str) -> None:
        self.conn.execute(
            "DELETE FROM theses WHERE agent_id = ? AND symbol = ?", (agent_id, symbol))
        self.conn.commit()

    def all_for(self, agent_id: str) -> dict[str, sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM theses WHERE agent_id = ?", (agent_id,)).fetchall()
        return {r["symbol"]: r for r in rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_theses.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading/persistence/schema.py src/trading/persistence/theses.py tests/test_theses.py
git commit -m "feat: ThesisStore for live per-position forecasts"
```

---

## Task 7: Rewrite the confirmation message

**Files:**
- Modify: `src/trading/reporting/format.py:21-31` (`format_confirmation`)
- Test: `tests/test_report_format.py`

- [ ] **Step 1: Write the failing tests**

Replace the existing `test_format_confirmation_has_agent_trade_notional_and_reason` in
`tests/test_report_format.py` with these:

```python
def test_confirmation_long_shows_expected_profit_and_horizon():
    p = TradeProposal(agent_id="aggressive", symbol="AAPL", intent=Intent.OPEN_LONG,
                      quantity=12, reference_price=185.0, stop_loss_price=176.0,
                      rationale="перепродана, жду отскок",
                      target_price=200.0, horizon_days=14)
    msg = format_confirmation(p, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 12, []))
    assert "AAPL" in msg
    assert "Купить" in msg
    assert "перепродана" in msg
    assert "+8" in msg                      # +8.1% expected return
    assert "180" in msg                     # ≈ +$180 expected profit (12 * (200-185))
    assert "недели" in msg                  # horizon rendered ~2 недели
    assert "176" in msg                     # stop


def test_confirmation_short_inverts_profit_sign():
    p = TradeProposal(agent_id="aggressive", symbol="TSLA", intent=Intent.OPEN_SHORT,
                      quantity=5, reference_price=200.0, stop_loss_price=215.0,
                      rationale="перегрета", target_price=180.0, horizon_days=7)
    msg = format_confirmation(p, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 5, []))
    assert "+10" in msg                     # short to 180 from 200 is +10% gain
    assert "100" in msg                     # ≈ +$100 (5 * (200-180))


def test_confirmation_close_has_no_target_block():
    p = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.CLOSE_LONG,
                      quantity=3, reference_price=190.0, stop_loss_price=None,
                      rationale="фиксирую прибыль")
    msg = format_confirmation(p, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 3, []))
    assert "Цель" not in msg
    assert "фиксирую прибыль" in msg


def test_confirmation_escapes_rationale_html():
    p = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
                      quantity=1, reference_price=10.0, stop_loss_price=9.0,
                      rationale="a < b & c", target_price=12.0, horizon_days=5)
    msg = format_confirmation(p, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 1, []))
    assert "a &lt; b &amp; c" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report_format.py -k confirmation -v`
Expected: FAIL (old format lacks profit/horizon; no HTML escaping).

- [ ] **Step 3: Implement**

Replace `format_confirmation` in `src/trading/reporting/format.py`:

```python
def format_confirmation(proposal: TradeProposal, decision: GuardrailDecision) -> str:
    qty = decision.quantity
    ref = proposal.reference_price
    notional = qty * ref
    verb = "Купить" if proposal.intent in (Intent.OPEN_LONG, Intent.CLOSE_SHORT) else "Продать"
    head = f"❓ <b>{verb} {html_escape(proposal.symbol)}?</b> — {html_escape(proposal.agent_id)}"

    what = (f"<b>Что:</b> {intent_label(proposal.intent.value).lower()} {qty} × "
            f"{html_escape(proposal.symbol)} по ~${ref:,.2f}  (≈ ${notional:,.0f})")
    why = f"<b>Зачем:</b> {html_escape(proposal.rationale)}"
    lines = [head, "", what, why]

    if proposal.target_price is not None and proposal.horizon_days is not None:
        tgt = proposal.target_price
        # Profit if the forecast lands: longs gain as price rises, shorts as it falls.
        if proposal.intent.is_short_side:
            profit = (ref - tgt) * qty
            pct = (ref - tgt) / ref if ref else 0.0
        else:
            profit = (tgt - ref) * qty
            pct = (tgt - ref) / ref if ref else 0.0
        lines.append(f"<b>Цель:</b> {pnl_color(profit)} ${tgt:,.2f} за {human_horizon(proposal.horizon_days)}")
        lines.append(f"        ожидаемая прибыль {pct:+.1%}  (≈ {profit:+,.0f}$)")

    if proposal.stop_loss_price is not None:
        stop = proposal.stop_loss_price
        loss = (stop - ref) * qty if not proposal.intent.is_short_side else (ref - stop) * qty
        stop_pct = (stop - ref) / ref if ref else 0.0
        if proposal.intent.is_short_side:
            stop_pct = (ref - stop) / ref if ref else 0.0
        lines.append(f"<b>Риск:</b> 🔴 стоп ${stop:,.2f}  ({stop_pct:+.1%}, ≈ {loss:+,.0f}$)")

    return "\n".join(lines)
```

Note: `Intent` is already imported indirectly via `TradeProposal`; add `from trading.domain import Intent` to the imports at the top of `format.py` (it currently imports only `TradeProposal`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report_format.py -k confirmation -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/format.py tests/test_report_format.py
git commit -m "feat: plain-language confirmation with expected profit and horizon"
```

---

## Task 8: Query helpers — forecast progress + SPY benchmark

**Files:**
- Modify: `src/trading/reporting/queries.py`
- Test: `tests/test_queries.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_queries.py`:

```python
from trading.reporting.queries import (
    days_left, path_to_target, pnl_report,
)


def test_path_to_target_long():
    # entry 100, target 120, current 110 -> halfway.
    assert path_to_target(100.0, 110.0, 120.0) == 0.5


def test_path_to_target_short():
    # short: entry 200, target 180, current 190 -> halfway.
    assert path_to_target(200.0, 190.0, 180.0) == 0.5


def test_path_to_target_handles_degenerate_target():
    assert path_to_target(100.0, 110.0, 100.0) == 0.0


def test_days_left_counts_down():
    assert days_left("2026-06-14", horizon_days=14, today="2026-06-19") == 9


def test_pnl_report_includes_benchmark_when_fn_given(tmp_path):
    # Build a journal with a two-point equity curve, stub a +2% SPY return.
    import sqlite3
    from trading.persistence.journal import JournalRepository
    from trading.persistence.schema import init_db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    j = JournalRepository(conn)
    j.record_equity_snapshot("aggressive", "2026-06-07", 10000.0)
    j.record_equity_snapshot("aggressive", "2026-06-14", 10800.0)

    rep = pnl_report(j, ["aggressive"], "week",
                     benchmark_fn=lambda start, end: 0.02)
    assert abs(rep.benchmark_pct - 0.02) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_queries.py -k "path_to_target or days_left or benchmark" -v`
Expected: FAIL (functions/field missing).

- [ ] **Step 3: Implement**

In `src/trading/reporting/queries.py`:

Add imports and pure helpers near the top (after the existing imports):

```python
def path_to_target(entry: float, current: float, target: float) -> float:
    """Fraction of the entry→target path covered. Sign-agnostic (works for shorts).
    Degenerate target == entry returns 0.0."""
    span = target - entry
    if span == 0:
        return 0.0
    return (current - entry) / span


def days_left(opened_on: str, horizon_days: int, today: str) -> int:
    """Calendar days remaining until the horizon. Negative once overdue."""
    return horizon_days - (date.fromisoformat(today) - date.fromisoformat(opened_on)).days


def _baseline_date(curve: list[tuple[str, float]], period: str) -> str:
    """Date of the snapshot `_baseline_equity` selects — for benchmark alignment."""
    if period == "all":
        return curve[0][0]
    cutoff = date.fromisoformat(curve[-1][0]) - timedelta(days=_LOOKBACK_DAYS[period])
    chosen = curve[0][0]
    for d, _ in curve:
        if date.fromisoformat(d) <= cutoff:
            chosen = d
        else:
            break
    return chosen
```

Add `benchmark_pct` to `PnlReport` and a `benchmark_fn` parameter to `pnl_report`:

```python
@dataclass(frozen=True)
class PnlReport:
    period: str
    per_agent: list[PnlLine]
    portfolio_start: float
    portfolio_end: float
    portfolio_pnl: float
    portfolio_pct: float
    benchmark_pct: float | None = None
```

```python
def pnl_report(journal: JournalRepository, agent_ids: list[str], period: str,
               benchmark_fn: Callable[[str, str], float | None] | None = None) -> PnlReport:
    per_agent: list[PnlLine] = []
    p_start = p_end = 0.0
    last_curve: list[tuple[str, float]] = []
    for aid in agent_ids:
        curve = journal.equity_curve(aid)
        if not curve:
            continue
        last_curve = curve
        start_eq = _baseline_equity(curve, period)
        end_eq = curve[-1][1]
        pnl = end_eq - start_eq
        pct = pnl / start_eq if start_eq else 0.0
        per_agent.append(PnlLine(aid, start_eq, end_eq, pnl, pct))
        p_start += start_eq
        p_end += end_eq
    p_pnl = p_end - p_start
    p_pct = p_pnl / p_start if p_start else 0.0
    bench = None
    if benchmark_fn is not None and last_curve:
        bench = benchmark_fn(_baseline_date(last_curve, period), last_curve[-1][0])
    return PnlReport(period, per_agent, p_start, p_end, p_pnl, p_pct, bench)
```

Extend `PositionLine` and `positions_report` to carry forecast progress (optional thesis store):

```python
@dataclass(frozen=True)
class PositionLine:
    agent_id: str
    symbol: str
    quantity: int
    avg_price: float
    current_price: float
    unrealized_pnl: float
    target_price: float | None = None
    path_pct: float | None = None
    days_left: int | None = None
```

```python
def positions_report(accounts: AccountRepository, agent_ids: list[str],
                     price_fn: Callable[[str], float],
                     theses=None, today: str | None = None) -> PositionsReport:
    per_agent: dict[str, list[PositionLine]] = {}
    port_unreal = 0.0
    port_mv = 0.0
    for aid in agent_ids:
        state = accounts.get_state(aid)
        lines: list[PositionLine] = []
        forecasts = theses.all_for(aid) if theses is not None else {}
        if state is not None:
            for p in state.positions:
                price = price_fn(p.symbol)
                unreal = (price - p.avg_price) * p.quantity
                tgt = path = left = None
                row = forecasts.get(p.symbol)
                if row is not None:
                    tgt = row["target_price"]
                    path = path_to_target(row["entry_price"], price, row["target_price"])
                    if today is not None:
                        left = days_left(row["opened_on"], row["horizon_days"], today)
                lines.append(PositionLine(aid, p.symbol, p.quantity, p.avg_price,
                                          price, unreal, tgt, path, left))
                port_unreal += unreal
                port_mv += price * p.quantity
        per_agent[aid] = lines
    return PositionsReport(per_agent, port_unreal, port_mv)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queries.py -v`
Expected: PASS (new + existing; existing `positions_report`/`pnl_report` callers still work via defaults).

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/queries.py tests/test_queries.py
git commit -m "feat: forecast progress on positions and SPY benchmark on P&L"
```

---

## Task 9: Regrouped formatters + retro formatter

**Files:**
- Modify: `src/trading/reporting/format.py` (`format_trades`, `format_positions`, `format_pnl_report`; add `format_retro`)
- Test: `tests/test_report_format.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_format.py`:

```python
from trading.reporting.format import format_retro


def test_trades_group_by_agent_and_use_signed_qty():
    rep = TradesReport([
        TradeLine("2026-06-14T13:00:00Z", "aggressive", "open_long", "IWM", 3, 292.95),
        TradeLine("2026-06-13T13:00:00Z", "aggressive", "close_long", "TSLA", 1, 406.43),
        TradeLine("2026-06-14T13:00:00Z", "moderate", "open_long", "DIA", 1, 513.06),
    ])
    msg = format_trades(rep)
    assert "AGGRESSIVE" in msg.upper()
    assert "MODERATE" in msg.upper()
    assert "+3" in msg          # buy -> positive
    assert "-1" in msg or "−1" in msg   # sell -> negative
    assert "<pre>" in msg       # monospace table


def test_pnl_report_header_shows_benchmark():
    rep = PnlReport("week",
                    [PnlLine("aggressive", 13500.0, 13080.0, -420.0, -0.0311)],
                    13500.0, 13080.0, -420.0, -0.0311, benchmark_pct=0.014)
    msg = format_pnl_report(rep)
    assert "SPY" in msg
    assert "1.4%" in msg
    assert "🔴" in msg          # negative aggressive P&L colored red


def test_positions_show_target_and_path():
    rep = PositionsReport(
        {"aggressive": [PositionLine("aggressive", "IWM", 3, 292.95, 298.10, 15.45,
                                     target_price=315.0, path_pct=0.43, days_left=9)]},
        15.45, 894.30)
    msg = format_positions(rep)
    assert "IWM" in msg
    assert "315" in msg
    assert "43%" in msg
    assert "9" in msg


def test_format_retro_reports_forecast_vs_actual():
    msg = format_retro(agent_id="aggressive", symbol="TSLA", quantity=1,
                       entry_price=200.0, exit_price=197.6, target_price=212.0,
                       horizon_days=7, opened_on="2026-06-09", closed_on="2026-06-13",
                       is_short=False)
    assert "TSLA" in msg
    assert "Прогноз" in msg
    assert "По факту" in msg
    assert "🔴" in msg          # losing trade
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report_format.py -k "trades_group or pnl_report_header or positions_show or retro" -v`
Expected: FAIL (`format_retro` missing; no grouping/benchmark/target in output).

- [ ] **Step 3: Implement**

In `src/trading/reporting/format.py`, add a date import at top:

```python
from datetime import date
```

Helpers and rewritten formatters (place near the existing report formatters):

```python
def _group_header(agent_id: str, suffix: str = "") -> str:
    """Bold, normal-text group header (color/emoji allowed here, unlike inside <pre>)."""
    bar = "━" * 18
    return f"━ <b>{html_escape(agent_id.upper())}</b> {bar}{suffix}"


_SIGNED_BUYS = {"open_long", "close_short"}


def format_trades(rep: TradesReport) -> str:
    if not rep.rows:
        return "🧾 <b>Последние сделки</b>\nсделок нет"
    by_agent: dict[str, list[TradeLine]] = {}
    for r in rep.rows:
        by_agent.setdefault(r.agent_id, []).append(r)
    blocks = ["🧾 <b>Последние сделки</b>"]
    for aid, rows in by_agent.items():
        table = mono_table(
            [[f"{r.ts[8:10]}.{r.ts[5:7]}",                              # DD.MM from ISO ts
              (f"+{r.quantity}" if r.intent in _SIGNED_BUYS else f"−{r.quantity}"),
              r.symbol, f"{r.price:,.2f}"] for r in rows],
            aligns="lllr",
        )
        blocks.append(_group_header(aid))
        blocks.append(table)
    return "\n".join(blocks) + "\n\n+N — купил, −N — продал"


def format_pnl_report(rep: PnlReport) -> str:
    bench = ""
    if rep.benchmark_pct is not None:
        verdict = "обыгрываем" if rep.portfolio_pct >= rep.benchmark_pct else "отстаём"
        bench = f"   ·   SPY {rep.benchmark_pct:+.1%} — {verdict}"
    head = (f"💰 <b>P&amp;L за {_PERIOD_RU.get(rep.period, rep.period)}</b>\n"
            f"{pnl_color(rep.portfolio_pnl)} <b>Портфель</b> {rep.portfolio_pnl:+,.0f}$  "
            f"({rep.portfolio_pct:+.1%}){bench}")
    if not rep.per_agent:
        return head + "\nнет данных"
    blocks = [head]
    for l in rep.per_agent:
        blocks.append(_group_header(l.agent_id))
        blocks.append(f"{pnl_color(l.pnl)}  {l.pnl:+,.0f}$   ({l.pct:+.1%})")
        blocks.append(mono_table([["нач.", f"{l.start_equity:,.0f}", "→",
                                    "тек.", f"{l.end_equity:,.0f}"]], aligns="lrllr"))
    return "\n".join(blocks)


def format_positions(rep: PositionsReport) -> str:
    head = (f"📦 <b>Позиции</b> · нереализ. {pnl_color(rep.portfolio_unrealized)} "
            f"{rep.portfolio_unrealized:+,.0f}$")
    blocks = [head]
    for agent_id, lines in rep.per_agent.items():
        blocks.append(_group_header(agent_id))
        if not lines:
            blocks.append("позиций нет")
            continue
        for l in lines:
            side = "LONG" if l.quantity > 0 else "SHORT"
            row = [f"{side} {abs(l.quantity)} {l.symbol}",
                   f"вход {l.avg_price:,.2f} → {l.current_price:,.2f}"]
            if l.target_price is not None:
                row.append(f"цель {l.target_price:,.2f}")
            sub = mono_table([row], aligns="l" * len(row))
            blocks.append(sub)
            tail = f"{pnl_color(l.unrealized_pnl)} {l.unrealized_pnl:+,.0f}$"
            if l.path_pct is not None:
                tail = f"путь к цели {l.path_pct:.0%}   ·   " + tail
            if l.days_left is not None:
                tail = f"ост. {human_days_left(l.days_left)}   ·   " + tail
            blocks.append(tail)
    return "\n".join(blocks)


def format_retro(agent_id: str, symbol: str, quantity: int, entry_price: float,
                 exit_price: float, target_price: float, horizon_days: int,
                 opened_on: str, closed_on: str, is_short: bool) -> str:
    """Pushed when a forecasted position fully closes: forecast vs actual."""
    if is_short:
        realized = (entry_price - exit_price) * quantity
        actual_pct = (entry_price - exit_price) / entry_price if entry_price else 0.0
        expected_pct = (entry_price - target_price) / entry_price if entry_price else 0.0
        path = (entry_price - exit_price) / (entry_price - target_price) if entry_price != target_price else 0.0
    else:
        realized = (exit_price - entry_price) * quantity
        actual_pct = (exit_price - entry_price) / entry_price if entry_price else 0.0
        expected_pct = (target_price - entry_price) / entry_price if entry_price else 0.0
        path = (exit_price - entry_price) / (target_price - entry_price) if target_price != entry_price else 0.0
    used = (date.fromisoformat(closed_on) - date.fromisoformat(opened_on)).days
    return (
        f"🏁 <b>Закрыта позиция</b> · {html_escape(agent_id)}\n"
        f"{html_escape(symbol)} ×{quantity} — итог {pnl_color(realized)} "
        f"{realized:+,.0f}$ ({actual_pct:+.1%})\n\n"
        f"<b>Прогноз был:</b>  {expected_pct:+.1%} за {human_horizon(horizon_days)}\n"
        f"<b>По факту:</b>     {actual_pct:+.1%}, дошли на {max(0.0, path):.0%} пути\n"
        f"<b>Срок:</b>         закрыто на {used}-й день из ~{horizon_days}"
    )
```

Remove the now-unused `_money`/`_delta` helpers only if nothing else references them; otherwise leave them. (`format_status` still uses `_money` — keep it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report_format.py -v`
Expected: PASS (all format tests, including the older ones — re-check `test_format_pnl_report_shows_portfolio_and_agents` and `test_format_positions_*`; update their assertions if they pinned the old plain-text layout, keeping them asserting on stable substrings like the agent id and the signed/colored amounts).

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/format.py tests/test_report_format.py
git commit -m "feat: bot-grouped tables, colored P&L headers, close retrospective"
```

---

## Task 10: Thesis lifecycle + retro emission in the cycle

**Files:**
- Modify: `src/trading/orchestrator/cycle.py`
- Test: `tests/test_cycle.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cycle.py` (reuse its existing fakes — a fake broker, `FakeSource`, in-memory repos; mirror the setup already used by tests in that file). Add a `ThesisStore` and a strategy that opens then closes:

```python
from trading.persistence.theses import ThesisStore


def test_cycle_writes_thesis_on_open_and_emits_retro_on_close(cycle_env):
    # cycle_env is the existing fixture/helper in this file that builds broker, source,
    # accounts, journal, conn. If none exists, construct them as the other tests do.
    env = cycle_env
    theses = ThesisStore(env.conn)

    # Day 1: strategy opens AAPL with a forecast.
    open_proposal = TradeProposal("aggressive", "AAPL", Intent.OPEN_LONG, 5,
                                  reference_price=100.0, stop_loss_price=95.0,
                                  rationale="rebound", target_price=120.0, horizon_days=10)
    run_cycle("aggressive", env.profile, env.broker, env.source, env.accounts,
              env.journal, _StaticStrategy([open_proposal]), ["AAPL"],
              as_of_date="2026-06-14", ts="2026-06-14T13:00:00Z",
              confirm=lambda p, d: True, theses=theses)
    row = theses.get("aggressive", "AAPL")
    assert row is not None and row["target_price"] == 120.0

    # Day 2: strategy closes the whole position; a retro must be pushed and thesis cleared.
    notifier = FakeNotifier()
    close_proposal = TradeProposal("aggressive", "AAPL", Intent.CLOSE_LONG, 5,
                                   reference_price=118.0, stop_loss_price=None,
                                   rationale="take profit")
    run_cycle("aggressive", env.profile, env.broker, env.source, env.accounts,
              env.journal, _StaticStrategy([close_proposal]), ["AAPL"],
              as_of_date="2026-06-16", ts="2026-06-16T13:00:00Z",
              confirm=lambda p, d: True, notifier=notifier, theses=theses)
    assert theses.get("aggressive", "AAPL") is None
    assert any("Закрыта позиция" in m for m in notifier.messages)
```

If `tests/test_cycle.py` has no `_StaticStrategy`/`cycle_env` helper, add a minimal strategy
stub at the top of the file:

```python
class _StaticStrategy:
    def __init__(self, proposals):
        self._proposals = proposals
    def propose(self, briefing, profile):
        return self._proposals
```

…and build `broker/source/accounts/journal/conn` inline exactly as the existing happy-path
test in the file does (copy that setup into a local `cycle_env`-shaped object).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cycle.py -k thesis -v`
Expected: FAIL (`run_cycle` has no `theses` param).

- [ ] **Step 3: Implement**

In `src/trading/orchestrator/cycle.py`, add the `theses` parameter and the lifecycle logic
around the fill. Add to the signature (after `notifier=None`):

```python
    theses=None,
```

Add a small local helper after `equity_now` is defined (or near the top of the function):

```python
    def _position_for(symbol: str):
        for p in broker.positions():
            if p.symbol == symbol:
                return p
        return None
```

Replace the fill/notify block (currently lines 88-103) with:

```python
        entry_action = action_for(proposal.intent)
        fill = broker.place_market_order(proposal.symbol, entry_action, decision.quantity)
        journal.record_fill(ts, agent_id, proposal.symbol, proposal.intent,
                            fill.quantity, fill.price, decision_id)
        if notifier is not None:
            from trading.reporting.format import format_fill
            notifier.notify(format_fill(agent_id, fill))

        if theses is not None:
            after = _position_for(proposal.symbol)
            if proposal.intent.is_opening and proposal.target_price is not None \
                    and proposal.horizon_days is not None:
                # Open or add-to: store/refresh the forecast, entry synced to the
                # position's (possibly averaged) cost.
                entry = after.avg_price if after is not None else fill.price
                theses.upsert(agent_id, proposal.symbol, entry, proposal.target_price,
                              proposal.horizon_days, as_of_date, proposal.rationale)
            elif not proposal.intent.is_opening and (after is None or after.quantity == 0):
                # Full close: emit a retro from the stored thesis, then clear it.
                row = theses.get(agent_id, proposal.symbol)
                if row is not None and notifier is not None:
                    from trading.reporting.format import format_retro
                    notifier.notify(format_retro(
                        agent_id=agent_id, symbol=proposal.symbol, quantity=fill.quantity,
                        entry_price=row["entry_price"], exit_price=fill.price,
                        target_price=row["target_price"], horizon_days=row["horizon_days"],
                        opened_on=row["opened_on"], closed_on=as_of_date,
                        is_short=proposal.intent.is_short_side))
                theses.delete(agent_id, proposal.symbol)

        # Transmit the protective stop to the broker so the position is guarded
        # between daily runs — not just journaled. Guardrails guarantee a valid
        # stop_loss_price on opening trades; the stop trades the opposite side.
        # (This block is preserved verbatim from the original cycle.)
        if proposal.intent.is_opening and proposal.stop_loss_price is not None:
            stop_action = Action.SELL if entry_action is Action.BUY else Action.BUY
            broker.place_stop_order(
                proposal.symbol, stop_action, fill.quantity, proposal.stop_loss_price)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cycle.py -v`
Expected: PASS (new thesis test + existing cycle tests; existing tests omit `theses`, so the new branch is skipped).

- [ ] **Step 5: Commit**

```bash
git add src/trading/orchestrator/cycle.py tests/test_cycle.py
git commit -m "feat: write/clear position theses and push close retrospectives"
```

---

## Task 11: Wire `ThesisStore` through daily run and bot

**Files:**
- Modify: `src/trading/orchestrator/daily.py:50,106` (pass `theses`)
- Modify: `src/trading/run.py:~150` (build `ThesisStore`, pass to daily)
- Modify: `src/trading/bot.py` (`build_bot`, report builders: pass thesis store + benchmark)
- Test: `tests/test_daily.py`, `tests/test_bot.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bot.py` a check that `/positions` is built with the thesis store and
`/pnl` passes a benchmark function. Since `Bot` holds a `journal`, `accounts`, etc., add a
`theses` attribute and a `benchmark_fn`:

```python
def test_bot_positions_uses_theses(monkeypatch):
    import trading.bot as botmod

    captured = {}

    def fake_positions_report(accounts, agent_ids, price_fn, theses=None, today=None):
        captured["theses"] = theses
        from trading.reporting.queries import PositionsReport
        return PositionsReport({}, 0.0, 0.0)

    monkeypatch.setattr(botmod, "positions_report", fake_positions_report)

    sentinel = object()

    class C:
        def post(self, url, json=None):
            class R:
                def json(self_inner): return {"ok": True, "result": {}}
            return R()

    bot = botmod.Bot(client=C(), base="b", accounts=None, journal=None, freezes=None,
                     run_lock=None, agent_ids=[], price_fn=lambda s: 0.0,
                     chat_id="1", admin_ids={1}, theses=sentinel)
    bot.handle_update({"message": {"from": {"id": 1}, "text": "/positions"}})
    assert captured["theses"] is sentinel
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bot.py -k theses -v`
Expected: FAIL (`Bot.__init__` has no `theses`).

- [ ] **Step 3: Implement**

In `src/trading/bot.py`:

- Add `theses=None` to `Bot.__init__` params and store `self.theses = theses`. Add an
  optional `benchmark_fn=None` too: `self.benchmark_fn = benchmark_fn`.
- Update the `/positions` handler to pass the store and today's date:

```python
        elif cmd == "positions":
            self._send(format_positions(positions_report(
                self.accounts, self.agent_ids, self.price_fn,
                theses=self.theses, today=_today_iso())))
```

- Update `_pnl_text` to pass the benchmark:

```python
    def _pnl_text(self, period: str) -> str:
        return format_pnl_report(
            pnl_report(self.journal, self.agent_ids, period, benchmark_fn=self.benchmark_fn))
```

- Add a tiny `_today_iso()` helper (UTC date) near the top of `bot.py`:

```python
from datetime import datetime, timezone


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()
```

- In `build_bot`, construct the store and a SPY benchmark function from the existing
  `source`, and pass both:

```python
    from trading.persistence.theses import ThesisStore

    theses = ThesisStore(conn)

    def benchmark_fn(start_date: str, end_date: str) -> float | None:
        try:
            bars = source.history("SPY", days=400, as_of_date=end_date)
            by_date = {b.date: b.close for b in bars}
            starts = [c for d, c in by_date.items() if d >= start_date]
            if not starts or end_date not in by_date:
                return None
            start_close = min(((d, c) for d, c in by_date.items() if d >= start_date))[1]
            return by_date[end_date] / start_close - 1.0
        except Exception:   # noqa: BLE001 — a benchmark hiccup must not break /pnl
            return None
```

…and add `theses=theses, benchmark_fn=benchmark_fn` to the `Bot(...)` constructor call.

In `src/trading/orchestrator/daily.py`: add `theses=None` to its signature and forward it to
`run_cycle(..., theses=theses)` at the existing call site (line ~106).

In `src/trading/run.py`: build `ThesisStore(conn)` where the other repos are built and pass
`theses=theses` into the daily entry point alongside `confirm=confirm`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bot.py tests/test_daily.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading/bot.py src/trading/orchestrator/daily.py src/trading/run.py tests/test_bot.py
git commit -m "feat: wire ThesisStore and SPY benchmark through daily run and bot"
```

---

## Task 12: Full suite + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `make test`
Expected: all pass. Fix any test that pinned the old plain-text message layout by updating
its assertions to the new HTML/grouped output (assert on stable substrings, not whole strings).

- [ ] **Step 2: Smoke the live wiring with no money, real Claude + Telegram**

Run: `BROKER=fake STRATEGY=claude NOTIFIER=telegram PANEL=off uv run python -m trading.run`
Expected: a confirmation prompt renders in Telegram with "Что/Зачем/Цель/Риск", bold and
🟢/🔴; `/trades`, `/positions`, `/pnl` render as grouped tables with no raw `<`/`&` artifacts
and no "can't parse entities" error from Telegram.

- [ ] **Step 3: Commit (if any test fixups were needed)**

```bash
git add -A
git commit -m "test: update report assertions for the new Telegram layout"
```

---

## Self-review notes

- **Spec coverage:** §1 formatting → Tasks 1,2,9. §2 confirmation → Task 7. §3 forecast capture → Tasks 3,4. §4 storage+migration → Tasks 5,6. §5 display (positions/pnl/retro/benchmark) → Tasks 8,9,10,11. §6 testing → every task is TDD + Task 12.
- **Backward compatibility:** `TradeProposal`, `positions_report`, `pnl_report`, `run_cycle`, and `Bot` all gained parameters with defaults, so existing simulation/test call sites keep working untouched.
- **Type consistency:** forecast fields are `target_price: float | None` / `horizon_days: int | None` everywhere (domain, schema, decisions, theses, queries). `ThesisStore` methods: `upsert/get/delete/all_for`. `format_retro` takes `is_short: bool`; the cycle passes `proposal.intent.is_short_side`.
