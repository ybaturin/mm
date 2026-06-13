from __future__ import annotations

from dataclasses import dataclass, field

from trading.broker.base import Broker
from trading.domain import AgentState


@dataclass(frozen=True)
class ReconResult:
    ok: bool
    discrepancies: list[str] = field(default_factory=list)


def reconcile(ledger: AgentState, broker: Broker, tolerance: float = 0.01) -> ReconResult:
    """Compare the system's ledger to the broker's real state. Any divergence is a problem.

    Catches bugs in our accounting and positions opened outside the system.
    """
    discrepancies: list[str] = []

    if abs(ledger.cash - broker.cash()) > tolerance:
        discrepancies.append(
            f"cash mismatch: ledger {ledger.cash:.2f} vs broker {broker.cash():.2f}")

    ledger_pos = {p.symbol: p.quantity for p in ledger.positions}
    broker_pos = {p.symbol: p.quantity for p in broker.positions()}
    for symbol in sorted(set(ledger_pos) | set(broker_pos)):
        lq, bq = ledger_pos.get(symbol, 0), broker_pos.get(symbol, 0)
        if lq != bq:
            discrepancies.append(
                f"{symbol} quantity mismatch: ledger {lq} vs broker {bq}")

    return ReconResult(ok=not discrepancies, discrepancies=discrepancies)
