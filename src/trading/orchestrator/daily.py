from __future__ import annotations

from trading.config import RiskProfile
from trading.data.bars import MarketDataSource
from trading.domain import AgentState
from trading.guardrails import checks
from trading.orchestrator.cycle import run_cycle
from trading.orchestrator.strategy import Strategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.freezes import GLOBAL, FreezeStore
from trading.persistence.journal import JournalRepository
from trading.reporting.format import format_alert, format_digest, format_pnl, intent_label
from trading.reporting.notifier import Notifier, make_confirm
from trading.safety.reconcile import reconcile
from trading.safety.watchdog import Watchdog, flatten


def _prices_for(source: MarketDataSource, symbols,
                as_of_date: str | None = None) -> dict[str, float]:
    """Price the union of the universe and everything the broker holds, so the safety
    checks never KeyError on a symbol that was dropped from the universe while held."""
    return {s: source.latest_price(s, as_of_date=as_of_date) for s in symbols}


def _summary(journal: JournalRepository, agent_id: str, date: str):
    fills = [r for r in journal.fills_for(agent_id) if r["ts"].startswith(date)]
    executed = [f'{intent_label(r["intent"])} {r["quantity"]} {r["symbol"]} @ {r["price"]:g}'
                for r in fills]
    rejected = sum(1 for r in journal.decisions_for(agent_id)
                   if r["ts"].startswith(date) and r["outcome"] == "rejected")
    vetoed = sum(1 for r in journal.vetoes_for(agent_id) if r["ts"].startswith(date))
    return executed, rejected, vetoed


def run_daily(
    profiles: dict[str, RiskProfile],
    brokers: dict[str, object],
    source: MarketDataSource,
    strategy: Strategy,
    panel,
    notifier: Notifier,
    accounts: AccountRepository,
    journal: JournalRepository,
    freezes: FreezeStore,
    universe: list[str],
    as_of_date: str,
    ts: str,
    floor_fraction: float = 0.8,
    confirm=None,
) -> None:
    """Run the full pre-market cycle for every agent. The production keystone.

    Per agent: skip if frozen -> reconcile ledger vs broker -> run_cycle (panel +
    Telegram confirm) -> drawdown suspension -> watchdog (flatten + freeze on breach)
    -> digest. Each agent runs in isolation: a failure freezes that agent and alerts,
    but never aborts the rest of the run or disables their safety checks.
    """
    if confirm is None:
        confirm = make_confirm(notifier)

    pnl_start_total = 0.0
    pnl_end_total = 0.0

    for name, profile in profiles.items():
        if freezes.is_frozen(GLOBAL):
            notifier.notify(format_alert("frozen", f"GLOBAL halt active — {name} skipped"))
            continue
        if freezes.is_frozen(name):
            notifier.notify(format_alert("frozen", f"{name} skipped: {freezes.reason(name)}"))
            continue

        broker = brokers[name]
        try:
            prev = accounts.get_state(name)
            if prev is not None:
                rec = reconcile(prev, broker)
                if not rec.ok:
                    detail = "; ".join(rec.discrepancies)
                    freezes.freeze(name, detail, ts)
                    notifier.notify(format_alert("reconciliation", f"{name}: {detail}"))
                    continue

            run_cycle(
                agent_id=name, profile=profile, broker=broker, source=source,
                accounts=accounts, journal=journal, strategy=strategy, universe=universe,
                as_of_date=as_of_date, ts=ts, confirm=confirm, panel=panel, notifier=notifier,
            )

            post = accounts.get_state(name)
            prices = _prices_for(source, set(universe) | {p.symbol for p in broker.positions()},
                                 as_of_date=as_of_date)

            # Drawdown kill-switch: durable suspension pending manual review. Fires
            # earlier than the NAV watchdog and leaves positions in place.
            equity = post.equity(prices)
            if checks.drawdown_breached(equity, post.peak_equity, profile.max_drawdown_pct):
                freezes.freeze(
                    name, f"max drawdown: equity {equity:.0f} vs peak {post.peak_equity:.0f}", ts)
                notifier.notify(format_alert(
                    "drawdown", f"{name}: max drawdown breached — suspended pending review"))

            result = Watchdog(profile.budget, floor_fraction).check(broker, prices)
            if result.breached:
                flatten(broker, prices)
                accounts.save_state(AgentState(
                    name, broker.cash(), broker.positions(),
                    post.peak_equity, post.equity_day_start))
                freezes.freeze(name, f"NAV {result.nav:.0f} < floor {result.floor:.0f}", ts)
                notifier.notify(format_alert(
                    "watchdog",
                    f"{name}: NAV {result.nav:.0f} < floor {result.floor:.0f} — flattened & frozen"))

            executed, rejected, vetoed = _summary(journal, name, as_of_date)
            notifier.notify(format_digest(name, as_of_date, executed, rejected, vetoed))

            # Per-agent P&L (budget -> current equity), and accumulate the portfolio total.
            final_equity = post.equity(prices)
            notifier.notify(format_pnl(name, profile.budget, final_equity))
            pnl_start_total += profile.budget
            pnl_end_total += final_equity
        except Exception as exc:  # noqa: BLE001 — one agent's failure must not sink the rest
            freezes.freeze(name, f"run error: {exc}", ts)
            notifier.notify(format_alert(
                "error", f"{name}: run failed — {exc} — frozen pending review"))
            continue

    # Portfolio-wide P&L across the agents that completed today.
    if pnl_start_total > 0:
        notifier.notify(format_pnl("ИТОГО", pnl_start_total, pnl_end_total))
