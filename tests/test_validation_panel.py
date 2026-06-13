from types import SimpleNamespace

from trading.data.briefing import Briefing, SymbolBrief
from trading.domain import Intent, TradeProposal
from trading.validation.panel import PanelResult, ValidationPanel, apply_veto_rule
from trading.validation.schema import Verdict


def test_apply_veto_rule_any():
    assert apply_veto_rule([False, False, False], "any") is False
    assert apply_veto_rule([False, True, False], "any") is True


def test_apply_veto_rule_majority():
    assert apply_veto_rule([True, False, False], "majority") is False     # 1 of 3
    assert apply_veto_rule([True, True, False], "majority") is True       # 2 of 3
    assert apply_veto_rule([True, True], "majority") is True              # 2 of 2


def proposal():
    return TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
                         quantity=5, reference_price=160.0, stop_loss_price=145.0, rationale="x")


def briefing():
    return Briefing("moderate", "2026-06-15", 5000.0, 5000.0,
                    [SymbolBrief("AAPL", 160.0, 150.0, 140.0, 55.0, 0.03, 0, None)])


def stub_client(verdicts):
    it = iter(verdicts)

    def parse(**kwargs):
        return SimpleNamespace(parsed_output=next(it))

    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def test_review_allows_when_no_vetoes():
    client = stub_client([Verdict(veto=False, reason="") for _ in range(3)])
    panel = ValidationPanel(client=client, model="claude-opus-4-8")
    result = panel.review(proposal(), briefing(), veto_rule="majority")
    assert isinstance(result, PanelResult)
    assert result.blocked is False
    assert len(result.verdicts) == 3


def test_review_any_rule_blocks_on_single_veto():
    client = stub_client([
        Verdict(veto=False, reason=""),
        Verdict(veto=True, reason="earnings tomorrow"),
        Verdict(veto=False, reason=""),
    ])
    panel = ValidationPanel(client=client, model="claude-opus-4-8")
    result = panel.review(proposal(), briefing(), veto_rule="any")
    assert result.blocked is True
    assert any(v.veto and "earnings" in v.reason for v in result.verdicts)


def test_review_majority_rule_needs_two_vetoes():
    client = stub_client([
        Verdict(veto=True, reason="risky"),
        Verdict(veto=False, reason=""),
        Verdict(veto=False, reason=""),
    ])
    panel = ValidationPanel(client=client, model="claude-opus-4-8")
    result = panel.review(proposal(), briefing(), veto_rule="majority")
    assert result.blocked is False
