# Self-Aware Briefing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the agent briefing with its own trade history (realized/unrealized P&L + original rationales, win-rate) and recent per-symbol news, so Claude makes better-grounded decisions.

**Architecture:** Both enrichments plug into a single point — `build_briefing`. A pure `round_trips` function reconstructs closed trades from the journal; `analysis/memory.py` assembles the memory block; `data/news.py` fetches headlines via yfinance with graceful degradation. The prompt renders both. Guardrails, validation panel, and execution are untouched. Memory is point-in-time (backtestable); news is current-only (off in backtest).

**Tech Stack:** Python 3.13, dataclasses, sqlite3, pydantic (existing), yfinance (existing), pytest, uv.

---

## File Structure

- Create: `src/trading/analysis/round_trips.py` — pure FIFO matching of fills → closed round-trips with realized P&L + rationale.
- Create: `src/trading/analysis/memory.py` — assembles the `Memory` block (open positions, recent closed, stats) from the journal.
- Create: `src/trading/data/news.py` — `Headline`, `NewsSource` protocol, `YFinanceNews`, `FakeNews`, `collect_news`.
- Modify: `src/trading/data/briefing.py` — add `Memory`/`OpenPositionMemory`/`SelfStats` dataclasses, `memory`/`news` fields on `Briefing`, and optional `journal`/`news_source` params on `build_briefing`.
- Modify: `src/trading/agent/prompts.py` — render memory + news; add learning/news instructions to the system prompt.
- Modify: `src/trading/orchestrator/cycle.py` — thread `journal` + `news_source` into `build_briefing`.
- Modify: `src/trading/orchestrator/daily.py` — accept and pass `news_source` to `run_cycle`.
- Modify: `src/trading/run.py` — build the news source from the `NEWS` env var and include it in the components dict.
- Create: `tests/test_round_trips.py`, `tests/test_memory.py`, `tests/test_news.py`.
- Modify: `tests/test_agent_prompts.py`, `tests/test_briefing.py`.

---

## Task 1: Round-trip P&L (pure)

**Files:**
- Create: `src/trading/analysis/round_trips.py`
- Test: `tests/test_round_trips.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_round_trips.py
from trading.analysis.round_trips import RoundTrip, compute_round_trips


def fill(ts, symbol, intent, quantity, price, decision_id=None):
    return {"ts": ts, "symbol": symbol, "intent": intent,
            "quantity": quantity, "price": price, "decision_id": decision_id}


def test_long_round_trip_profit_and_rationale():
    fills = [
        fill("2026-06-01T13:30:00Z", "AAPL", "open_long", 10, 100.0, decision_id=1),
        fill("2026-06-05T13:30:00Z", "AAPL", "close_long", 10, 110.0),
    ]
    trips = compute_round_trips(fills, {1: "momentum above sma20"})
    assert len(trips) == 1
    t = trips[0]
    assert t == RoundTrip(symbol="AAPL", quantity=10, entry_date="2026-06-01",
                          entry_price=100.0, exit_date="2026-06-05", exit_price=110.0,
                          realized_pnl=100.0, realized_pct=0.10,
                          rationale="momentum above sma20")


def test_short_round_trip_profit_when_price_falls():
    fills = [
        fill("2026-06-01T13:30:00Z", "TSLA", "open_short", 5, 200.0, decision_id=7),
        fill("2026-06-03T13:30:00Z", "TSLA", "close_short", 5, 180.0),
    ]
    trips = compute_round_trips(fills, {7: "overbought"})
    assert trips[0].realized_pnl == 100.0          # (200 - 180) * 5
    assert trips[0].realized_pct == 0.10


def test_partial_close_fifo_matching():
    fills = [
        fill("2026-06-01T13:30:00Z", "AAPL", "open_long", 10, 100.0, decision_id=1),
        fill("2026-06-02T13:30:00Z", "AAPL", "open_long", 10, 120.0, decision_id=2),
        fill("2026-06-05T13:30:00Z", "AAPL", "close_long", 15, 130.0),
    ]
    trips = compute_round_trips(fills, {1: "first", 2: "second"})
    # FIFO: 10 @100 fully closed, then 5 @120 closed
    assert len(trips) == 2
    assert (trips[0].quantity, trips[0].entry_price, trips[0].rationale) == (10, 100.0, "first")
    assert (trips[1].quantity, trips[1].entry_price, trips[1].rationale) == (5, 120.0, "second")
    assert trips[1].realized_pnl == 50.0           # (130 - 120) * 5


def test_open_position_produces_no_round_trip():
    fills = [fill("2026-06-01T13:30:00Z", "AAPL", "open_long", 10, 100.0, decision_id=1)]
    assert compute_round_trips(fills, {1: "x"}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_round_trips.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.analysis.round_trips'`

- [ ] **Step 3: Write the implementation**

```python
# src/trading/analysis/round_trips.py
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

_OPENING = {"open_long": "long", "open_short": "short"}
_CLOSING = {"close_long": "long", "close_short": "short"}


@dataclass(frozen=True)
class RoundTrip:
    """One closed trade: an opening fill matched (FIFO) against a closing fill."""
    symbol: str
    quantity: int
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    realized_pnl: float
    realized_pct: float
    rationale: str


def compute_round_trips(fills, rationale_by_decision) -> list[RoundTrip]:
    """Reconstruct closed round-trips from fills in chronological order.

    `fills`: iterable of mappings with keys ts, symbol, intent, quantity, price,
    decision_id — the shape JournalRepository.fills_for returns (already time-ordered).
    `rationale_by_decision`: {decision_id: rationale} for opening fills.
    Long and short are matched independently per symbol; partial closes use FIFO.
    """
    lots: dict[tuple[str, str], deque] = defaultdict(deque)
    out: list[RoundTrip] = []

    for f in fills:
        intent = f["intent"]
        symbol = f["symbol"]
        date = f["ts"][:10]

        if intent in _OPENING:
            rationale = rationale_by_decision.get(f["decision_id"], "")
            lots[(symbol, _OPENING[intent])].append(
                [f["quantity"], f["price"], date, rationale])
            continue

        if intent not in _CLOSING:
            continue

        side = _CLOSING[intent]
        queue = lots[(symbol, side)]
        remaining = f["quantity"]
        exit_price = f["price"]
        while remaining > 0 and queue:
            lot = queue[0]
            matched = min(remaining, lot[0])
            entry_price = lot[1]
            pnl = ((exit_price - entry_price) if side == "long"
                   else (entry_price - exit_price)) * matched
            pct = pnl / (entry_price * matched) if entry_price else 0.0
            out.append(RoundTrip(
                symbol=symbol, quantity=matched, entry_date=lot[2],
                entry_price=entry_price, exit_date=date, exit_price=exit_price,
                realized_pnl=round(pnl, 2), realized_pct=round(pct, 4),
                rationale=lot[3]))
            lot[0] -= matched
            remaining -= matched
            if lot[0] == 0:
                queue.popleft()

    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_round_trips.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/analysis/round_trips.py tests/test_round_trips.py
git commit -m "feat: round-trip P&L reconstruction from fills (FIFO)"
```

---

## Task 2: Memory dataclasses + builder

**Files:**
- Modify: `src/trading/data/briefing.py` (add memory dataclasses + import)
- Create: `src/trading/analysis/memory.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Add the memory dataclasses and Briefing fields to briefing.py**

In `src/trading/data/briefing.py`, change the dataclass import line at the top so `field` is available:

```python
from dataclasses import dataclass, field
```

After the existing imports, add:

```python
from trading.analysis.round_trips import RoundTrip
```

Add these dataclasses after the `SymbolBrief` definition (before `Briefing`):

```python
@dataclass(frozen=True)
class OpenPositionMemory:
    symbol: str
    quantity: int
    avg_price: float
    rationale: str          # why this position was opened (latest opening decision)
    unrealized_pct: float


@dataclass(frozen=True)
class SelfStats:
    closed_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    total_realized_pnl: float


@dataclass(frozen=True)
class Memory:
    open_positions: list[OpenPositionMemory]
    recent_closed: list[RoundTrip]
    stats: SelfStats | None
```

Add the two new fields to the `Briefing` dataclass (after `symbols`), so the prompt
renderer in Task 4 can read them and they default to empty everywhere else:

```python
@dataclass(frozen=True)
class Briefing:
    agent_id: str
    as_of_date: str
    cash: float
    equity: float
    symbols: list[SymbolBrief]
    memory: "Memory | None" = None
    news: dict = field(default_factory=dict)
```

`build_briefing` still constructs `Briefing(...)` without these args for now, so the
defaults apply — existing behavior is unchanged until Task 5 wires them.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_memory.py
import pytest
from trading.analysis.memory import build_memory
from trading.domain import Intent, Outcome, Position, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db


@pytest.fixture
def journal(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return JournalRepository(conn)


def _open_and_close(journal, symbol, entry, exit_price, rationale):
    prop = TradeProposal("moderate", symbol, Intent.OPEN_LONG, 10, entry, entry * 0.9, rationale)
    did = journal.record_decision("2026-06-01T13:30:00Z", prop,
                                  GuardrailDecision(Outcome.APPROVED_AUTO, 10, []))
    journal.record_fill("2026-06-01T13:30:00Z", "moderate", symbol, Intent.OPEN_LONG,
                        10, entry, did)
    journal.record_fill("2026-06-05T13:30:00Z", "moderate", symbol, Intent.CLOSE_LONG,
                        10, exit_price, None)


def test_build_memory_empty_on_cold_start(journal):
    mem = build_memory(journal, "moderate", positions=[], prices={})
    assert mem.open_positions == []
    assert mem.recent_closed == []
    assert mem.stats is None


def test_build_memory_reports_closed_trades_and_stats(journal):
    _open_and_close(journal, "AAPL", 100.0, 110.0, "winner")   # +100
    _open_and_close(journal, "MSFT", 200.0, 180.0, "loser")    # -200
    mem = build_memory(journal, "moderate", positions=[], prices={})
    assert mem.stats.closed_trades == 2
    assert mem.stats.win_rate == 0.5
    assert mem.stats.total_realized_pnl == -100.0
    assert {t.symbol for t in mem.recent_closed} == {"AAPL", "MSFT"}


def test_build_memory_open_position_carries_rationale_and_unrealized(journal):
    prop = TradeProposal("moderate", "NVDA", Intent.OPEN_LONG, 2, 800.0, 720.0, "breakout")
    did = journal.record_decision("2026-06-01T13:30:00Z", prop,
                                  GuardrailDecision(Outcome.APPROVED_AUTO, 2, []))
    journal.record_fill("2026-06-01T13:30:00Z", "moderate", "NVDA", Intent.OPEN_LONG,
                        2, 800.0, did)
    positions = [Position("NVDA", 2, 800.0)]
    mem = build_memory(journal, "moderate", positions, prices={"NVDA": 900.0})
    assert len(mem.open_positions) == 1
    op = mem.open_positions[0]
    assert op.symbol == "NVDA"
    assert op.rationale == "breakout"
    assert op.unrealized_pct == pytest.approx(0.125)   # (900-800)/800
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.analysis.memory'`

- [ ] **Step 4: Write the implementation**

```python
# src/trading/analysis/memory.py
from __future__ import annotations

from trading.analysis.round_trips import compute_round_trips
from trading.data.briefing import Memory, OpenPositionMemory, SelfStats

_OPENING_INTENTS = ("open_long", "open_short")


def build_memory(journal, agent_id, positions, prices, recent_limit: int = 12) -> Memory:
    """Assemble the agent's self-memory from the journal.

    Returns empty/None fields on a cold start (no history) — identical to today's behavior.
    """
    decisions = journal.decisions_for(agent_id)
    rationale_by_decision = {d["id"]: d["rationale"] for d in decisions}
    trips = compute_round_trips(journal.fills_for(agent_id), rationale_by_decision)

    open_positions = [
        OpenPositionMemory(
            symbol=p.symbol, quantity=p.quantity, avg_price=p.avg_price,
            rationale=_latest_open_rationale(decisions, p.symbol),
            unrealized_pct=_unrealized_pct(p, prices.get(p.symbol, p.avg_price)),
        )
        for p in positions
    ]
    return Memory(open_positions=open_positions,
                  recent_closed=trips[-recent_limit:],
                  stats=_stats(trips))


def _latest_open_rationale(decisions, symbol: str) -> str:
    for d in reversed(decisions):
        if d["symbol"] == symbol and d["intent"] in _OPENING_INTENTS:
            return d["rationale"]
    return ""


def _unrealized_pct(position, price: float) -> float:
    if position.avg_price == 0:
        return 0.0
    pct = (price - position.avg_price) / position.avg_price
    return -pct if position.quantity < 0 else pct      # short profits when price falls


def _stats(trips) -> SelfStats | None:
    if not trips:
        return None
    wins = [t for t in trips if t.realized_pnl > 0]
    losses = [t for t in trips if t.realized_pnl < 0]
    return SelfStats(
        closed_trades=len(trips),
        win_rate=len(wins) / len(trips),
        avg_win=round(sum(t.realized_pnl for t in wins) / len(wins), 2) if wins else 0.0,
        avg_loss=round(sum(t.realized_pnl for t in losses) / len(losses), 2) if losses else 0.0,
        total_realized_pnl=round(sum(t.realized_pnl for t in trips), 2),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_memory.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/trading/data/briefing.py src/trading/analysis/memory.py tests/test_memory.py
git commit -m "feat: build self-memory block (open positions, closed trades, stats)"
```

---

## Task 3: News source

**Files:**
- Create: `src/trading/data/news.py`
- Test: `tests/test_news.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_news.py
from trading.data.news import (FakeNews, Headline, _parse_item, collect_news)


def test_fake_news_returns_configured_headlines():
    src = FakeNews({"AAPL": [Headline("AAPL", "iPhone day", "Reuters", "2026-06-13")]})
    assert src.headlines("AAPL")[0].title == "iPhone day"
    assert src.headlines("MSFT") == []


def test_collect_news_swallows_per_symbol_errors():
    class Boom:
        def headlines(self, symbol, as_of_date=None):
            if symbol == "BAD":
                raise RuntimeError("network down")
            return [Headline(symbol, "ok", "X", "2026-06-13")]

    got = collect_news(Boom(), ["AAPL", "BAD"], as_of_date="2026-06-14")
    assert [h.title for h in got["AAPL"]] == ["ok"]
    assert got["BAD"] == []                       # error degraded to empty, no raise


def test_parse_item_handles_new_yfinance_schema():
    item = {"content": {"title": "Earnings beat",
                        "provider": {"displayName": "Bloomberg"},
                        "pubDate": "2026-06-13T20:00:00Z"}}
    h = _parse_item("AAPL", item)
    assert h == Headline("AAPL", "Earnings beat", "Bloomberg", "2026-06-13")


def test_parse_item_handles_legacy_schema():
    item = {"title": "Old style", "publisher": "WSJ",
            "providerPublishTime": 1781726400}   # 2026-06-17 UTC
    h = _parse_item("AAPL", item)
    assert h.title == "Old style" and h.publisher == "WSJ"
    assert h.published_date == "2026-06-17"


def test_parse_item_returns_none_without_title():
    assert _parse_item("AAPL", {"content": {"provider": {"displayName": "X"}}}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_news.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.data.news'`

- [ ] **Step 3: Write the implementation**

```python
# src/trading/data/news.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class Headline:
    symbol: str
    title: str
    publisher: str
    published_date: str          # YYYY-MM-DD, or "" if unknown


class NewsSource(Protocol):
    def headlines(self, symbol: str, as_of_date: str | None = None) -> list[Headline]: ...


class FakeNews:
    """Deterministic news for tests and offline runs. Satisfies NewsSource."""

    def __init__(self, by_symbol: dict[str, list[Headline]] | None = None) -> None:
        self._by_symbol = by_symbol or {}

    def headlines(self, symbol: str, as_of_date: str | None = None) -> list[Headline]:
        return list(self._by_symbol.get(symbol, []))


class YFinanceNews:
    """Recent headlines via yfinance. Never raises — failures degrade to []."""

    def __init__(self, max_items: int = 5) -> None:
        self.max_items = max_items

    def headlines(self, symbol: str, as_of_date: str | None = None) -> list[Headline]:
        try:
            import yfinance as yf
            raw = yf.Ticker(symbol).news or []
        except Exception:
            return []
        out: list[Headline] = []
        for item in raw[: self.max_items]:
            parsed = _parse_item(symbol, item)
            if parsed is not None:
                out.append(parsed)
        return out


def _parse_item(symbol: str, item: dict) -> Headline | None:
    """Parse one yfinance news item, tolerating both the new (nested 'content')
    and legacy (flat) schemas. Returns None when there is no usable title."""
    content = item.get("content", item)
    title = content.get("title") or item.get("title") or ""
    if not title:
        return None

    provider = content.get("provider")
    publisher = (provider.get("displayName", "") if isinstance(provider, dict)
                 else "") or item.get("publisher", "")

    date = ""
    if content.get("pubDate"):
        date = str(content["pubDate"])[:10]
    elif item.get("providerPublishTime"):
        date = datetime.fromtimestamp(
            item["providerPublishTime"], tz=timezone.utc).strftime("%Y-%m-%d")

    return Headline(symbol=symbol, title=title, publisher=publisher, published_date=date)


def collect_news(news_source, symbols, as_of_date: str | None = None) -> dict[str, list[Headline]]:
    """Headlines for each symbol. A per-symbol failure degrades to [] — never raises,
    so a flaky news provider can never abort a trading cycle."""
    out: dict[str, list[Headline]] = {}
    for symbol in symbols:
        try:
            out[symbol] = news_source.headlines(symbol, as_of_date=as_of_date)
        except Exception:
            out[symbol] = []
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_news.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/trading/data/news.py tests/test_news.py
git commit -m "feat: news source (yfinance + fake) with graceful degradation"
```

---

## Task 4: Render memory + news in the prompt

**Files:**
- Modify: `src/trading/agent/prompts.py`
- Test: `tests/test_agent_prompts.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_prompts.py`:

```python
from trading.analysis.round_trips import RoundTrip
from trading.data.briefing import Memory, OpenPositionMemory, SelfStats
from trading.data.news import Headline


def briefing_with_memory_and_news():
    base = briefing()
    mem = Memory(
        open_positions=[OpenPositionMemory("AAPL", 5, 120.0, "bought the breakout", -0.08)],
        recent_closed=[RoundTrip("MSFT", 3, "2026-06-01", 400.0, "2026-06-04", 380.0,
                                 -60.0, -0.05, "chased the rally")],
        stats=SelfStats(closed_trades=4, win_rate=0.25, avg_win=40.0,
                        avg_loss=-50.0, total_realized_pnl=-30.0),
    )
    news = {"AAPL": [Headline("AAPL", "Antitrust probe opens", "Reuters", "2026-06-14")]}
    return Briefing(agent_id=base.agent_id, as_of_date=base.as_of_date, cash=base.cash,
                    equity=base.equity, symbols=base.symbols, memory=mem, news=news)


def test_user_prompt_renders_memory_block():
    u = build_user_prompt(briefing_with_memory_and_news())
    assert "bought the breakout" in u           # open position rationale
    assert "chased the rally" in u              # closed trade rationale
    assert "25%" in u                           # win rate


def test_user_prompt_renders_news_block():
    u = build_user_prompt(briefing_with_memory_and_news())
    assert "Antitrust probe opens" in u
    assert "Reuters" in u


def test_user_prompt_omits_empty_memory_and_news():
    u = build_user_prompt(briefing())           # no memory, no news
    assert "track record" not in u.lower()
    assert "recent news" not in u.lower()


def test_system_prompt_instructs_learning_and_news_discipline():
    p = build_system_prompt(make_profile())
    assert "track record" in p.lower() or "past trade" in p.lower()
    assert "invent" in p.lower() or "not listed" in p.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_prompts.py -v`
Expected: FAIL — assorted AssertionErrors (memory/news strings absent)

- [ ] **Step 3: Add the system-prompt instructions**

In `src/trading/agent/prompts.py`, inside `build_system_prompt`, add one line to the returned string immediately after the existing rationale-in-Russian line (`"- Write the rationale field in Russian (the owner reads Russian).\n"`):

```python
        f"- Below the symbols you may see your own track record (past trades, their P&L, "
        f"and the rationales you gave) and recent news. Learn from losing trades — do not "
        f"repeat a thesis that has lost money. Weigh the news, but NEVER act on or invent a "
        f"headline that is not explicitly listed.\n"
```

- [ ] **Step 4: Render memory + news in the user prompt**

In `src/trading/agent/prompts.py`, replace the tail of `build_user_prompt` (the final two `lines.append(...)` before `return`) with calls to two new helpers, then add the helpers. The function tail becomes:

```python
    lines.extend(_render_memory(briefing.memory))
    lines.extend(_render_news(briefing.news))
    lines.append("")
    lines.append("Propose trades for today as structured data, or an empty list.")
    return "\n".join(lines)


def _render_memory(memory) -> list[str]:
    if memory is None or not (memory.open_positions or memory.recent_closed or memory.stats):
        return []
    lines = ["", "Your track record (learn from it):"]
    if memory.stats is not None:
        s = memory.stats
        lines.append(
            f"  stats: closed={s.closed_trades} win_rate={s.win_rate:.0%} "
            f"avg_win={s.avg_win:+.2f} avg_loss={s.avg_loss:+.2f} "
            f"realized_pnl={s.total_realized_pnl:+.2f}")
    for op in memory.open_positions:
        lines.append(
            f"  OPEN {op.symbol} {op.quantity} @ {op.avg_price:.2f} "
            f"({op.unrealized_pct:+.1%}) — {op.rationale}")
    for t in memory.recent_closed:
        lines.append(
            f"  CLOSED {t.symbol} {t.quantity} {t.entry_price:.2f}->{t.exit_price:.2f} "
            f"({t.realized_pct:+.1%}, {t.realized_pnl:+.2f}) — {t.rationale}")
    return lines


def _render_news(news) -> list[str]:
    if not news:
        return []
    lines = ["", "Recent news (consider, but do not invent any not listed):"]
    for symbol, items in news.items():
        for h in items:
            lines.append(f"  [{symbol}] {h.published_date} {h.title} ({h.publisher})")
    if len(lines) == 2:        # header only, every symbol had no headlines
        return []
    return lines
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_prompts.py -v`
Expected: PASS (all, including the pre-existing tests)

- [ ] **Step 6: Commit**

```bash
git add src/trading/agent/prompts.py tests/test_agent_prompts.py
git commit -m "feat: render self-memory + news blocks in the agent prompt"
```

---

## Task 5: Wire memory + news into build_briefing

**Files:**
- Modify: `src/trading/data/briefing.py`
- Test: `tests/test_briefing.py`

(The `memory`/`news` fields on `Briefing` were already added in Task 2. This task wires
`build_briefing` to populate them.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_briefing.py`:

```python
from trading.data.news import FakeNews, Headline
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db


def test_build_briefing_populates_news_when_source_given():
    source = FakeMarketDataSource({"AAPL": ramp(100, 60)})
    state = AgentState(agent_id="moderate", cash=2000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    news_source = FakeNews({"AAPL": [Headline("AAPL", "Big news", "Reuters", "2026-06-13")]})
    briefing = build_briefing(state, universe=["AAPL"], source=source,
                              as_of_date="2026-12-31", news_source=news_source)
    assert briefing.news["AAPL"][0].title == "Big news"


def test_build_briefing_populates_memory_when_journal_given():
    conn = connect(":memory:")
    init_db(conn)
    journal = JournalRepository(conn)
    source = FakeMarketDataSource({"AAPL": ramp(100, 60)})
    state = AgentState(agent_id="moderate", cash=2000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    briefing = build_briefing(state, universe=["AAPL"], source=source,
                              as_of_date="2026-12-31", journal=journal)
    assert briefing.memory is not None
    assert briefing.memory.recent_closed == []      # cold start


def test_build_briefing_defaults_have_no_memory_or_news():
    source = FakeMarketDataSource({"AAPL": ramp(100, 60)})
    state = AgentState(agent_id="moderate", cash=2000.0, positions=[],
                       peak_equity=5000.0, equity_day_start=5000.0)
    briefing = build_briefing(state, universe=["AAPL"], source=source, as_of_date="2026-12-31")
    assert briefing.memory is None
    assert briefing.news == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_briefing.py -v`
Expected: FAIL — `TypeError: build_briefing() got an unexpected keyword argument 'news_source'`
(`test_build_briefing_defaults_have_no_memory_or_news` already passes — defaults apply.)

- [ ] **Step 3: Thread `journal` and `news_source` through `build_briefing`**

In `src/trading/data/briefing.py`, change the `build_briefing` signature and tail. New signature:

```python
def build_briefing(
    state: AgentState,
    universe: list[str],
    source: MarketDataSource,
    as_of_date: str,
    lookback_days: int = 60,
    journal=None,
    news_source=None,
) -> Briefing:
```

Replace the final `return Briefing(...)` with:

```python
    memory = None
    if journal is not None:
        from trading.analysis.memory import build_memory
        memory = build_memory(journal, state.agent_id, state.positions, prices)

    news: dict = {}
    if news_source is not None:
        from trading.data.news import collect_news
        news = collect_news(news_source, symbols, as_of_date)

    return Briefing(
        agent_id=state.agent_id,
        as_of_date=as_of_date,
        cash=state.cash,
        equity=state.equity(prices),
        symbols=briefs,
        memory=memory,
        news=news,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_briefing.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add src/trading/data/briefing.py tests/test_briefing.py
git commit -m "feat: build_briefing assembles memory (journal) and news (source)"
```

---

## Task 6: Wire through the cycle, daily run, and run.py

**Files:**
- Modify: `src/trading/orchestrator/cycle.py`
- Modify: `src/trading/orchestrator/daily.py`
- Modify: `src/trading/run.py`
- Test: `tests/test_cycle.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cycle.py`:

```python
def test_run_cycle_passes_news_into_briefing(tmp_path):
    """A captured-briefing strategy proves news reaches the prompt-facing briefing."""
    from trading.data.news import FakeNews, Headline

    captured = {}

    class CapturingStrategy:
        def propose(self, briefing, profile):
            captured["briefing"] = briefing
            return []

    from trading.broker.fake import FakeBroker
    from trading.config import load_profiles
    from trading.data.fake_source import FakeMarketDataSource
    from trading.data.bars import Bar
    from trading.orchestrator.cycle import run_cycle
    from trading.persistence.accounts import AccountRepository
    from trading.persistence.db import connect
    from trading.persistence.journal import JournalRepository
    from trading.persistence.schema import init_db

    bars = [Bar(f"2026-06-{i+1:02d}", 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1000)
            for i in range(60)]
    source = FakeMarketDataSource({"AAPL": bars})
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    accounts, journal = AccountRepository(conn), JournalRepository(conn)
    profile = load_profiles("config/profiles.toml")["moderate"]
    broker = FakeBroker(cash=profile.budget)
    broker.set_price("AAPL", 159.0)
    news = FakeNews({"AAPL": [Headline("AAPL", "News!", "Reuters", "2026-06-13")]})

    run_cycle(agent_id="moderate", profile=profile, broker=broker, source=source,
              accounts=accounts, journal=journal, strategy=CapturingStrategy(),
              universe=["AAPL"], as_of_date="2026-12-31", ts="2026-12-31T13:30:00Z",
              confirm=lambda p, d: True, news_source=news)

    assert captured["briefing"].news["AAPL"][0].title == "News!"
    assert captured["briefing"].memory is not None     # journal always wired now
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cycle.py::test_run_cycle_passes_news_into_briefing -v`
Expected: FAIL — `TypeError: run_cycle() got an unexpected keyword argument 'news_source'`

- [ ] **Step 3: Thread `news_source` through `run_cycle`**

In `src/trading/orchestrator/cycle.py`, add `news_source=None` to the `run_cycle` signature (after `notifier=None`), and change the briefing line:

```python
    briefing = build_briefing(state, universe, source, as_of_date,
                              journal=journal, news_source=news_source)
```

- [ ] **Step 4: Thread `news_source` through `run_daily`**

In `src/trading/orchestrator/daily.py`:

Add `news_source=None` to the `run_daily` signature (after `run_lock=None`). Pass it into `_run_daily_body` — update both the call inside `run_daily`:

```python
        _run_daily_body(
            profiles, brokers, source, strategy, panel, notifier, accounts, journal,
            freezes, universe, as_of_date, ts, floor_fraction, confirm, news_source)
```

and the `_run_daily_body` signature (append `news_source` as the last parameter):

```python
def _run_daily_body(
    profiles, brokers, source, strategy, panel, notifier, accounts, journal,
    freezes, universe, as_of_date, ts, floor_fraction, confirm, news_source=None,
) -> None:
```

Pass it into the `run_cycle(...)` call inside `_run_daily_body` by adding the argument:

```python
            run_cycle(
                agent_id=name, profile=profile, broker=broker, source=source,
                accounts=accounts, journal=journal, strategy=strategy, universe=universe,
                as_of_date=as_of_date, ts=ts, confirm=confirm, panel=panel, notifier=notifier,
                news_source=news_source,
            )
```

- [ ] **Step 5: Build the news source from env in run.py**

In `src/trading/run.py`, add a helper and include `news_source` in the components dict.

Add this helper above `build_components`:

```python
def _news_source_for():
    """NEWS=yfinance (default) | fake. yfinance failures degrade to no news."""
    if os.environ.get("NEWS", "yfinance") == "fake":
        from trading.data.news import FakeNews
        return FakeNews()
    from trading.data.news import YFinanceNews
    return YFinanceNews()
```

In the `return dict(...)` of `build_components`, add the key:

```python
                freezes=freezes, run_lock=run_lock, universe=universe, confirm=confirm,
                news_source=_news_source_for(),
                floor_fraction=float(os.environ.get("FLOOR_FRACTION", "0.8")))
```

Add the `NEWS` line to the `Env:` docstring at the top of the module, after the `NOTIFIER` line:

```
  NEWS                    yfinance (default) | fake
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_cycle.py tests/test_daily.py -v`
Expected: PASS (new test passes; pre-existing daily/cycle tests still pass — they omit `news_source`, which defaults to None)

- [ ] **Step 7: Commit**

```bash
git add src/trading/orchestrator/cycle.py src/trading/orchestrator/daily.py src/trading/run.py tests/test_cycle.py
git commit -m "feat: thread news_source through cycle, daily run, and run wiring"
```

---

## Task 7: Backtest honesty — memory on, news off; full suite green

**Files:**
- Modify: `src/trading/orchestrator/simulate.py`
- Test: `tests/test_simulate.py`

The backtest runs `FakeStrategy` (no LLM) and must NOT use news (yfinance `.news` is current-only — feeding it during a historical replay would be look-ahead). Memory is point-in-time and stays available via the journal. This task makes the rule explicit and guards it with a test.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_simulate.py`:

```python
def test_simulation_does_not_attach_news():
    """Backtest must never surface news (yfinance .news has no point-in-time access)."""
    from trading.config import load_profiles
    from trading.data.briefing import load_universe
    from trading.orchestrator.simulate import run_simulation, synthetic_series, LOOKBACK
    from trading.persistence.accounts import AccountRepository
    from trading.persistence.db import connect
    from trading.persistence.journal import JournalRepository
    from trading.persistence.schema import init_db

    profiles = {"moderate": load_profiles("config/profiles.toml")["moderate"]}
    universe = load_universe("config/universe.toml")
    series = synthetic_series(universe, total_bars=LOOKBACK + 5 + 1)
    conn = connect(":memory:")
    init_db(conn)
    accounts, journal = AccountRepository(conn), JournalRepository(conn)

    captured = {"news_seen": False}
    import trading.orchestrator.simulate as sim
    real_build = sim.build_briefing

    def spy_build(*a, **kw):
        b = real_build(*a, **kw)
        if b.news:
            captured["news_seen"] = True
        return b

    sim.build_briefing = spy_build
    try:
        run_simulation(5, profiles, universe, series, accounts, journal)
    finally:
        sim.build_briefing = real_build
    assert captured["news_seen"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_simulate.py::test_simulation_does_not_attach_news -v`
Expected: FAIL — `AttributeError: module 'trading.orchestrator.simulate' has no attribute 'build_briefing'` (simulate.py does not import it yet)

- [ ] **Step 3: Make memory explicit and news absent in simulate.py**

In `src/trading/orchestrator/simulate.py`, add the import so the spy can patch it and so memory is explicitly wired through `run_cycle` (which already receives `journal`):

```python
from trading.data.briefing import build_briefing, load_universe
```

(Replace the existing `from trading.data.briefing import load_universe` line.)

The `run_cycle(...)` call in `run_simulation` already passes `journal` (memory on) and omits `news_source` (news off, defaults to None). Add an explicit comment above that call to lock the intent:

```python
            # Memory is point-in-time (journal) so it stays on; news is current-only and
            # would be look-ahead in a historical replay, so it is deliberately omitted.
            state = run_cycle(
                agent_id=name, profile=profile, broker=broker, source=source,
                accounts=accounts, journal=journal, strategy=FakeStrategy(),
                universe=universe, as_of_date=as_of, ts=f"{as_of}T13:30:00Z",
                confirm=lambda proposal, decision: True,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_simulate.py -v`
Expected: PASS (all)

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS (entire suite green — no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/trading/orchestrator/simulate.py tests/test_simulate.py
git commit -m "feat: keep news out of backtest (look-ahead); memory stays point-in-time"
```

---

## Notes on measuring the result

- The **memory** effect is backtestable, but only once a memory-consuming strategy exists. `FakeStrategy` ignores memory, so `simulate.py` is a plumbing/regression guard, not a strategy A/B. A real before/after comparison requires the Claude strategy (`STRATEGY=claude`), which costs API tokens and network — run it as an ops step (`uv run python -m trading.run` in `BROKER=fake` dry-run over several days), comparing the equity curve and `analysis/track_record.py` metrics against the pre-change baseline.
- The **news** effect is measurable only forward, in paper mode (`NEWS=yfinance`), for the same look-ahead reason. Disable with `NEWS=fake` to isolate the memory contribution.
- This plan deliberately leaves the reflection step (a second LLM "lessons" call — variant B) out of scope; it layers cleanly on top of the rendered memory block later.
