from __future__ import annotations

from trading.config import RiskProfile
from trading.data.bars import MarketDataSource
from trading.domain import AgentState
from trading.orchestrator.cycle import run_cycle
from trading.orchestrator.strategy import Strategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.freezes import GLOBAL, FreezeStore
from trading.persistence.journal import JournalRepository
from trading.reporting.format import format_alert, format_digest
from trading.reporting.notifier import Notifier, make_confirm
from trading.safety.reconcile import reconcile
from trading.safety.watchdog import Watchdog, flatten


def _summary(journal: JournalRepository, agent_id: str, date: str):
    fills = [r for r in journal.fills_for(agent_id) if r["ts"].startswith(date)]
    executed = [f'{r["intent"]} {r["quantity"]} {r["symbol"]} @ {r["price"]:g}' for r in fills]
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
) -> None:
    """Run the full pre-market cycle for every agent. The production keystone.

    Per agent: skip if frozen -> reconcile ledger vs broker -> run_cycle (panel +
    Telegram confirm) -> watchdog (flatten + freeze on breach) -> digest.
    """
    confirm = make_confirm(notifier)
    prices = {s: source.latest_price(s) for s in universe}

    for name, profile in profiles.items():
        if freezes.is_frozen(GLOBAL):
            notifier.notify(format_alert("frozen", f"GLOBAL halt active — {name} skipped"))
            continue
        if freezes.is_frozen(name):
            notifier.notify(format_alert("frozen", f"{name} skipped: {freezes.reason(name)}"))
            continue

        broker = brokers[name]

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
            as_of_date=as_of_date, ts=ts, confirm=confirm, panel=panel,
        )

        result = Watchdog(profile.budget, floor_fraction).check(broker, prices)
        if result.breached:
            flatten(broker, prices)
            post = accounts.get_state(name)
            accounts.save_state(AgentState(
                name, broker.cash(), broker.positions(),
                post.peak_equity, post.equity_day_start))
            freezes.freeze(name, f"NAV {result.nav:.0f} < floor {result.floor:.0f}", ts)
            notifier.notify(format_alert(
                "watchdog",
                f"{name}: NAV {result.nav:.0f} < floor {result.floor:.0f} — flattened & frozen"))

        executed, rejected, vetoed = _summary(journal, name, as_of_date)
        notifier.notify(format_digest(name, as_of_date, executed, rejected, vetoed))
