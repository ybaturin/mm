"""Manual check: run a fake briefing through the real Claude API.

Requires ANTHROPIC_API_KEY in the environment. Uses canned market data, so it does
NOT need IBKR or live prices.

Run:  uv run python scripts/smoke_agent.py
"""
from __future__ import annotations

from trading.agent.core import AgentCore
from trading.config import load_profiles
from trading.data.briefing import Briefing, SymbolBrief


def main() -> None:
    profile = load_profiles("config/profiles.toml")["moderate"]
    briefing = Briefing(
        agent_id="moderate", as_of_date="2026-06-15", cash=5000.0, equity=5000.0,
        symbols=[
            SymbolBrief("AAPL", 159.0, 150.0, 140.0, 62.0, 0.03, 0, None),
            SymbolBrief("MSFT", 410.0, 405.0, 395.0, 48.0, -0.01, 0, None),
            SymbolBrief("SPY", 540.0, 535.0, 520.0, 55.0, 0.02, 0, None),
        ],
    )
    proposals = AgentCore().propose(briefing, profile)
    if not proposals:
        print("No trades proposed.")
    for p in proposals:
        print(f"{p.intent.value} {p.quantity} {p.symbol} @ ~{p.reference_price} "
              f"stop={p.stop_loss_price} — {p.rationale}")


if __name__ == "__main__":
    main()
