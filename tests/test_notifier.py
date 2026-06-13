from trading.domain import Intent, Outcome, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.reporting.notifier import FakeNotifier, make_confirm


def proposal():
    return TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
                         quantity=10, reference_price=160.0, stop_loss_price=145.0,
                         rationale="uptrend")


def test_fake_notifier_records_messages():
    n = FakeNotifier()
    n.notify("hello")
    n.notify("world")
    assert n.messages == ["hello", "world"]


def test_fake_notifier_confirmation_default_true_and_recorded():
    n = FakeNotifier()
    assert n.request_confirmation("approve?") is True
    assert n.confirmations == ["approve?"]


def test_fake_notifier_can_decline():
    n = FakeNotifier(confirm_result=False)
    assert n.request_confirmation("approve?") is False


def test_make_confirm_adapts_notifier_to_run_cycle_callback():
    n = FakeNotifier(confirm_result=True)
    confirm = make_confirm(n)
    decision = GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 10, [])

    result = confirm(proposal(), decision)

    assert result is True
    assert len(n.confirmations) == 1
    assert "AAPL" in n.confirmations[0]            # the confirmation text was the proposal
