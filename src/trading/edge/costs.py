from __future__ import annotations

# Round-trip cost (entry+exit) in basis points, by liquidity tier. Starting values,
# configurable. Small-cap spreads + impact dwarf large-cap — this is what most likely
# kills a small-cap edge. v1 tags symbols 'large' or 'small'; 'mid' kept for later.
ROUND_TRIP_BPS = {"large": 5.0, "mid": 20.0, "small": 60.0}


def position_pnl(gross_return: float, tier: str, side: str) -> float:
    """Net P&L of one position over its hold, after the tier's round-trip cost.
    `side` is 'long' or 'short'. A short profits when gross_return is negative."""
    cost = ROUND_TRIP_BPS[tier] / 10_000.0
    directional = gross_return if side == "long" else -gross_return
    return directional - cost
