from __future__ import annotations

import os

from trading.agent.prompts import build_system_prompt, build_user_prompt
from trading.agent.schema import ProposalBatch, to_domain_proposals
from trading.config import RiskProfile
from trading.data.briefing import Briefing
from trading.domain import TradeProposal

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "claude-opus-4-8")
MAX_TOKENS = 8192


class AgentCore:
    """Asks Claude for trade proposals. The only component that calls the LLM.

    Claude returns a strict ProposalBatch (it cannot express anything outside that
    schema). The result is inert data — execution happens elsewhere, behind guardrails.
    """

    def __init__(self, client=None, model: str = DEFAULT_MODEL) -> None:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def propose(self, briefing: Briefing, profile: RiskProfile) -> list[TradeProposal]:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=build_system_prompt(profile),
            messages=[{"role": "user", "content": build_user_prompt(briefing)}],
            output_format=ProposalBatch,
        )
        batch: ProposalBatch = response.parsed_output
        return to_domain_proposals(batch, briefing.agent_id)
