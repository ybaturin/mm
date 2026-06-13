from trading.agent.schema import ProposalBatch, ProposedTrade, to_domain_proposals
from trading.domain import Intent


def test_proposed_trade_accepts_valid_intent():
    t = ProposedTrade(symbol="AAPL", intent="open_long", quantity=10,
                      reference_price=190.0, stop_loss_price=175.0, rationale="momentum")
    assert t.intent == "open_long"


def test_to_domain_proposals_maps_fields_and_sets_agent_id():
    batch = ProposalBatch(trades=[
        ProposedTrade(symbol="AAPL", intent="open_long", quantity=10,
                      reference_price=190.0, stop_loss_price=175.0, rationale="momentum"),
        ProposedTrade(symbol="TSLA", intent="open_short", quantity=4,
                      reference_price=200.0, stop_loss_price=215.0, rationale="overbought"),
    ])
    proposals = to_domain_proposals(batch, agent_id="aggressive")

    assert len(proposals) == 2
    assert proposals[0].agent_id == "aggressive"
    assert proposals[0].symbol == "AAPL"
    assert proposals[0].intent is Intent.OPEN_LONG
    assert proposals[1].intent is Intent.OPEN_SHORT
    assert proposals[1].stop_loss_price == 215.0


def test_to_domain_proposals_allows_null_stop_for_close():
    batch = ProposalBatch(trades=[
        ProposedTrade(symbol="AAPL", intent="close_long", quantity=10,
                      reference_price=190.0, stop_loss_price=None, rationale="take profit"),
    ])
    proposals = to_domain_proposals(batch, agent_id="moderate")
    assert proposals[0].intent is Intent.CLOSE_LONG
    assert proposals[0].stop_loss_price is None


def test_empty_batch_maps_to_empty_list():
    assert to_domain_proposals(ProposalBatch(trades=[]), agent_id="conservative") == []
