# Telegram: ясные сообщения + меню команд — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить Telegram-демон с меню команд (`/positions`, `/pnl`, `/status`, `/trades`) для запроса позиций и P&L в любой момент, и сделать исходящие уведомления яснее.

**Architecture:** Разделяем вычисление (`reporting/queries.py`, чистые функции над репозиториями + ценами), форматирование (`reporting/format.py`, данные→строки) и транспорт (`trading/bot.py`, polling-демон). Конфликт `getUpdates` между демоном и ежедневным прогоном решаем кооперативной паузой через лок в БД (`persistence/runlock.py`, таблица `run_state`).

**Tech Stack:** Python 3.12+, SQLite (`sqlite3`), httpx (Telegram Bot API), pytest. Запуск тестов: `uv run pytest`.

**Соглашения проекта (соблюдать):**
- Тесты на БД: фикстура `connect(str(tmp_path/"t.db"))` → `init_db(conn)` (см. `tests/test_journal.py`).
- Фейковый Telegram-клиент с методами `post(url, json=...)` / `get(url, params=...)`, возвращающими объект с `.json()` (см. `tests/test_telegram.py`).
- Все строки сообщений — русские, plain text (без `parse_mode`).
- `equity_curve(agent)` → `list[(date_str, equity_float)]`, отсортирован по дате.
- Позиции: `quantity` signed (+ лонг, − шорт). Нереализ. P&L = `(price − avg_price) * quantity`.

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `src/trading/persistence/schema.py` (правка) | + таблица `run_state` |
| `src/trading/persistence/runlock.py` (НОВЫЙ) | `RunLock`: acquire/release/is_active с защитой от зависшего лока |
| `src/trading/orchestrator/daily.py` (правка) | обернуть тело `run_daily` в лок (try/finally) |
| `src/trading/reporting/queries.py` (НОВЫЙ) | чистые функции-сводки: positions/pnl/status/trades → dataclass'ы |
| `src/trading/reporting/format.py` (правка) | новые форматтеры + умеренная полировка части A |
| `src/trading/reporting/telegram.py` (правка) | вынести `resolve_admin_ids` в свободную функцию (DRY с ботом) |
| `src/trading/bot.py` (НОВЫЙ) | демон: polling-цикл, диспетчер команд, меню, inline-кнопки `/pnl` |
| `src/trading/run.py` (правка) | создать `RunLock` и передать в `run_daily` |
| `tests/test_runlock.py` (НОВЫЙ) | лок |
| `tests/test_queries.py` (НОВЫЙ) | сводки |
| `tests/test_report_format.py` (правка) | новые форматтеры |
| `tests/test_bot.py` (НОВЫЙ) | диспетчер бота |

---

## Task 1: Таблица `run_state` и `RunLock`

**Files:**
- Modify: `src/trading/persistence/schema.py`
- Create: `src/trading/persistence/runlock.py`
- Test: `tests/test_runlock.py`

- [ ] **Step 1: Добавить таблицу в схему**

В `src/trading/persistence/schema.py`, внутри строки `SCHEMA_SQL`, перед закрывающими `"""`, добавить блок:

```sql

CREATE TABLE IF NOT EXISTS run_state (
    scope   TEXT PRIMARY KEY,    -- 'GLOBAL' (один прогон за раз)
    active  INTEGER NOT NULL,    -- 1 = идёт ежедневный цикл
    since   TEXT                 -- ISO 8601 wall-clock момент захвата лока
);
```

- [ ] **Step 2: Написать падающий тест на RunLock**

Создать `tests/test_runlock.py`:

```python
import pytest
from trading.persistence.db import connect
from trading.persistence.runlock import RunLock
from trading.persistence.schema import init_db


@pytest.fixture
def lock(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return RunLock(conn)


def test_inactive_by_default(lock):
    assert lock.is_active(now_iso="2026-06-13T13:30:00Z") is False


def test_acquire_makes_active(lock):
    lock.acquire(now_iso="2026-06-13T13:30:00Z")
    assert lock.is_active(now_iso="2026-06-13T13:30:30Z") is True


def test_release_makes_inactive(lock):
    lock.acquire(now_iso="2026-06-13T13:30:00Z")
    lock.release()
    assert lock.is_active(now_iso="2026-06-13T13:30:30Z") is False


def test_stale_lock_is_treated_inactive(lock):
    # since 20 минут назад при stale_after_s=900 (15 мин) -> считаем неактивным
    lock.acquire(now_iso="2026-06-13T13:00:00Z")
    assert lock.is_active(now_iso="2026-06-13T13:20:01Z") is False
```

- [ ] **Step 3: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_runlock.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'trading.persistence.runlock'`

- [ ] **Step 4: Реализовать RunLock**

Создать `src/trading/persistence/runlock.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

GLOBAL = "GLOBAL"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str) -> datetime:
    # Принимаем и "...Z", и "...+00:00".
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class RunLock:
    """Cooperative lock so the command daemon pauses its getUpdates polling while a
    daily cycle is running (only one process may consume Telegram updates at a time)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def acquire(self, scope: str = GLOBAL, now_iso: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO run_state (scope, active, since) VALUES (?, 1, ?)
            ON CONFLICT(scope) DO UPDATE SET active = 1, since = excluded.since
            """,
            (scope, now_iso or _now_iso()),
        )
        self.conn.commit()

    def release(self, scope: str = GLOBAL) -> None:
        self.conn.execute(
            "UPDATE run_state SET active = 0 WHERE scope = ?", (scope,))
        self.conn.commit()

    def is_active(self, now_iso: str | None = None, scope: str = GLOBAL,
                  stale_after_s: float = 900.0) -> bool:
        row = self.conn.execute(
            "SELECT active, since FROM run_state WHERE scope = ?", (scope,)).fetchone()
        if row is None or not row["active"]:
            return False
        now = _parse(now_iso) if now_iso else datetime.now(timezone.utc)
        age = (now - _parse(row["since"])).total_seconds()
        return age < stale_after_s
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_runlock.py -v`
Expected: PASS (4 теста)

- [ ] **Step 6: Commit**

```bash
git add src/trading/persistence/schema.py src/trading/persistence/runlock.py tests/test_runlock.py
git commit -m "feat: run_state lock to coordinate getUpdates between daemon and daily run

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Обернуть `run_daily` в лок

**Files:**
- Modify: `src/trading/orchestrator/daily.py`
- Test: `tests/test_daily.py` (добавить один тест)

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/test_daily.py`:

```python
def test_run_daily_acquires_and_releases_run_lock(tmp_path):
    """run_daily ставит лок на время прогона и снимает его в finally."""
    from trading.persistence.db import connect
    from trading.persistence.runlock import RunLock
    from trading.persistence.schema import init_db

    conn = connect(str(tmp_path / "lock.db"))
    init_db(conn)
    lock = RunLock(conn)

    # Лок снят до и после прогона; внутри прогона он был активен.
    seen = {}

    class _Spy(RunLock):
        def acquire(self, *a, **k):
            super().acquire(*a, **k)
            seen["active_during"] = self.is_active(now_iso="2026-06-13T13:30:10Z")

    spy = _Spy(conn)
    run_daily(run_lock=spy, **_minimal_components(tmp_path))
    assert seen["active_during"] is True
    assert lock.is_active(now_iso="2026-06-13T13:30:10Z") is False
```

> Примечание исполнителю: `_minimal_components(tmp_path)` — переиспользуй существующий в `tests/test_daily.py` способ сборки аргументов `run_daily` (там уже есть фикстуры/хелперы для фейкового брокера, источника и репозиториев). Если хелпера нет — собери словарь по образцу первого теста в файле, добавив `as_of_date="2026-06-13"`, `ts="2026-06-13T13:30:00Z"`. Лок передаётся отдельным kwarg `run_lock=`.

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_daily.py::test_run_daily_acquires_and_releases_run_lock -v`
Expected: FAIL с `TypeError: run_daily() got an unexpected keyword argument 'run_lock'`

- [ ] **Step 3: Добавить параметр и обёртку в `run_daily`**

В `src/trading/orchestrator/daily.py` изменить сигнатуру `run_daily`, добавив параметр (после `confirm=None`):

```python
    confirm=None,
    run_lock=None,
) -> None:
```

Затем обернуть всё тело функции (начиная с `if confirm is None:` и до конца) в try/finally. Конкретно: сразу после docstring вставить захват лока, и обернуть остаток:

```python
    if run_lock is not None:
        run_lock.acquire()
    try:
        if confirm is None:
            confirm = make_confirm(notifier)
        # ... весь существующий код тела без изменений ...
    finally:
        if run_lock is not None:
            run_lock.release()
```

> Исполнителю: перенести существующее тело внутрь `try:` с соответствующим отступом, ничего в логике не меняя.

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_daily.py -v`
Expected: PASS (включая новый тест и все существующие)

- [ ] **Step 5: Commit**

```bash
git add src/trading/orchestrator/daily.py tests/test_daily.py
git commit -m "feat: wrap run_daily body in run_lock (acquire/release in try/finally)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `queries.py` — отчёт по P&L за период

**Files:**
- Create: `src/trading/reporting/queries.py`
- Test: `tests/test_queries.py`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_queries.py`:

```python
import pytest
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.reporting.queries import pnl_report


@pytest.fixture
def journal(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


def test_pnl_week_uses_snapshot_on_or_before_cutoff(journal):
    # momentum: за 8 дней до конца equity=10000, в конце 10800.
    journal.record_equity_snapshot("momentum", "2026-06-05", 10000.0)
    journal.record_equity_snapshot("momentum", "2026-06-13", 10800.0)
    rep = pnl_report(journal, ["momentum"], "week")
    line = rep.per_agent[0]
    assert line.agent_id == "momentum"
    assert line.start_equity == 10000.0
    assert line.end_equity == 10800.0
    assert line.pnl == pytest.approx(800.0)
    assert line.pct == pytest.approx(0.08)
    assert rep.portfolio_pnl == pytest.approx(800.0)


def test_pnl_all_uses_first_snapshot(journal):
    journal.record_equity_snapshot("v", "2026-06-01", 5000.0)
    journal.record_equity_snapshot("v", "2026-06-10", 5100.0)
    journal.record_equity_snapshot("v", "2026-06-13", 5300.0)
    rep = pnl_report(journal, ["v"], "all")
    assert rep.per_agent[0].start_equity == 5000.0
    assert rep.per_agent[0].end_equity == 5300.0


def test_pnl_portfolio_sums_agents(journal):
    journal.record_equity_snapshot("a", "2026-06-05", 10000.0)
    journal.record_equity_snapshot("a", "2026-06-13", 11000.0)
    journal.record_equity_snapshot("b", "2026-06-05", 20000.0)
    journal.record_equity_snapshot("b", "2026-06-13", 19000.0)
    rep = pnl_report(journal, ["a", "b"], "week")
    assert rep.portfolio_start == pytest.approx(30000.0)
    assert rep.portfolio_end == pytest.approx(30000.0)
    assert rep.portfolio_pnl == pytest.approx(0.0)


def test_pnl_skips_agents_without_snapshots(journal):
    journal.record_equity_snapshot("a", "2026-06-13", 10000.0)
    rep = pnl_report(journal, ["a", "ghost"], "week")
    assert [l.agent_id for l in rep.per_agent] == ["a"]
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_queries.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'trading.reporting.queries'`

- [ ] **Step 3: Реализовать `pnl_report` и dataclass'ы**

Создать `src/trading/reporting/queries.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from trading.persistence.accounts import AccountRepository
from trading.persistence.freezes import FreezeStore
from trading.persistence.journal import JournalRepository

_LOOKBACK_DAYS = {"day": 1, "week": 7, "month": 30}


@dataclass(frozen=True)
class PnlLine:
    agent_id: str
    start_equity: float
    end_equity: float
    pnl: float
    pct: float


@dataclass(frozen=True)
class PnlReport:
    period: str
    per_agent: list[PnlLine]
    portfolio_start: float
    portfolio_end: float
    portfolio_pnl: float
    portfolio_pct: float


def _baseline_equity(curve: list[tuple[str, float]], period: str) -> float:
    """Equity at the period's start: the snapshot on-or-before (last_date - N days),
    or the earliest snapshot when none qualifies / period == 'all'."""
    if period == "all":
        return curve[0][1]
    cutoff = date.fromisoformat(curve[-1][0]) - timedelta(days=_LOOKBACK_DAYS[period])
    baseline = curve[0][1]
    for d, e in curve:
        if date.fromisoformat(d) <= cutoff:
            baseline = e
        else:
            break
    return baseline


def pnl_report(journal: JournalRepository, agent_ids: list[str], period: str) -> PnlReport:
    per_agent: list[PnlLine] = []
    p_start = p_end = 0.0
    for aid in agent_ids:
        curve = journal.equity_curve(aid)
        if not curve:
            continue
        start_eq = _baseline_equity(curve, period)
        end_eq = curve[-1][1]
        pnl = end_eq - start_eq
        pct = pnl / start_eq if start_eq else 0.0
        per_agent.append(PnlLine(aid, start_eq, end_eq, pnl, pct))
        p_start += start_eq
        p_end += end_eq
    p_pnl = p_end - p_start
    p_pct = p_pnl / p_start if p_start else 0.0
    return PnlReport(period, per_agent, p_start, p_end, p_pnl, p_pct)
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_queries.py -v`
Expected: PASS (4 теста)

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/queries.py tests/test_queries.py
git commit -m "feat: pnl_report query (period P&L per agent + portfolio)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `queries.py` — отчёт по позициям

**Files:**
- Modify: `src/trading/reporting/queries.py`
- Test: `tests/test_queries.py` (добавить тесты)

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_queries.py` (импорт сверху расширить):

```python
from trading.reporting.queries import positions_report  # add to imports
from trading.domain import AgentState, Position
from trading.persistence.accounts import AccountRepository


@pytest.fixture
def accounts(tmp_path):
    conn = connect(str(tmp_path / "acc.db"))
    init_db(conn)
    return AccountRepository(conn)


def test_positions_report_values_long_and_short(accounts):
    accounts.save_state(AgentState(
        "momentum", cash=1000.0,
        positions=[Position("AAPL", 10, 200.0), Position("TSLA", -5, 250.0)]))
    prices = {"AAPL": 210.0, "TSLA": 240.0}
    rep = positions_report(accounts, ["momentum"], lambda s: prices[s])
    lines = rep.per_agent["momentum"]
    aapl = next(l for l in lines if l.symbol == "AAPL")
    tsla = next(l for l in lines if l.symbol == "TSLA")
    assert aapl.unrealized_pnl == pytest.approx(100.0)    # (210-200)*10
    assert tsla.unrealized_pnl == pytest.approx(50.0)     # (240-250)*-5
    assert rep.portfolio_unrealized == pytest.approx(150.0)


def test_positions_report_empty_agent(accounts):
    accounts.save_state(AgentState("flat", cash=5000.0, positions=[]))
    rep = positions_report(accounts, ["flat"], lambda s: 1.0)
    assert rep.per_agent["flat"] == []
    assert rep.portfolio_unrealized == 0.0
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_queries.py::test_positions_report_values_long_and_short -v`
Expected: FAIL с `ImportError: cannot import name 'positions_report'`

- [ ] **Step 3: Реализовать `positions_report`**

Добавить в `src/trading/reporting/queries.py`:

```python
from typing import Callable  # add near top imports


@dataclass(frozen=True)
class PositionLine:
    agent_id: str
    symbol: str
    quantity: int
    avg_price: float
    current_price: float
    unrealized_pnl: float


@dataclass(frozen=True)
class PositionsReport:
    per_agent: dict[str, list[PositionLine]]
    portfolio_unrealized: float
    portfolio_market_value: float


def positions_report(accounts: AccountRepository, agent_ids: list[str],
                     price_fn: Callable[[str], float]) -> PositionsReport:
    per_agent: dict[str, list[PositionLine]] = {}
    port_unreal = 0.0
    port_mv = 0.0
    for aid in agent_ids:
        state = accounts.get_state(aid)
        lines: list[PositionLine] = []
        if state is not None:
            for p in state.positions:
                price = price_fn(p.symbol)
                unreal = (price - p.avg_price) * p.quantity
                lines.append(PositionLine(aid, p.symbol, p.quantity, p.avg_price,
                                          price, unreal))
                port_unreal += unreal
                port_mv += price * p.quantity
        per_agent[aid] = lines
    return PositionsReport(per_agent, port_unreal, port_mv)
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_queries.py -v`
Expected: PASS (все тесты файла)

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/queries.py tests/test_queries.py
git commit -m "feat: positions_report query (mark-to-market per agent + portfolio)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `queries.py` — статус и сделки

**Files:**
- Modify: `src/trading/reporting/queries.py`
- Test: `tests/test_queries.py` (добавить тесты)

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_queries.py`:

```python
from trading.reporting.queries import status_report, trades_report  # add to imports
from trading.persistence.freezes import FreezeStore
from trading.domain import Intent


def test_status_report_aggregates(tmp_path):
    conn = connect(str(tmp_path / "s.db"))
    init_db(conn)
    acc = AccountRepository(conn)
    jr = JournalRepository(conn)
    fr = FreezeStore(conn)
    acc.save_state(AgentState("a", cash=1000.0, positions=[Position("AAPL", 10, 100.0)]))
    jr.record_equity_snapshot("a", "2026-06-12", 1900.0)
    jr.record_equity_snapshot("a", "2026-06-13", 2000.0)   # +100 сегодня
    fr.freeze("a", "manual hold", "2026-06-13T13:00:00Z")
    rep = status_report(acc, jr, fr, ["a"], lambda s: 100.0)
    assert rep.portfolio_equity == pytest.approx(2000.0)   # 1000 cash + 10*100
    assert rep.today_pnl == pytest.approx(100.0)
    assert rep.open_positions_count == 1
    assert rep.freezes == [("a", "manual hold")]


def test_trades_report_sorts_desc_and_limits(tmp_path):
    conn = connect(str(tmp_path / "tr.db"))
    init_db(conn)
    jr = JournalRepository(conn)
    jr.record_fill("2026-06-11T13:30:00Z", "a", "AAPL", Intent.OPEN_LONG, 5, 100.0, None)
    jr.record_fill("2026-06-13T13:30:00Z", "b", "TSLA", Intent.OPEN_SHORT, 3, 250.0, None)
    jr.record_fill("2026-06-12T13:30:00Z", "a", "MSFT", Intent.OPEN_LONG, 2, 400.0, None)
    rep = trades_report(jr, ["a", "b"], limit=2)
    assert [r.symbol for r in rep.rows] == ["TSLA", "MSFT"]   # самые свежие сверху
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_queries.py::test_status_report_aggregates -v`
Expected: FAIL с `ImportError: cannot import name 'status_report'`

- [ ] **Step 3: Реализовать `status_report` и `trades_report`**

Добавить в `src/trading/reporting/queries.py`:

```python
@dataclass(frozen=True)
class StatusReport:
    portfolio_equity: float
    today_pnl: float
    today_pct: float
    open_positions_count: int
    freezes: list[tuple[str, str]]


@dataclass(frozen=True)
class TradeLine:
    ts: str
    agent_id: str
    intent: str
    symbol: str
    quantity: int
    price: float


@dataclass(frozen=True)
class TradesReport:
    rows: list[TradeLine]


def status_report(accounts: AccountRepository, journal: JournalRepository,
                  freezes: FreezeStore, agent_ids: list[str],
                  price_fn: Callable[[str], float]) -> StatusReport:
    total_equity = 0.0
    today_pnl = 0.0
    open_count = 0
    for aid in agent_ids:
        state = accounts.get_state(aid)
        if state is None:
            continue
        prices = {p.symbol: price_fn(p.symbol) for p in state.positions}
        total_equity += state.equity(prices)
        open_count += len(state.positions)
        curve = journal.equity_curve(aid)
        if len(curve) >= 2:
            today_pnl += curve[-1][1] - curve[-2][1]
    base = total_equity - today_pnl
    today_pct = today_pnl / base if base else 0.0
    frozen = [(s, freezes.reason(s) or "") for s in freezes.frozen_scopes()]
    return StatusReport(total_equity, today_pnl, today_pct, open_count, frozen)


def trades_report(journal: JournalRepository, agent_ids: list[str],
                  limit: int = 10) -> TradesReport:
    rows = []
    for aid in agent_ids:
        rows.extend(journal.fills_for(aid))
    rows.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
    lines = [TradeLine(r["ts"], r["agent_id"], r["intent"], r["symbol"],
                       r["quantity"], r["price"]) for r in rows[:limit]]
    return TradesReport(lines)
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_queries.py -v`
Expected: PASS (все тесты файла)

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/queries.py tests/test_queries.py
git commit -m "feat: status_report and trades_report queries

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Форматтеры новых отчётов

**Files:**
- Modify: `src/trading/reporting/format.py`
- Test: `tests/test_report_format.py` (добавить тесты)

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/test_report_format.py`:

```python
from trading.reporting.format import (
    format_positions, format_pnl_report, format_status, format_trades,
)
from trading.reporting.queries import (
    PnlLine, PnlReport, PositionLine, PositionsReport,
    StatusReport, TradeLine, TradesReport,
)


def test_format_pnl_report_shows_portfolio_and_agents():
    rep = PnlReport("week",
                    [PnlLine("momentum", 10000.0, 10800.0, 800.0, 0.08)],
                    10000.0, 10800.0, 800.0, 0.08)
    msg = format_pnl_report(rep)
    assert "неделю" in msg
    assert "momentum" in msg
    assert "+800" in msg or "800.00" in msg
    assert "8.0%" in msg


def test_format_positions_marks_direction_and_pnl():
    rep = PositionsReport(
        {"momentum": [PositionLine("momentum", "AAPL", 10, 200.0, 210.0, 100.0)]},
        100.0, 2100.0)
    msg = format_positions(rep)
    assert "AAPL" in msg
    assert "LONG" in msg
    assert "+100" in msg or "100.00" in msg


def test_format_positions_empty_agent_says_so():
    rep = PositionsReport({"flat": []}, 0.0, 0.0)
    msg = format_positions(rep)
    assert "flat" in msg
    assert "позиций нет" in msg.lower()


def test_format_status_shows_equity_and_freezes():
    rep = StatusReport(2000.0, 100.0, 0.0526, 1, [("a", "manual hold")])
    msg = format_status(rep)
    assert "2,000" in msg
    assert "manual hold" in msg


def test_format_trades_lists_fills():
    rep = TradesReport([TradeLine("2026-06-13T13:30:00Z", "b", "open_short",
                                  "TSLA", 3, 250.0)])
    msg = format_trades(rep)
    assert "TSLA" in msg and "b" in msg


def test_format_trades_handles_empty():
    assert "сделок нет" in format_trades(TradesReport([])).lower()
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_report_format.py -k "pnl_report or positions or status or trades" -v`
Expected: FAIL с `ImportError: cannot import name 'format_pnl_report'`

- [ ] **Step 3: Реализовать форматтеры**

Добавить в конец `src/trading/reporting/format.py`:

```python
from trading.reporting.queries import (  # noqa: E402  (внизу, чтобы избежать цикла импорта)
    PnlReport, PositionsReport, StatusReport, TradesReport,
)

_PERIOD_RU = {"day": "сегодня", "week": "неделю", "month": "месяц", "all": "всё время"}


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _delta(pnl: float, pct: float) -> str:
    return f"({pnl:+,.2f}, {pct:+.1%})"


def format_pnl_report(rep: PnlReport) -> str:
    head = (f"💰 P&L за {_PERIOD_RU.get(rep.period, rep.period)}\n"
            f"Портфель: {_money(rep.portfolio_start)} → {_money(rep.portfolio_end)}  "
            f"{_delta(rep.portfolio_pnl, rep.portfolio_pct)}")
    if not rep.per_agent:
        return head + "\nнет данных"
    lines = [f"  • {l.agent_id}: {_money(l.start_equity)} → {_money(l.end_equity)} "
             f"{_delta(l.pnl, l.pct)}" for l in rep.per_agent]
    return head + "\n" + "\n".join(lines)


def format_positions(rep: PositionsReport) -> str:
    head = (f"📦 Активные позиции\n"
            f"Портфель: рыночная стоимость {_money(rep.portfolio_market_value)}, "
            f"нереализ. P&L {rep.portfolio_unrealized:+,.2f}")
    blocks = []
    for agent_id, lines in rep.per_agent.items():
        if not lines:
            blocks.append(f"{agent_id}: позиций нет")
            continue
        rows = []
        for l in lines:
            side = "LONG" if l.quantity > 0 else "SHORT"
            rows.append(f"  • {side} {abs(l.quantity)} {l.symbol} "
                        f"@ {_money(l.avg_price)} → {_money(l.current_price)}  "
                        f"(P&L {l.unrealized_pnl:+,.2f})")
        blocks.append(f"{agent_id}:\n" + "\n".join(rows))
    return head + "\n" + "\n".join(blocks)


def format_status(rep: StatusReport) -> str:
    frozen = ("нет" if not rep.freezes
              else "; ".join(f"{scope} — {reason}" for scope, reason in rep.freezes))
    return (f"📋 Статус\n"
            f"Портфель: {_money(rep.portfolio_equity)}  "
            f"(сегодня {rep.today_pnl:+,.2f}, {rep.today_pct:+.1%})\n"
            f"Открытых позиций: {rep.open_positions_count}\n"
            f"Заморозки: {frozen}")


def format_trades(rep: TradesReport) -> str:
    if not rep.rows:
        return "🧾 Последние сделки\nсделок нет"
    lines = [f"  • {r.ts[:10]} {r.agent_id} {intent_label(r.intent)} "
             f"{r.quantity} {r.symbol} @ {_money(r.price)}" for r in rep.rows]
    return "🧾 Последние сделки\n" + "\n".join(lines)
```

> Исполнителю: `intent_label` уже определён в этом же файле выше — используем его. Импорт `queries` стоит внизу файла намеренно: `format.py` импортирует типы из `queries.py`, а `queries.py` не импортирует `format.py`, цикла нет, но импорт внизу держит верх файла чистым и совпадает с тем, что типы — только для аннотаций/распаковки.

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_report_format.py -v`
Expected: PASS (новые + существующие тесты)

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/format.py tests/test_report_format.py
git commit -m "feat: formatters for positions/pnl/status/trades reports

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Часть A — полировка исходящих сообщений

**Files:**
- Modify: `src/trading/reporting/format.py`
- Test: `tests/test_report_format.py` (обновить существующие)

- [ ] **Step 1: Обновить тесты под новый формат**

В `tests/test_report_format.py` заменить тело `test_format_fill_reads_naturally` на:

```python
def test_format_fill_reads_naturally():
    fill = Fill(symbol="AAPL", action=Action.BUY, quantity=3, price=101.5)
    msg = format_fill("moderate", fill)
    assert "moderate" in msg
    assert "AAPL" in msg
    assert "Покупка" in msg
    assert "3" in msg
    assert "101.50" in msg
    assert "исполнена" in msg.lower()
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_report_format.py::test_format_fill_reads_naturally -v`
Expected: FAIL (нет «исполнена» в текущем выводе)

- [ ] **Step 3: Обновить `format_fill` и `format_confirmation`**

В `src/trading/reporting/format.py` заменить `format_fill` на:

```python
def format_fill(agent_id: str, fill: Fill) -> str:
    action = _ACTION_RU.get(fill.action.value, fill.action.value)
    notional = fill.quantity * fill.price
    return (f"✅ Сделка исполнена · {agent_id}\n"
            f"{action} {fill.quantity} × {fill.symbol} @ ${fill.price:,.2f}  "
            f"(≈ ${notional:,.0f})")
```

И заменить `format_confirmation` на (стиль выровнен, логика та же):

```python
def format_confirmation(proposal: TradeProposal, decision: GuardrailDecision) -> str:
    notional = decision.quantity * proposal.reference_price
    stop = "—" if proposal.stop_loss_price is None else f"${proposal.stop_loss_price:,.2f}"
    intent = _INTENT_RU.get(proposal.intent.value, proposal.intent.value)
    return (
        f"❓ Подтвердить сделку? · {proposal.agent_id}\n"
        f"{intent}: {decision.quantity} × {proposal.symbol} "
        f"@ ~${proposal.reference_price:,.2f}  (≈ ${notional:,.0f})\n"
        f"стоп: {stop}\n"
        f"основание: {proposal.rationale}"
    )
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_report_format.py -v`
Expected: PASS

> Если `test_format_confirmation_has_agent_trade_notional_and_reason` упадёт на проверке `"1000" in msg or "1,000" in msg`: 5×200=1000 → формат даёт `1,000`, проверка проходит. Ничего менять не нужно.

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/format.py tests/test_report_format.py
git commit -m "feat: clearer fill and confirmation messages (emoji header, notional, separators)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Вынести `resolve_admin_ids` (DRY с ботом)

**Files:**
- Modify: `src/trading/reporting/telegram.py`
- Test: `tests/test_telegram.py` (добавить тест) — существующие должны продолжать проходить

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_telegram.py`:

```python
def test_resolve_admin_ids_from_env(monkeypatch):
    from trading.reporting.telegram import resolve_admin_ids
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "111, 222")
    assert resolve_admin_ids("999") == {111, 222}


def test_resolve_admin_ids_falls_back_to_chat_id(monkeypatch):
    from trading.reporting.telegram import resolve_admin_ids
    monkeypatch.delenv("TELEGRAM_ADMIN_IDS", raising=False)
    assert resolve_admin_ids("999") == {999}
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_telegram.py::test_resolve_admin_ids_from_env -v`
Expected: FAIL с `ImportError: cannot import name 'resolve_admin_ids'`

- [ ] **Step 3: Вынести свободную функцию и переиспользовать в классе**

В `src/trading/reporting/telegram.py` добавить свободную функцию (после импортов, до класса):

```python
def resolve_admin_ids(chat_id: str | None) -> set[int]:
    """Telegram user ids allowed to act. From TELEGRAM_ADMIN_IDS (comma-separated)
    if set, else the configured chat_id (correct for a private 1:1 chat)."""
    raw = os.environ.get("TELEGRAM_ADMIN_IDS")
    if raw:
        return {int(x) for x in raw.split(",") if x.strip()}
    try:
        return {int(chat_id)}
    except (TypeError, ValueError):
        return set()
```

Затем заменить метод `_resolve_admin_ids` так, чтобы он делегировал:

```python
    def _resolve_admin_ids(self) -> set[int]:
        return resolve_admin_ids(self.chat_id)
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `uv run pytest tests/test_telegram.py -v`
Expected: PASS (новые + все существующие тесты авторизации)

- [ ] **Step 5: Commit**

```bash
git add src/trading/reporting/telegram.py tests/test_telegram.py
git commit -m "refactor: extract resolve_admin_ids free function for reuse by bot

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `bot.py` — диспетчер команд

**Files:**
- Create: `src/trading/bot.py`
- Test: `tests/test_bot.py`

Диспетчер обрабатывает один Telegram-апдейт: проверяет отправителя (admin), маршрутизирует команду в query+format и отправляет ответ через клиент. Цикл polling и `__main__` — в следующей задаче.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_bot.py`:

```python
import pytest
from trading.bot import Bot
from trading.domain import AgentState, Position
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.freezes import FreezeStore
from trading.persistence.journal import JournalRepository
from trading.persistence.runlock import RunLock
from trading.persistence.schema import init_db

ADMIN = 12345


class FakeClient:
    """Records outbound calls; mirrors the post/get shape of httpx used elsewhere."""

    def __init__(self):
        self.sent = []          # list of (text, reply_markup)
        self.edits = []         # list of (message_id, text)
        self.answered = []
        self.commands = None

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def post(self, url, json=None):
        if url.endswith("/sendMessage"):
            self.sent.append((json["text"], json.get("reply_markup")))
            return self._Resp({"result": {"message_id": 1}})
        if url.endswith("/editMessageText"):
            self.edits.append((json["message_id"], json["text"]))
            return self._Resp({"result": {}})
        if url.endswith("/answerCallbackQuery"):
            self.answered.append(json["callback_query_id"])
            return self._Resp({"ok": True})
        if url.endswith("/setMyCommands"):
            self.commands = json["commands"]
            return self._Resp({"ok": True})
        return self._Resp({"ok": True})


@pytest.fixture
def bot():
    conn = connect(":memory:")
    init_db(conn)
    acc = AccountRepository(conn)
    jr = JournalRepository(conn)
    fr = FreezeStore(conn)
    acc.save_state(AgentState("momentum", cash=1000.0,
                              positions=[Position("AAPL", 10, 200.0)]))
    jr.record_equity_snapshot("momentum", "2026-06-05", 3000.0)
    jr.record_equity_snapshot("momentum", "2026-06-13", 3000.0)
    client = FakeClient()
    bot = Bot(client=client, base="https://api.telegram.org/botT",
              accounts=acc, journal=jr, freezes=fr, run_lock=RunLock(conn),
              agent_ids=["momentum"], price_fn=lambda s: 210.0,
              chat_id=str(ADMIN), admin_ids={ADMIN})
    return bot, client


def _message(text, sender=ADMIN):
    return {"update_id": 1, "message": {"from": {"id": sender}, "text": text}}


def test_positions_command_replies(bot):
    b, client = bot
    b.handle_update(_message("/positions"))
    assert client.sent
    assert "AAPL" in client.sent[0][0]


def test_status_command_replies(bot):
    b, client = bot
    b.handle_update(_message("/status"))
    assert "Статус" in client.sent[0][0]


def test_pnl_without_arg_sends_period_buttons(bot):
    b, client = bot
    b.handle_update(_message("/pnl"))
    text, markup = client.sent[0]
    assert markup is not None
    datas = [btn["callback_data"] for btn in markup["inline_keyboard"][0]]
    assert "pnl:week" in datas


def test_pnl_with_arg_replies_directly(bot):
    b, client = bot
    b.handle_update(_message("/pnl week"))
    assert "P&L" in client.sent[0][0]
    assert client.sent[0][1] is None       # без кнопок


def test_pnl_callback_edits_message(bot):
    b, client = bot
    cb = {"update_id": 2, "callback_query": {
        "id": "cb1", "from": {"id": ADMIN},
        "message": {"message_id": 77}, "data": "pnl:month"}}
    b.handle_update(cb)
    assert client.edits and client.edits[0][0] == 77
    assert "P&L" in client.edits[0][1]
    assert client.answered == ["cb1"]


def test_ignores_non_admin(bot):
    b, client = bot
    b.handle_update(_message("/positions", sender=99999))
    assert client.sent == []
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_bot.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'trading.bot'`

- [ ] **Step 3: Реализовать `Bot` (диспетчер)**

Создать `src/trading/bot.py`:

```python
from __future__ import annotations

from trading.reporting.format import (
    format_pnl_report, format_positions, format_status, format_trades,
)
from trading.reporting.queries import (
    pnl_report, positions_report, status_report, trades_report,
)

_PERIODS = ("day", "week", "month", "all")
_PNL_BUTTONS = {"inline_keyboard": [[
    {"text": "Сегодня", "callback_data": "pnl:day"},
    {"text": "Неделя", "callback_data": "pnl:week"},
    {"text": "Месяц", "callback_data": "pnl:month"},
    {"text": "Всё", "callback_data": "pnl:all"},
]]}

COMMANDS = [
    {"command": "positions", "description": "Активные позиции"},
    {"command": "pnl", "description": "P&L за период"},
    {"command": "status", "description": "Краткая сводка"},
    {"command": "trades", "description": "Последние сделки"},
]


class Bot:
    """Telegram command dispatcher: reads the DB and answers /positions, /pnl,
    /status, /trades. Transport is injected (httpx-shaped client)."""

    def __init__(self, client, base: str, accounts, journal, freezes, run_lock,
                 agent_ids: list[str], price_fn, chat_id: str, admin_ids: set[int]) -> None:
        self.client = client
        self.base = base
        self.accounts = accounts
        self.journal = journal
        self.freezes = freezes
        self.run_lock = run_lock
        self.agent_ids = agent_ids
        self.price_fn = price_fn
        self.chat_id = chat_id
        self.admin_ids = admin_ids

    # --- transport helpers ---
    def _send(self, text: str, reply_markup=None) -> None:
        payload = {"chat_id": self.chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.client.post(f"{self.base}/sendMessage", json=payload)

    def _edit(self, message_id: int, text: str) -> None:
        self.client.post(f"{self.base}/editMessageText",
                         json={"chat_id": self.chat_id, "message_id": message_id,
                               "text": text})

    def _answer(self, cb_id: str) -> None:
        self.client.post(f"{self.base}/answerCallbackQuery",
                         json={"callback_query_id": cb_id})

    def set_my_commands(self) -> None:
        self.client.post(f"{self.base}/setMyCommands", json={"commands": COMMANDS})

    # --- report builders ---
    def _pnl_text(self, period: str) -> str:
        return format_pnl_report(pnl_report(self.journal, self.agent_ids, period))

    # --- dispatch ---
    def handle_update(self, upd: dict) -> None:
        cb = upd.get("callback_query")
        if cb is not None:
            if cb.get("from", {}).get("id") not in self.admin_ids:
                return
            data = cb.get("data", "")
            if data.startswith("pnl:"):
                period = data.split(":", 1)[1]
                if period in _PERIODS:
                    self._edit(cb["message"]["message_id"], self._pnl_text(period))
            self._answer(cb["id"])
            return

        msg = upd.get("message")
        if not msg:
            return
        if msg.get("from", {}).get("id") not in self.admin_ids:
            return
        parts = (msg.get("text") or "").strip().split()
        if not parts:
            return
        cmd = parts[0].split("@")[0].lstrip("/")   # tolerate /cmd@botname
        arg = parts[1] if len(parts) > 1 else None

        if cmd == "positions":
            self._send(format_positions(
                positions_report(self.accounts, self.agent_ids, self.price_fn)))
        elif cmd == "status":
            self._send(format_status(status_report(
                self.accounts, self.journal, self.freezes, self.agent_ids, self.price_fn)))
        elif cmd == "trades":
            self._send(format_trades(trades_report(self.journal, self.agent_ids)))
        elif cmd == "pnl":
            if arg in _PERIODS:
                self._send(self._pnl_text(arg))
            else:
                self._send("Выбери период:", reply_markup=_PNL_BUTTONS)
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_bot.py -v`
Expected: PASS (7 тестов)

- [ ] **Step 5: Commit**

```bash
git add src/trading/bot.py tests/test_bot.py
git commit -m "feat: Telegram command dispatcher (positions/pnl/status/trades + admin gate)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `bot.py` — polling-цикл с паузой по локу

**Files:**
- Modify: `src/trading/bot.py`
- Test: `tests/test_bot.py` (добавить тесты)

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_bot.py`:

```python
def test_poll_once_skips_when_run_lock_active(bot, monkeypatch):
    b, client = bot
    b.run_lock.acquire(now_iso="2026-06-13T13:30:00Z")
    polled = b.poll_once(offset=None, now_iso="2026-06-13T13:30:10Z")
    assert polled is None                  # не опрашивали getUpdates
    assert client.sent == []


def test_poll_once_processes_updates_when_unlocked(bot):
    b, client = bot

    def fake_get(url, params=None):
        return FakeClient._Resp({"result": [_message("/status")]})

    b.client.get = fake_get
    new_offset = b.poll_once(offset=None, now_iso="2026-06-13T13:30:10Z")
    assert client.sent                     # /status обработан
    assert new_offset == 2                 # update_id (1) + 1
```

> `_message` и `FakeClient` уже определены в файле из Task 9.

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_bot.py::test_poll_once_skips_when_run_lock_active -v`
Expected: FAIL с `AttributeError: 'Bot' object has no attribute 'poll_once'`

- [ ] **Step 3: Добавить `poll_once` и `run_forever`**

В `src/trading/bot.py` добавить импорты вверху:

```python
import time
```

И методы в класс `Bot`:

```python
    def poll_once(self, offset, now_iso=None):
        """One polling step. Returns the next offset, or None if it deferred to the
        running daily cycle (lock active)."""
        if self.run_lock.is_active(now_iso=now_iso):
            return None
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        updates = self.client.get(f"{self.base}/getUpdates", params=params).json()
        for upd in updates.get("result", []):
            offset = upd["update_id"] + 1
            self.handle_update(upd)
        return offset

    def run_forever(self) -> None:
        self.set_my_commands()
        offset = None
        while True:
            next_offset = self.poll_once(offset)
            if next_offset is None:
                time.sleep(3)        # daily cycle owns Telegram right now
                continue
            offset = next_offset
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_bot.py -v`
Expected: PASS (все тесты файла)

- [ ] **Step 5: Commit**

```bash
git add src/trading/bot.py tests/test_bot.py
git commit -m "feat: bot polling loop that pauses while a daily run holds the lock

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Точка входа `python -m trading.bot` и проводка в `run.py`

**Files:**
- Modify: `src/trading/bot.py` (добавить `main()` и `__main__`)
- Modify: `src/trading/run.py` (создать `RunLock`, передать в `run_daily`)

- [ ] **Step 1: Добавить `main()` в `bot.py`**

В конец `src/trading/bot.py` добавить:

```python
def build_bot():
    """Assemble the bot from the environment (mirrors run.py wiring)."""
    import os

    import httpx

    from trading.config import load_profiles
    from trading.data.briefing import load_universe
    from trading.data.yfinance_source import YFinanceSource
    from trading.persistence.accounts import AccountRepository
    from trading.persistence.db import connect
    from trading.persistence.freezes import FreezeStore
    from trading.persistence.journal import JournalRepository
    from trading.persistence.runlock import RunLock
    from trading.persistence.schema import init_db
    from trading.reporting.telegram import resolve_admin_ids

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    db_path = os.environ.get("DB_PATH", "data/trading.db")

    conn = connect(db_path)
    conn.execute("PRAGMA busy_timeout = 5000")    # tolerate the daily run's brief writes
    init_db(conn)

    profiles = load_profiles("config/profiles.toml")
    universe = load_universe("config/universe.toml")
    source = YFinanceSource()
    # value the union of universe + held symbols; latest_price covers held ones too
    price_fn = lambda s: source.latest_price(s)   # noqa: E731

    return Bot(
        client=httpx.Client(timeout=30.0),
        base=f"https://api.telegram.org/bot{token}",
        accounts=AccountRepository(conn),
        journal=JournalRepository(conn),
        freezes=FreezeStore(conn),
        run_lock=RunLock(conn),
        agent_ids=list(profiles.keys()),
        price_fn=price_fn,
        chat_id=chat_id,
        admin_ids=resolve_admin_ids(chat_id),
    )


def main() -> None:
    build_bot().run_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Проверить, что модуль импортируется без ошибок**

Run: `uv run python -c "import trading.bot; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Провязать `RunLock` в `run.py`**

В `src/trading/run.py`, в `build_components()`, после создания репозиториев (строка с `accounts, journal, freezes = ...`) добавить:

```python
    from trading.persistence.runlock import RunLock
    run_lock = RunLock(conn)
```

И добавить `run_lock=run_lock` в возвращаемый `dict(...)` (рядом с `freezes=freezes`):

```python
                freezes=freezes, run_lock=run_lock, universe=universe, confirm=confirm,
```

- [ ] **Step 4: Прогнать весь тест-сьют**

Run: `uv run pytest -q`
Expected: PASS (все тесты, включая `test_daily.py` — `run_daily` теперь получает `run_lock` из компонентов и работает с локом сквозным образом)

> Если `test_daily.py` собирает компоненты не через `build_components`, а вручную — он не передаёт `run_lock`, и благодаря дефолту `run_lock=None` всё равно проходит. Проверять отдельно не нужно.

- [ ] **Step 5: Ручной smoke-тест демона (опционально, требует токена)**

```bash
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... DB_PATH=data/trading.db \
  uv run python -m trading.bot
```
Ожидание: в Telegram у бота появляется меню «/» с командами; `/status`, `/positions`, `/pnl` (с кнопками периода), `/trades` отвечают. `Ctrl-C` останавливает.

- [ ] **Step 6: Commit**

```bash
git add src/trading/bot.py src/trading/run.py
git commit -m "feat: python -m trading.bot entrypoint and run_lock wiring in run.py

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Финальная проверка

- [ ] **Весь сьют зелёный:** `uv run pytest -q`
- [ ] **Демон импортируется:** `uv run python -c "import trading.bot"`
- [ ] Обновить `README`/`run.py` docstring при необходимости: как запускать демон (env-переменные те же, что у `run.py`).

---

## Соответствие спеку (self-review)

| Требование спека | Задача |
|---|---|
| Отдельный демон с polling | 9, 10, 11 |
| Команды `/positions`, `/pnl` (день/неделя/месяц/всё), `/status`, `/trades` | 9 |
| Нативное меню `setMyCommands` | 10 (`set_my_commands` в `run_forever`), 9 (`COMMANDS`) |
| Inline-кнопки периода для `/pnl` | 9 |
| Портфель + разбивка по агентам | 3, 4, 5 (queries) + 6 (format) |
| Кооперативная пауза getUpdates (лок в БД) | 1, 2, 10 |
| Защита от зависшего лока (15 мин) | 1 (`stale_after_s`) |
| Часть A: яснее сообщения (умеренно) | 7 |
| Игнор не-админов | 9 (`admin_ids`), 8 (`resolve_admin_ids`) |
| Тестируемое разделение compute/format/transport | 3-5 / 6-7 / 9-10 |
| Известное ограничение (команды во время прогона) | задокументировано в спеке; лок гарантирует, что демон не крадёт callback подтверждения |
