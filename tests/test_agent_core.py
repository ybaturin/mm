from types import SimpleNamespace

from trading.agent.core import AgentCore
from trading.agent.schema import ProposalBatch, ProposedTrade
from trading.config import RiskProfile
from trading.data.briefing import Briefing, SymbolBrief
from trading.domain import Intent


def make_profile():
    return RiskProfile(
        name="moderate", budget=5000.0, max_position_pct=0.25, min_positions=5,
        allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
        daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
        auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority",
    )


def make_briefing():
    return Briefing(
        agent_id="moderate", as_of_date="2026-06-15", cash=5000.0, equity=5000.0,
        symbols=[SymbolBrief("AAPL", 159.0, 150.0, 140.0, 60.0, 0.03, 0, None)],
    )


def stub_client(batch):
    """A fake Anthropic client whose messages.parse returns a fixed parsed_output."""
    captured = {}

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(parsed_output=batch)

    client = SimpleNamespace(messages=SimpleNamespace(parse=parse))
    return client, captured


def test_propose_returns_domain_proposals_with_agent_id():
    batch = ProposalBatch(trades=[
        ProposedTrade(symbol="AAPL", intent="open_long", quantity=10,
                      reference_price=159.0, stop_loss_price=146.0, rationale="uptrend"),
    ])
    client, _ = stub_client(batch)
    core = AgentCore(client=client, model="claude-opus-4-8")

    proposals = core.propose(make_briefing(), make_profile())

    assert len(proposals) == 1
    assert proposals[0].agent_id == "moderate"
    assert proposals[0].intent is Intent.OPEN_LONG
    assert proposals[0].symbol == "AAPL"


def test_propose_sends_model_and_structured_output_format():
    batch = ProposalBatch(trades=[])
    client, captured = stub_client(batch)
    core = AgentCore(client=client, model="claude-opus-4-8")

    core.propose(make_briefing(), make_profile())

    assert captured["model"] == "claude-opus-4-8"
    assert captured["output_format"] is ProposalBatch     # strict structured output
    assert "system" in captured and "messages" in captured


def test_propose_empty_batch_returns_empty_list():
    client, _ = stub_client(ProposalBatch(trades=[]))
    core = AgentCore(client=client, model="claude-opus-4-8")
    assert core.propose(make_briefing(), make_profile()) == []
