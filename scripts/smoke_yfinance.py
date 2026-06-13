"""Manual check: fetch real bars for a symbol and print the last few.

Run:  uv run python scripts/smoke_yfinance.py AAPL
"""
from __future__ import annotations

import sys

from trading.data.yfinance_source import YFinanceSource


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    bars = YFinanceSource().history(symbol, days=5)
    print(f"{symbol}: {len(bars)} bars")
    for b in bars:
        print(f"  {b.date}  close={b.close:.2f}  vol={b.volume}")


if __name__ == "__main__":
    main()
