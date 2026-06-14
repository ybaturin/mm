from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading.edge.events import EarningsEvent


@dataclass(frozen=True)
class EventDocuments:
    """Point-in-time primary sources for one event (all dated <= decision_date)."""
    symbol: str
    decision_date: str
    transcript: str
    press_release: str = ""
    mdna: str = ""


class DocumentSource(Protocol):
    def documents(self, event: EarningsEvent) -> EventDocuments: ...


class FakeDocumentSource:
    """Deterministic documents for tests and offline runs. Satisfies DocumentSource."""

    def __init__(self, by_symbol: dict[str, EventDocuments]) -> None:
        self._by_symbol = by_symbol

    def documents(self, event: EarningsEvent) -> EventDocuments:
        return self._by_symbol[event.symbol]
