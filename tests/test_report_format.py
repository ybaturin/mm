from trading.broker.types import Action, Fill
from trading.domain import Intent, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.domain import Outcome
from trading.reporting.format import (
    format_alert, format_confirmation, format_digest, format_fill, format_pnl,
)


def proposal():
    return TradeProposal(agent_id="aggressive", symbol="TSLA", intent=Intent.OPEN_SHORT,
                         quantity=5, reference_price=200.0, stop_loss_price=215.0,
                         rationale="overbought")


def test_format_confirmation_has_agent_trade_notional_and_reason():
    decision = GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 5, [])
    msg = format_confirmation(proposal(), decision)
    assert "aggressive" in msg
    assert "TSLA" in msg
    assert "5" in msg
    assert "1000" in msg or "1,000" in msg        # 5 * 200 notional
    assert "overbought" in msg


def test_format_fill_reads_naturally():
    fill = Fill(symbol="AAPL", action=Action.BUY, quantity=3, price=101.5)
    msg = format_fill("moderate", fill)
    assert "moderate" in msg and "AAPL" in msg and "3" in msg and "101.5" in msg


def test_format_digest_summarizes_counts():
    msg = format_digest("conservative", "2026-06-15",
                        executed=["BUY 2 SPY @ 540.0"], rejected=1, vetoed=2)
    assert "conservative" in msg
    assert "2026-06-15" in msg
    assert "SPY" in msg
    assert "1" in msg and "2" in msg              # rejected / vetoed counts


def test_format_digest_handles_quiet_day():
    msg = format_digest("moderate", "2026-06-15", executed=[], rejected=0, vetoed=0)
    assert "no trades" in msg.lower() or "nothing" in msg.lower()


def test_format_alert_is_marked():
    msg = format_alert("kill-switch", "moderate hit -5% daily loss; frozen for today")
    assert "kill-switch" in msg
    assert "moderate" in msg


def test_format_pnl_shows_change_and_percent():
    msg = format_pnl("aggressive", start=5000.0, end=5472.45)
    assert "aggressive" in msg
    assert "5,472" in msg or "5472" in msg
    assert "+472" in msg or "472.45" in msg
    assert "9.4%" in msg or "+9.4" in msg
