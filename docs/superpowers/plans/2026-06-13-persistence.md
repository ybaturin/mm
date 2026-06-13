# Persistence (Ledger & Decision Journal) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Durable storage so the system survives restarts and accumulates the 6-month track record: per-agent ledger (cash, positions, peak equity, day-start equity), a decision journal (every proposal + guardrail verdict), executed fills, and daily equity snapshots for the go-live metrics.

**Architecture:** A thin repository layer over stdlib `sqlite3` — no ORM (YAGNI). Schema is plain SQL created on init. `AccountRepository` reads/writes `AgentState` + `Position` objects from plan 1 directly (round-trips the real domain types). `JournalRepository` records decisions, fills, and equity snapshots, all with caller-supplied timestamps (no hidden clock — keeps it testable). SQLite now; a future Postgres swap stays behind this same repository interface.

**Tech Stack:** Python 3.12+, stdlib `sqlite3` + `json` (no new dependencies), `pytest`.

This is plan **2 of 9**. Depends on plan 1 (domain types). Spec: `docs/superpowers/specs/2026-06-13-ibkr-trading-agents-design.md`.

---

## Existing interfaces this plan consumes (from plan 1, verified)

```python
# src/trading/domain.py
class Intent(str, Enum): OPEN_LONG / CLOSE_LONG / OPEN_SHORT / CLOSE_SHORT
class Outcome(str, Enum): APPROVED_AUTO / NEEDS_CONFIRMATION / REJECTED

@dataclass(frozen=True)
class TradeProposal:
    agent_id: str; symbol: str; intent: Intent; quantity: int
    reference_price: float; stop_loss_price: float | None; rationale: str

@dataclass(frozen=True)
class Position:
    symbol: str; quantity: int; avg_price: float   # quantity signed

@dataclass
class AgentState:
    agent_id: str; cash: float; positions: list[Position]
    peak_equity: float; equity_day_start: float

# src/trading/guardrails/engine.py
@dataclass(frozen=True)
class GuardrailDecision:
    outcome: Outcome; quantity: int; reasons: list[str]
```

## File Structure

```
src/trading/persistence/__init__.py
src/trading/persistence/schema.py      # SQL DDL + init_db(conn)
src/trading/persistence/db.py          # connect(path) -> sqlite3.Connection
src/trading/persistence/accounts.py    # AccountRepository (AgentState <-> rows)
src/trading/persistence/journal.py     # JournalRepository (decisions, fills, equity)
tests/test_db.py
tests/test_accounts.py
tests/test_journal.py
```

**Responsibilities:**
- `schema.py` — single source of truth for the table layout.
- `db.py` — connection setup (row factory, foreign keys). Nothing else.
- `accounts.py` — the live ledger: current cash, positions, equity markers per agent.
- `journal.py` — the append-only history: decisions, fills, equity snapshots.

---

## Task 1: Schema and connection

**Files:**
- Create: `src/trading/persistence/__init__.py` (empty)
- Create: `src/trading/persistence/schema.py`
- Create: `src/trading/persistence/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from trading.persistence.db import connect
from trading.persistence.schema import init_db


def test_init_db_creates_all_tables(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert names == {"accounts", "positions", "decisions", "fills", "equity_snapshots"}


def test_init_db_is_idempotent(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    init_db(conn)  # second call must not raise
    count = conn.execute(
        "SELECT count(*) AS c FROM sqlite_master WHERE type='table'"
    ).fetchone()["c"]
    assert count == 5


def test_connection_uses_row_factory(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    row = conn.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.persistence.db'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/persistence/__init__.py
```

```python
# src/trading/persistence/db.py
from __future__ import annotations

import sqlite3


def connect(path: str) -> sqlite3.Connection:
    """Open a SQLite connection with dict-like rows and FK enforcement."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

```python
# src/trading/persistence/schema.py
from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    agent_id          TEXT PRIMARY KEY,
    cash              REAL NOT NULL,
    peak_equity       REAL NOT NULL,
    equity_day_start  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    agent_id   TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    quantity   INTEGER NOT NULL,   -- signed: + long, - short
    avg_price  REAL NOT NULL,
    PRIMARY KEY (agent_id, symbol),
    FOREIGN KEY (agent_id) REFERENCES accounts(agent_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,        -- ISO 8601, supplied by caller
    agent_id         TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    intent           TEXT NOT NULL,
    proposed_qty     INTEGER NOT NULL,
    reference_price  REAL NOT NULL,
    stop_loss_price  REAL,
    rationale        TEXT NOT NULL,
    outcome          TEXT NOT NULL,        -- Outcome value
    final_qty        INTEGER NOT NULL,
    reasons          TEXT NOT NULL         -- JSON array of strings
);

CREATE TABLE IF NOT EXISTS fills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    intent       TEXT NOT NULL,
    quantity     INTEGER NOT NULL,         -- shares actually filled (unsigned)
    price        REAL NOT NULL,
    decision_id  INTEGER,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    agent_id  TEXT NOT NULL,
    date      TEXT NOT NULL,               -- YYYY-MM-DD
    equity    REAL NOT NULL,
    PRIMARY KEY (agent_id, date)
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/persistence/__init__.py src/trading/persistence/db.py src/trading/persistence/schema.py tests/test_db.py
git commit -m "feat: sqlite schema and connection for persistence"
```

---

## Task 2: AccountRepository — ledger round-trip

**Files:**
- Create: `src/trading/persistence/accounts.py`
- Test: `tests/test_accounts.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_accounts.py
import pytest
from trading.domain import AgentState, Position
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.schema import init_db


@pytest.fixture
def repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return AccountRepository(conn)


def test_save_and_get_state_round_trip(repo):
    state = AgentState(
        agent_id="aggressive", cash=3000.0,
        positions=[
            Position(symbol="AAPL", quantity=10, avg_price=100.0),
            Position(symbol="TSLA", quantity=-5, avg_price=200.0),
        ],
        peak_equity=5000.0, equity_day_start=4800.0,
    )
    repo.save_state(state)

    loaded = repo.get_state("aggressive")
    assert loaded.agent_id == "aggressive"
    assert loaded.cash == 3000.0
    assert loaded.peak_equity == 5000.0
    assert loaded.equity_day_start == 4800.0
    by_symbol = {p.symbol: p for p in loaded.positions}
    assert by_symbol["AAPL"].quantity == 10
    assert by_symbol["TSLA"].quantity == -5
    assert by_symbol["TSLA"].avg_price == 200.0


def test_get_state_unknown_agent_returns_none(repo):
    assert repo.get_state("nobody") is None


def test_save_state_replaces_positions(repo):
    repo.save_state(AgentState(
        agent_id="moderate", cash=1000.0,
        positions=[Position("AAPL", 10, 100.0), Position("MSFT", 5, 300.0)],
        peak_equity=5000.0, equity_day_start=5000.0,
    ))
    # Re-save with AAPL closed (gone) and a new NVDA position
    repo.save_state(AgentState(
        agent_id="moderate", cash=1200.0,
        positions=[Position("MSFT", 5, 300.0), Position("NVDA", 2, 900.0)],
        peak_equity=5000.0, equity_day_start=5000.0,
    ))
    loaded = repo.get_state("moderate")
    assert {p.symbol for p in loaded.positions} == {"MSFT", "NVDA"}
    assert loaded.cash == 1200.0


def test_save_state_with_no_positions(repo):
    repo.save_state(AgentState(
        agent_id="conservative", cash=5000.0, positions=[],
        peak_equity=5000.0, equity_day_start=5000.0,
    ))
    loaded = repo.get_state("conservative")
    assert loaded.positions == []
    assert loaded.cash == 5000.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.persistence.accounts'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/persistence/accounts.py
from __future__ import annotations

import sqlite3

from trading.domain import AgentState, Position


class AccountRepository:
    """Live ledger for the virtual sub-accounts. Reads/writes domain objects directly."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save_state(self, state: AgentState) -> None:
        """Upsert the account row and fully replace its positions (snapshot semantics)."""
        self.conn.execute(
            """
            INSERT INTO accounts (agent_id, cash, peak_equity, equity_day_start)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                cash = excluded.cash,
                peak_equity = excluded.peak_equity,
                equity_day_start = excluded.equity_day_start
            """,
            (state.agent_id, state.cash, state.peak_equity, state.equity_day_start),
        )
        self.conn.execute("DELETE FROM positions WHERE agent_id = ?", (state.agent_id,))
        self.conn.executemany(
            "INSERT INTO positions (agent_id, symbol, quantity, avg_price) VALUES (?, ?, ?, ?)",
            [(state.agent_id, p.symbol, p.quantity, p.avg_price) for p in state.positions],
        )
        self.conn.commit()

    def get_state(self, agent_id: str) -> AgentState | None:
        row = self.conn.execute(
            "SELECT cash, peak_equity, equity_day_start FROM accounts WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        pos_rows = self.conn.execute(
            "SELECT symbol, quantity, avg_price FROM positions WHERE agent_id = ? ORDER BY symbol",
            (agent_id,),
        ).fetchall()
        positions = [
            Position(symbol=r["symbol"], quantity=r["quantity"], avg_price=r["avg_price"])
            for r in pos_rows
        ]
        return AgentState(
            agent_id=agent_id,
            cash=row["cash"],
            positions=positions,
            peak_equity=row["peak_equity"],
            equity_day_start=row["equity_day_start"],
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/persistence/accounts.py tests/test_accounts.py
git commit -m "feat: account repository round-trips agent ledger state"
```

---

## Task 3: JournalRepository — record and query decisions

**Files:**
- Create: `src/trading/persistence/journal.py`
- Test: `tests/test_journal.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_journal.py
import pytest
from trading.domain import Intent, Outcome, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db


@pytest.fixture
def repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


def proposal(symbol="AAPL", qty=10, intent=Intent.OPEN_LONG):
    return TradeProposal(
        agent_id="moderate", symbol=symbol, intent=intent, quantity=qty,
        reference_price=100.0, stop_loss_price=90.0, rationale="momentum",
    )


def test_record_decision_returns_id_and_persists(repo):
    decision = GuardrailDecision(outcome=Outcome.NEEDS_CONFIRMATION, quantity=8, reasons=[])
    did = repo.record_decision("2026-06-15T13:00:00Z", proposal(), decision)
    assert isinstance(did, int) and did > 0

    rows = repo.decisions_for("moderate")
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "AAPL"
    assert r["intent"] == "open_long"
    assert r["proposed_qty"] == 10
    assert r["final_qty"] == 8
    assert r["outcome"] == "needs_confirmation"


def test_record_decision_stores_reasons_as_json(repo):
    decision = GuardrailDecision(
        outcome=Outcome.REJECTED, quantity=0,
        reasons=["Insufficient cash for this buy", "Daily trade limit reached"],
    )
    repo.record_decision("2026-06-15T13:00:00Z", proposal(), decision)
    reasons = repo.reasons_for_latest("moderate")
    assert reasons == ["Insufficient cash for this buy", "Daily trade limit reached"]


def test_decisions_for_filters_by_agent_and_orders_by_time(repo):
    repo.record_decision("2026-06-15T13:00:00Z", proposal(symbol="AAPL"),
                         GuardrailDecision(Outcome.APPROVED_AUTO, 3, []))
    repo.record_decision("2026-06-16T13:00:00Z", proposal(symbol="MSFT"),
                         GuardrailDecision(Outcome.APPROVED_AUTO, 2, []))
    other = TradeProposal("aggressive", "NVDA", Intent.OPEN_LONG, 1, 900.0, 800.0, "x")
    repo.record_decision("2026-06-16T13:00:00Z", other,
                         GuardrailDecision(Outcome.APPROVED_AUTO, 1, []))

    rows = repo.decisions_for("moderate")
    assert [r["symbol"] for r in rows] == ["AAPL", "MSFT"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_journal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.persistence.journal'`.

- [ ] **Step 3: Write the implementation**

```python
# src/trading/persistence/journal.py
from __future__ import annotations

import json
import sqlite3

from trading.domain import TradeProposal
from trading.guardrails.engine import GuardrailDecision


class JournalRepository:
    """Append-only history: decisions, fills, equity snapshots."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record_decision(
        self, ts: str, proposal: TradeProposal, decision: GuardrailDecision
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO decisions (
                ts, agent_id, symbol, intent, proposed_qty, reference_price,
                stop_loss_price, rationale, outcome, final_qty, reasons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts, proposal.agent_id, proposal.symbol, proposal.intent.value,
                proposal.quantity, proposal.reference_price, proposal.stop_loss_price,
                proposal.rationale, decision.outcome.value, decision.quantity,
                json.dumps(decision.reasons),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def decisions_for(self, agent_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM decisions WHERE agent_id = ? ORDER BY ts, id",
            (agent_id,),
        ).fetchall()

    def reasons_for_latest(self, agent_id: str) -> list[str]:
        row = self.conn.execute(
            "SELECT reasons FROM decisions WHERE agent_id = ? ORDER BY id DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        return json.loads(row["reasons"]) if row else []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_journal.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/trading/persistence/journal.py tests/test_journal.py
git commit -m "feat: journal repository records and queries decisions"
```

---

## Task 4: JournalRepository — fills and equity snapshots

**Files:**
- Modify: `src/trading/persistence/journal.py`
- Test: `tests/test_journal.py` (append)

- [ ] **Step 1: Write the failing tests (append to `tests/test_journal.py`)**

```python
def test_record_fill_links_to_decision(repo):
    did = repo.record_decision("2026-06-15T13:00:00Z", proposal(),
                               GuardrailDecision(Outcome.APPROVED_AUTO, 3, []))
    repo.record_fill("2026-06-15T13:30:00Z", agent_id="moderate", symbol="AAPL",
                     intent=Intent.OPEN_LONG, quantity=3, price=101.5, decision_id=did)
    fills = repo.fills_for("moderate")
    assert len(fills) == 1
    assert fills[0]["quantity"] == 3
    assert fills[0]["price"] == 101.5
    assert fills[0]["decision_id"] == did


def test_record_fill_allows_null_decision(repo):
    repo.record_fill("2026-06-15T13:30:00Z", agent_id="moderate", symbol="AAPL",
                     intent=Intent.CLOSE_LONG, quantity=3, price=101.5, decision_id=None)
    assert repo.fills_for("moderate")[0]["decision_id"] is None


def test_equity_snapshot_upserts_by_date(repo):
    repo.record_equity_snapshot("moderate", "2026-06-15", 5010.0)
    repo.record_equity_snapshot("moderate", "2026-06-16", 4980.0)
    repo.record_equity_snapshot("moderate", "2026-06-16", 4990.0)  # same date overwrites
    curve = repo.equity_curve("moderate")
    assert curve == [("2026-06-15", 5010.0), ("2026-06-16", 4990.0)]
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `uv run pytest tests/test_journal.py -v`
Expected: the three new tests FAIL with `AttributeError: 'JournalRepository' object has no attribute 'record_fill'`.

- [ ] **Step 3: Add the methods to `JournalRepository`**

Add these imports at the top of `journal.py` (extend the existing domain import line):

```python
from trading.domain import Intent, TradeProposal
```

Append these methods to the `JournalRepository` class:

```python
    def record_fill(
        self, ts: str, agent_id: str, symbol: str, intent: Intent,
        quantity: int, price: float, decision_id: int | None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO fills (ts, agent_id, symbol, intent, quantity, price, decision_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, agent_id, symbol, intent.value, quantity, price, decision_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def fills_for(self, agent_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM fills WHERE agent_id = ? ORDER BY ts, id",
            (agent_id,),
        ).fetchall()

    def record_equity_snapshot(self, agent_id: str, date: str, equity: float) -> None:
        self.conn.execute(
            """
            INSERT INTO equity_snapshots (agent_id, date, equity)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id, date) DO UPDATE SET equity = excluded.equity
            """,
            (agent_id, date, equity),
        )
        self.conn.commit()

    def equity_curve(self, agent_id: str) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT date, equity FROM equity_snapshots WHERE agent_id = ? ORDER BY date",
            (agent_id,),
        ).fetchall()
        return [(r["date"], r["equity"]) for r in rows]
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `uv run pytest -v`
Expected: PASS (all tests across plan 1 + plan 2 green).

- [ ] **Step 5: Commit**

```bash
git add src/trading/persistence/journal.py tests/test_journal.py
git commit -m "feat: journal records fills and daily equity snapshots"
```

---

## Task 5: Whole-suite check and README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all tests pass, exit code 0.

- [ ] **Step 2: Update the Status section of `README.md`**

Replace the `## Status` section with:

```markdown
## Status

- Plan 1 of 9: domain models, risk-profile config, deterministic Guardrails Engine. ✓
- Plan 2 of 9: SQLite persistence — account ledger, decision journal, fills, daily
  equity snapshots, behind a repository layer. ✓

The trade DB defaults to a local SQLite file; the repository layer keeps a future
Postgres swap isolated from the rest of the code.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: mark persistence plan complete in README"
```

---

## Self-Review

**Spec coverage (this plan's slice — spec §4 component 6 "Ledger + Decision Journal"):**
- Virtual sub-account ledger per agent (cash, positions, peak/day-start equity) →
  `accounts` + `positions` tables, `AccountRepository`. ✓
- Decision journal: proposal + rationale + guardrail verdict + final qty + reasons →
  `decisions` table, `record_decision` / `decisions_for`. ✓ (Enables the 6-month track
  record and weekly self-review, spec §5, §11.)
- Executed trades → `fills` table, linkable to the decision that produced them. ✓
- Daily equity snapshots → `equity_snapshots`, `equity_curve` — feeds Sharpe / drawdown
  for the go-live gate (spec §12). ✓
- `peak_equity` / `equity_day_start` persisted across days → stored on the account row,
  read back by `get_state` (closes the deferral noted in plan 1's self-review). ✓

**Deferred to later plans (correctly out of scope here):**
- Computing P&L / Sharpe / drawdown *metrics* from the snapshots → a reporting/metrics
  step (used by Reporter plan 7 and the go-live evaluation).
- Updating positions/cash from a fill (the accounting math) → Broker Adapter plan 3,
  which owns post-trade state changes; this plan only stores what it is told.
- Postgres backend → behind the same repository interface, only if scale demands it.

**No hidden clock:** every write takes an explicit `ts`/`date` from the caller, so tests
are deterministic and the orchestrator controls timestamps. (Matches the spec rule that
historical replays must be point-in-time, spec §11.)

**Placeholder scan:** none — every step has runnable code and expected output.

**Type consistency:** `AccountRepository(conn)` / `JournalRepository(conn)` constructors,
`save_state(AgentState)` / `get_state -> AgentState | None`, `record_decision(ts, proposal,
decision) -> int`, `record_fill(..., intent: Intent, ..., decision_id: int | None)`,
`record_equity_snapshot(agent_id, date, equity)`, `equity_curve -> list[tuple[str, float]]`
are used identically across Tasks 1–5 and consume the verified plan-1 types. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-persistence.md`.
