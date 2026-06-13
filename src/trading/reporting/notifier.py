from __future__ import annotations

from typing import Protocol

from trading.domain import TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.reporting.format import format_confirmation


class Notifier(Protocol):
    def notify(self, text: str) -> None: ...
    def request_confirmation(self, text: str) -> bool: ...


class FakeNotifier:
    """Records messages and auto-answers confirmations. For tests and simulation."""

    def __init__(self, confirm_result: bool = True) -> None:
        self.messages: list[str] = []
        self.confirmations: list[str] = []
        self._confirm_result = confirm_result

    def notify(self, text: str) -> None:
        self.messages.append(text)

    def request_confirmation(self, text: str) -> bool:
        self.confirmations.append(text)
        return self._confirm_result


def make_confirm(notifier: Notifier):
    """Adapt a Notifier into run_cycle's confirm(proposal, decision) -> bool callback."""
    def confirm(proposal: TradeProposal, decision: GuardrailDecision) -> bool:
        return notifier.request_confirmation(format_confirmation(proposal, decision))
    return confirm
