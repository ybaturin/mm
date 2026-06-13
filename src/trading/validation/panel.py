from __future__ import annotations

import os
from dataclasses import dataclass

from trading.data.briefing import Briefing
from trading.domain import TradeProposal
from trading.validation.roles import ROLES, Role, build_validator_system, build_validator_user
from trading.validation.schema import Verdict

DEFAULT_MODEL = os.environ.get("VALIDATOR_MODEL", "claude-opus-4-8")
MAX_TOKENS = 1024


@dataclass(frozen=True)
class RoleVerdict:
    role: str
    veto: bool
    reason: str


@dataclass(frozen=True)
class PanelResult:
    blocked: bool
    verdicts: list[RoleVerdict]


def apply_veto_rule(vetoes: list[bool], veto_rule: str) -> bool:
    """Whether the panel blocks. 'any': one veto blocks. 'majority': more than half block."""
    if veto_rule == "any":
        return any(vetoes)
    return sum(1 for v in vetoes if v) * 2 > len(vetoes)


class ValidationPanel:
    """Role-diverse second opinion. Subtractive only — blocks or allows, never resizes."""

    def __init__(self, client=None, model: str = DEFAULT_MODEL) -> None:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def review(self, proposal: TradeProposal, briefing: Briefing, veto_rule: str) -> PanelResult:
        verdicts = [self._verdict(role, proposal, briefing) for role in ROLES]
        blocked = apply_veto_rule([v.veto for v in verdicts], veto_rule)
        return PanelResult(blocked=blocked, verdicts=verdicts)

    def _verdict(self, role: Role, proposal: TradeProposal, briefing: Briefing) -> RoleVerdict:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=build_validator_system(role),
            messages=[{"role": "user", "content": build_validator_user(proposal, briefing)}],
            output_format=Verdict,
        )
        v: Verdict = response.parsed_output
        return RoleVerdict(role=role.key, veto=v.veto, reason=v.reason)
