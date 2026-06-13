"""Assemble real components from the environment and run one daily cycle.

Env:
  DB_PATH                 default data/trading.db
  BROKER                  fake (default) | ibkr
  FLOOR_FRACTION          default 0.8
  ANTHROPIC_API_KEY       required unless STRATEGY=fake
  STRATEGY                claude (default) | fake
  PANEL                   on (default) | off
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   required unless NOTIFIER=fake
  NOTIFIER                telegram (default) | fake
  IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID_BASE   (BROKER=ibkr)

BROKER=fake is a real dry-run: live Claude + live Telegram on simulated fills priced
from real yfinance data. No IBKR, no money.

Run:  uv run python -m trading.run [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import os
from datetime import date as date_cls

from trading.config import load_profiles
from trading.data.briefing import load_universe
from trading.data.yfinance_source import YFinanceSource
from trading.orchestrator.daily import run_daily
from trading.orchestrator.strategy import FakeStrategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.freezes import FreezeStore
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db
from trading.reporting.notifier import FakeNotifier


def _broker_for(profile, index: int):
    if os.environ.get("BROKER", "fake") == "ibkr":
        from trading.broker.ibkr import IBKRBroker
        base = int(os.environ.get("IBKR_CLIENT_ID_BASE", "1"))
        broker = IBKRBroker(
            host=os.environ.get("IBKR_HOST", "127.0.0.1"),
            port=int(os.environ.get("IBKR_PORT", "4002")),
            client_id=base + index,
        )
        broker.connect()
        return broker
    from trading.broker.fake import FakeBroker
    return FakeBroker(cash=profile.budget)


def build_components():
    profiles = load_profiles("config/profiles.toml")
    universe = load_universe("config/universe.toml")
    source = YFinanceSource()

    db_path = os.environ.get("DB_PATH", "data/trading.db")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = connect(db_path)
    init_db(conn)
    accounts, journal, freezes = (AccountRepository(conn), JournalRepository(conn),
                                  FreezeStore(conn))

    brokers = {name: _broker_for(p, i) for i, (name, p) in enumerate(profiles.items())}
    # FakeBrokers need a fill price; seed from live data.
    for name, broker in brokers.items():
        if hasattr(broker, "set_price"):
            for s in universe:
                broker.set_price(s, source.latest_price(s))

    if os.environ.get("STRATEGY", "claude") == "fake":
        strategy = FakeStrategy()
    else:
        from trading.agent.core import AgentCore
        strategy = AgentCore()

    panel = None
    if os.environ.get("PANEL", "on") == "on":
        from trading.validation.panel import ValidationPanel
        panel = ValidationPanel()

    if os.environ.get("NOTIFIER", "telegram") == "fake":
        notifier = FakeNotifier()
    else:
        from trading.reporting.telegram import TelegramNotifier
        notifier = TelegramNotifier()

    return dict(profiles=profiles, brokers=brokers, source=source, strategy=strategy,
                panel=panel, notifier=notifier, accounts=accounts, journal=journal,
                freezes=freezes, universe=universe,
                floor_fraction=float(os.environ.get("FLOOR_FRACTION", "0.8")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one daily trading cycle.")
    parser.add_argument("--date", default=date_cls.today().isoformat())
    args = parser.parse_args()

    components = build_components()
    run_daily(as_of_date=args.date, ts=f"{args.date}T13:30:00Z", **components)
    print(f"Daily run complete for {args.date}.")


if __name__ == "__main__":
    main()
