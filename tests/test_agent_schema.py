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


def _batch(**over):
    base = dict(symbol="AAPL", intent="open_long", quantity=10,
                reference_price=185.0, stop_loss_price=176.0, rationale="rebound")
    base.update(over)
    return ProposalBatch(trades=[ProposedTrade(**base)])


def test_forecast_fields_pass_through():
    p = to_domain_proposals(_batch(target_price=200.0, horizon_days=14), "aggressive")[0]
    assert p.target_price == 200.0
    assert p.horizon_days == 14


def test_wrong_side_target_is_dropped_for_long():
    # Target below entry on a long is incoherent — drop it, keep the trade.
    p = to_domain_proposals(_batch(target_price=170.0, horizon_days=14), "aggressive")[0]
    assert p.target_price is None
    assert p.horizon_days is None


def test_missing_forecast_is_tolerated():
    p = to_domain_proposals(_batch(), "aggressive")[0]
    assert p.target_price is None
    assert p.horizon_days is None


def test_short_forecast_target_below_entry_kept():
    p = to_domain_proposals(
        _batch(intent="open_short", reference_price=200.0, stop_loss_price=215.0,
               target_price=180.0, horizon_days=7), "aggressive")[0]
    assert p.target_price == 180.0
    assert p.horizon_days == 7
