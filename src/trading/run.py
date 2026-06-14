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
  NEWS                    yfinance (default) | fake
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


def _assert_go_live_allowed(profiles, journal, source) -> None:
    """Block real-money (live IBKR) trading until each agent's forward track record
    clears the go-live gate: long enough, beats SPY on Sharpe, drawdown within limit.
    Override knowingly with GO_LIVE_OVERRIDE=1. Paper trading (port 4002) is exempt."""
    if os.environ.get("GO_LIVE_OVERRIDE") == "1":
        return
    from trading.analysis.track_record import evaluate_go_live

    blocked = []
    for name, profile in profiles.items():
        curve = [eq for _, eq in journal.equity_curve(name)]
        spy = [b.close for b in source.history("SPY", days=max(len(curve), 1))]
        result = evaluate_go_live(curve, spy, profile.max_drawdown_pct)
        if not result.cleared:
            blocked.append(f"{name}: " + "; ".join(result.reasons))
    if blocked:
        raise SystemExit(
            "Go-live gate not cleared — refusing to trade real money "
            "(set GO_LIVE_OVERRIDE=1 to bypass):\n  " + "\n  ".join(blocked))


def _broker_for(profile, index: int):
    if os.environ.get("BROKER", "fake") == "ibkr":
        from trading.broker.ibkr import IBKRBroker
        base = int(os.environ.get("IBKR_CLIENT_ID_BASE", "1"))
        # Each agent trades its OWN IBKR account so positions/cash never commingle.
        # IBKR_ACCOUNTS is a comma-separated list aligned to the profile order.
        accounts = [a.strip() for a in os.environ.get("IBKR_ACCOUNTS", "").split(",") if a.strip()]
        broker = IBKRBroker(
            host=os.environ.get("IBKR_HOST", "127.0.0.1"),
            port=int(os.environ.get("IBKR_PORT", "4002")),
            client_id=base + index,
            account=accounts[index] if index < len(accounts) else None,
        )
        broker.connect()
        return broker
    from trading.broker.fake import FakeBroker
    return FakeBroker(cash=profile.budget)


def _mode_tag() -> str:
    """fake | paper | live — used to keep each mode's track record in its own DB."""
    if os.environ.get("BROKER", "fake") != "ibkr":
        return "fake"
    return "live" if int(os.environ.get("IBKR_PORT", "4002")) == 4001 else "paper"


def resolve_db_path() -> str:
    """The DB file for the current mode. Mode-tagged so fake/paper/live never commingle;
    override with DB_PATH. The command bot MUST resolve the path the same way, or it would
    read a different file than the run writes to."""
    return os.environ.get("DB_PATH") or f"data/trading-{_mode_tag()}.db"


def _news_source_for():
    """NEWS=yfinance (default) | fake. yfinance failures degrade to no news."""
    if os.environ.get("NEWS", "yfinance") == "fake":
        from trading.data.news import FakeNews
        return FakeNews()
    from trading.data.news import YFinanceNews
    return YFinanceNews()


def build_components():
    profiles = load_profiles("config/profiles.toml")
    universe = load_universe("config/universe.toml")
    source = YFinanceSource()

    # Separate the track record by mode so fake/paper/live NEVER commingle. Each gets its
    # own DB file; switching to real money starts a clean ledger. Override with DB_PATH.
    db_path = resolve_db_path()
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = connect(db_path)
    init_db(conn)
    accounts, journal, freezes = (AccountRepository(conn), JournalRepository(conn),
                                  FreezeStore(conn))
    from trading.persistence.runlock import RunLock
    run_lock = RunLock(conn)

    # Gate real money: live IBKR (port 4001) requires a track record that beats SPY.
    # Paper (4002) is exempt — that is how the record is built.
    if (os.environ.get("BROKER", "fake") == "ibkr"
            and int(os.environ.get("IBKR_PORT", "4002")) == 4001):
        _assert_go_live_allowed(profiles, journal, source)

    brokers = {name: _broker_for(p, i) for i, (name, p) in enumerate(profiles.items())}
    for name, broker in brokers.items():
        # Carry FakeBroker state across runs from the ledger (it is otherwise in-memory),
        # so reconcile matches and a multi-day track record accumulates.
        if hasattr(broker, "seed"):
            prev = accounts.get_state(name)
            if prev is not None:
                broker.seed(prev.cash, prev.positions)
        # FakeBrokers need a fill price; seed from live data.
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
        # Test and real money run under separate bots/tokens, so messages no longer need
        # a per-message "this is a test" banner to tell them apart.
        notifier = TelegramNotifier()

    # Confirmation policy: ask in Telegram by default — you keep oversight, large trades
    # wait for your tap. Tune frequency via each profile's auto_exec_threshold_usd (small
    # trades auto-execute), or silence entirely with CONFIRM=auto.
    confirm_mode = os.environ.get("CONFIRM", "telegram")
    confirm = (lambda proposal, decision: True) if confirm_mode == "auto" else None

    return dict(profiles=profiles, brokers=brokers, source=source, strategy=strategy,
                panel=panel, notifier=notifier, accounts=accounts, journal=journal,
                freezes=freezes, run_lock=run_lock, universe=universe, confirm=confirm,
                news_source=_news_source_for(),
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
