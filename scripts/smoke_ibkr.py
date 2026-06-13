"""Manual check: connect to the paper IB Gateway and print account state.

NOT a unit test — requires a running IB Gateway logged into a PAPER account.
Run:  IBKR_PORT=4002 uv run python scripts/smoke_ibkr.py
"""
from __future__ import annotations

import os

from trading.broker.ibkr import IBKRBroker


def main() -> None:
    broker = IBKRBroker(
        host=os.environ.get("IBKR_HOST", "127.0.0.1"),
        port=int(os.environ.get("IBKR_PORT", "4002")),
        client_id=int(os.environ.get("IBKR_CLIENT_ID", "1")),
    )
    broker.connect()
    try:
        print(f"connected: {broker.is_connected()}")
        print(f"cash (USD): {broker.cash():.2f}")
        positions = broker.positions()
        if not positions:
            print("positions: none")
        for p in positions:
            print(f"  {p.symbol}: {p.quantity} @ {p.avg_price:.2f}")
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
