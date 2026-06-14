from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from trading.analysis.round_trips import RoundTrip
from trading.data.bars import MarketDataSource
from trading.data.indicators import pct_change, rsi, sma
from trading.domain import AgentState


@dataclass(frozen=True)
class SymbolBrief:
    symbol: str
    price: float
    sma20: float | None
    sma50: float | None
    rsi14: float | None
    return_5d: float | None
    held_quantity: int          # 0 if not held
    held_avg_price: float | None


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


@dataclass(frozen=True)
class Briefing:
    agent_id: str
    as_of_date: str
    cash: float
    equity: float
    symbols: list[SymbolBrief]
    memory: "Memory | None" = None
    news: dict = field(default_factory=dict)


def load_universe(path: str | Path) -> list[str]:
    with open(path, "rb") as f:
        return tomllib.load(f)["symbols"]


def build_briefing(
    state: AgentState,
    universe: list[str],
    source: MarketDataSource,
    as_of_date: str,
    lookback_days: int = 60,
    journal=None,
    news_source=None,
) -> Briefing:
    """Assemble the agent-facing snapshot: cash/equity + per-symbol price & indicators.

    Covers the union of the universe and currently-held symbols (deduped, sorted).
    """
    held = {p.symbol: p for p in state.positions}
    symbols = sorted(set(universe) | set(held))

    briefs: list[SymbolBrief] = []
    prices: dict[str, float] = {}
    for symbol in symbols:
        closes = [b.close for b in source.history(symbol, days=lookback_days,
                                                  as_of_date=as_of_date)]
        price = closes[-1]
        prices[symbol] = price
        position = held.get(symbol)
        briefs.append(SymbolBrief(
            symbol=symbol,
            price=price,
            sma20=sma(closes, 20),
            sma50=sma(closes, 50),
            rsi14=rsi(closes, 14),
            return_5d=pct_change(closes, 5),
            held_quantity=position.quantity if position else 0,
            held_avg_price=position.avg_price if position else None,
        ))

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
