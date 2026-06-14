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


def test_confirmation_long_shows_expected_profit_and_horizon():
    p = TradeProposal(agent_id="aggressive", symbol="AAPL", intent=Intent.OPEN_LONG,
                      quantity=12, reference_price=185.0, stop_loss_price=176.0,
                      rationale="перепродана, жду отскок",
                      target_price=200.0, horizon_days=14)
    msg = format_confirmation(p, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 12, []))
    assert "AAPL" in msg
    assert "Купить" in msg
    assert "перепродана" in msg
    assert "+8" in msg                      # +8.1% expected return
    assert "180" in msg                     # ≈ +$180 expected profit (12 * (200-185))
    assert "недели" in msg                  # horizon rendered ~2 недели
    assert "176" in msg                     # stop


def test_confirmation_short_inverts_profit_sign():
    p = TradeProposal(agent_id="aggressive", symbol="TSLA", intent=Intent.OPEN_SHORT,
                      quantity=5, reference_price=200.0, stop_loss_price=215.0,
                      rationale="перегрета", target_price=180.0, horizon_days=7)
    msg = format_confirmation(p, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 5, []))
    assert "+10" in msg                     # short to 180 from 200 is +10% gain
    assert "100" in msg                     # ≈ +$100 (5 * (200-180))


def test_confirmation_close_has_no_target_block():
    p = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.CLOSE_LONG,
                      quantity=3, reference_price=190.0, stop_loss_price=None,
                      rationale="фиксирую прибыль")
    msg = format_confirmation(p, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 3, []))
    assert "Цель" not in msg
    assert "фиксирую прибыль" in msg


def test_confirmation_escapes_rationale_html():
    p = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
                      quantity=1, reference_price=10.0, stop_loss_price=9.0,
                      rationale="a < b & c", target_price=12.0, horizon_days=5)
    msg = format_confirmation(p, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 1, []))
    assert "a &lt; b &amp; c" in msg


def test_format_fill_reads_naturally():
    fill = Fill(symbol="AAPL", action=Action.BUY, quantity=3, price=101.5)
    msg = format_fill("moderate", fill)
    assert "moderate" in msg
    assert "AAPL" in msg
    assert "покупка" in msg.lower()
    assert "3" in msg
    assert "101.50" in msg


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
    assert "FLAT" in msg
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
    assert "TSLA" in msg and "B" in msg


from trading.reporting.format import (
    html_escape, human_horizon, human_days_left, mono_table, pnl_color,
)


def test_html_escape_neutralizes_markup():
    assert html_escape("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_human_horizon_buckets():
    assert human_horizon(3) == "3 дня"
    assert human_horizon(7) == "~1 неделю"
    assert human_horizon(10) == "~1 неделю"
    assert human_horizon(14) == "~2 недели"
    assert human_horizon(30) == "~1 месяц"


def test_human_days_left_handles_overdue():
    assert human_days_left(9) == "~9 дн."
    assert human_days_left(0) == "сегодня"
    assert human_days_left(-2) == "просрочено"


def test_pnl_color_by_sign():
    assert pnl_color(5.0) == "🟢"
    assert pnl_color(-5.0) == "🔴"
    assert pnl_color(0.0) == "⚪"        # flat is neutral, not green


def test_money_signed_drops_sign_when_flat():
    from trading.reporting.format import money_signed
    assert money_signed(800.0) == "+800$"
    assert money_signed(-420.0) == "-420$"
    assert money_signed(0.0) == "0$"


def test_positions_no_code_block_and_no_arrows():
    rep = PositionsReport(
        {"moderate": [PositionLine("moderate", "DIA", 1, 513.06, 513.06, 0.0)]},
        0.0, 513.06)
    msg = format_positions(rep)
    assert "<pre>" not in msg          # plain text, no monospace box that overflows on mobile
    assert "→" not in msg              # arrows hurt readability
    assert "⚪" in msg                 # flat P&L is neutral
    assert "0$" in msg


def test_mono_table_aligns_columns_and_wraps_in_pre():
    out = mono_table(
        [["14.06", "+3", "IWM", "292.95"],
         ["13.06", "-1", "TSLA", "406.43"]],
        aligns="lllr",
    )
    assert out.startswith("<pre>") and out.endswith("</pre>")
    lines = out[len("<pre>"):-len("</pre>")].strip("\n").split("\n")
    # Every line is padded to the same width.
    assert len({len(l) for l in lines}) == 1
    # Symbol column is left-aligned, price column right-aligned.
    assert "IWM " in lines[0]
    assert lines[0].endswith("292.95")


def test_mono_table_escapes_cells():
    out = mono_table([["a<b"]], aligns="l")
    assert "a&lt;b" in out


def test_format_trades_handles_empty():
    assert "сделок нет" in format_trades(TradesReport([])).lower()


from trading.reporting.format import format_retro


def test_trades_group_by_agent_and_use_signed_qty():
    rep = TradesReport([
        TradeLine("2026-06-14T13:00:00Z", "aggressive", "open_long", "IWM", 3, 292.95),
        TradeLine("2026-06-13T13:00:00Z", "aggressive", "close_long", "TSLA", 1, 406.43),
        TradeLine("2026-06-14T13:00:00Z", "moderate", "open_long", "DIA", 1, 513.06),
    ])
    msg = format_trades(rep)
    assert "AGGRESSIVE" in msg.upper()
    assert "MODERATE" in msg.upper()
    assert "+3" in msg          # buy -> positive
    assert "-1" in msg or "−1" in msg   # sell -> negative
    assert "<pre>" in msg       # monospace table
    assert "тикер" in msg       # header row (keeps the </> copy button off the data)


def test_pnl_report_header_shows_benchmark():
    rep = PnlReport("week",
                    [PnlLine("aggressive", 13500.0, 13080.0, -420.0, -0.0311)],
                    13500.0, 13080.0, -420.0, -0.0311, benchmark_pct=0.014)
    msg = format_pnl_report(rep)
    assert "SPY" in msg
    assert "1.4%" in msg
    assert "🔴" in msg          # negative aggressive P&L colored red


def test_positions_show_target_and_path():
    rep = PositionsReport(
        {"aggressive": [PositionLine("aggressive", "IWM", 3, 292.95, 298.10, 15.45,
                                     target_price=315.0, path_pct=0.43, days_left=9,
                                     horizon_days=14)]},
        15.45, 894.30)
    msg = format_positions(rep)
    assert "IWM" in msg
    assert "315" in msg
    assert "43%" in msg
    assert "9" in msg


def test_positions_show_expected_profit_and_horizon():
    rep = PositionsReport(
        {"aggressive": [PositionLine("aggressive", "IWM", 3, 292.95, 298.10, 15.45,
                                     target_price=315.0, path_pct=0.43, days_left=9,
                                     horizon_days=14)]},
        15.45, 894.30)
    msg = format_positions(rep)
    assert "🎯" in msg
    assert "315" in msg                   # target price
    assert "+7.5%" in msg                 # (315-292.95)/292.95
    assert "+66$" in msg                  # 3 * (315-292.95) ≈ 66
    assert "9" in msg                     # days left


def test_positions_without_forecast_omit_target_line():
    rep = PositionsReport(
        {"moderate": [PositionLine("moderate", "DIA", 1, 513.06, 513.06, 0.0)]},
        0.0, 513.06)
    msg = format_positions(rep)
    assert "прогноз" not in msg
    assert "🎯" not in msg


def test_positions_show_invested_and_free():
    rep = PositionsReport(
        {"moderate": [PositionLine("moderate", "DIA", 1, 513.06, 513.06, 0.0)]},
        0.0, 513.06, portfolio_cash=4286.94, per_agent_cash={"moderate": 4286.94})
    msg = format_positions(rep)
    assert "вложено" in msg
    assert "из" in msg               # "вложено X из Y" — free is implied by the total
    assert "4,800" in msg            # 513 invested + 4,287 free ≈ 4,800 total


def test_format_retro_reports_forecast_vs_actual():
    msg = format_retro(agent_id="aggressive", symbol="TSLA", quantity=1,
                       entry_price=200.0, exit_price=197.6, target_price=212.0,
                       horizon_days=7, opened_on="2026-06-09", closed_on="2026-06-13",
                       is_short=False)
    assert "TSLA" in msg
    assert "Прогноз" in msg
    assert "По факту" in msg
    assert "🔴" in msg          # losing trade
