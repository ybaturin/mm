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
    assert "moderate" in msg
    assert "AAPL" in msg
    assert "Покупка" in msg
    assert "3" in msg
    assert "101.50" in msg
    assert "исполнена" in msg.lower()


def test_format_digest_summarizes_counts():
    msg = format_digest("conservative", "2026-06-15",
                        executed=["BUY 2 SPY @ 540.0"], rejected=1, vetoed=2)
    assert "conservative" in msg
    assert "2026-06-15" in msg
    assert "SPY" in msg
    assert "1" in msg and "2" in msg              # rejected / vetoed counts


def test_format_digest_handles_quiet_day():
    msg = format_digest("moderate", "2026-06-15", executed=[], rejected=0, vetoed=0)
    assert "сделок нет" in msg.lower()


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


from trading.reporting.format import (
    format_positions, format_pnl_report, format_status, format_trades,
)
from trading.reporting.queries import (
    PnlLine, PnlReport, PositionLine, PositionsReport,
    StatusReport, TradeLine, TradesReport,
)


def test_format_pnl_report_shows_portfolio_and_agents():
    rep = PnlReport("week",
                    [PnlLine("momentum", 10000.0, 10800.0, 800.0, 0.08)],
                    10000.0, 10800.0, 800.0, 0.08)
    msg = format_pnl_report(rep)
    assert "неделю" in msg
    assert "momentum" in msg
    assert "+800" in msg or "800.00" in msg
    assert "8.0%" in msg


def test_format_positions_marks_direction_and_pnl():
    rep = PositionsReport(
        {"momentum": [PositionLine("momentum", "AAPL", 10, 200.0, 210.0, 100.0)]},
        100.0, 2100.0)
    msg = format_positions(rep)
    assert "AAPL" in msg
    assert "LONG" in msg
    assert "+100" in msg or "100.00" in msg


def test_format_positions_empty_agent_says_so():
    rep = PositionsReport({"flat": []}, 0.0, 0.0)
    msg = format_positions(rep)
    assert "flat" in msg
    assert "позиций нет" in msg.lower()


def test_format_status_shows_equity_and_freezes():
    rep = StatusReport(2000.0, 100.0, 0.0526, 1, [("a", "manual hold")])
    msg = format_status(rep)
    assert "2,000" in msg
    assert "manual hold" in msg


def test_format_trades_lists_fills():
    rep = TradesReport([TradeLine("2026-06-13T13:30:00Z", "b", "open_short",
                                  "TSLA", 3, 250.0)])
    msg = format_trades(rep)
    assert "TSLA" in msg and "b" in msg


def test_format_trades_handles_empty():
    assert "сделок нет" in format_trades(TradesReport([])).lower()
