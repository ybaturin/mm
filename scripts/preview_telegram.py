"""Send one of each redesigned Telegram message to your chat, on sample data.

Visual smoke test for the analytics redesign: no Claude, no broker, no blocking
confirmation — just renders every message type and posts it so you can eyeball the
HTML formatting, grouping, colors and tables in the real Telegram client.

Run where the bot credentials live (locally or on the VPS):

    TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... uv run python scripts/preview_telegram.py
"""
from __future__ import annotations

from trading.domain import Intent, Outcome, TradeProposal
from trading.guardrails.engine import GuardrailDecision
from trading.reporting.format import (
    format_confirmation, format_pnl_report, format_positions, format_retro, format_trades,
)
from trading.reporting.queries import (
    PnlLine, PnlReport, PositionLine, PositionsReport, TradeLine, TradesReport,
)
from trading.reporting.telegram import TelegramNotifier


def main() -> None:
    n = TelegramNotifier()

    proposal = TradeProposal(
        "aggressive", "AAPL", Intent.OPEN_LONG, 12, 185.0, 176.0,
        "перепродана, жду отскок к средней цене", target_price=200.0, horizon_days=14)
    n.notify("👀 <b>Превью оформления</b> — тестовые данные, реальных сделок нет")
    n.notify(format_confirmation(proposal, GuardrailDecision(Outcome.NEEDS_CONFIRMATION, 12, [])))

    n.notify(format_trades(TradesReport([
        TradeLine("2026-06-14T13:00:00Z", "aggressive", "open_long", "IWM", 3, 292.95),
        TradeLine("2026-06-13T13:00:00Z", "aggressive", "close_long", "TSLA", 1, 406.43),
        TradeLine("2026-06-14T13:00:00Z", "moderate", "open_long", "DIA", 1, 513.06),
        TradeLine("2026-06-14T13:00:00Z", "moderate", "close_long", "AAPL", 2, 291.13),
    ])))

    n.notify(format_pnl_report(PnlReport(
        "week",
        [PnlLine("aggressive", 13500.0, 13080.0, -420.0, -0.0311),
         PnlLine("moderate", 40200.0, 41020.0, 820.0, 0.0204)],
        53700.0, 54100.0, 400.0, 0.0074, benchmark_pct=0.014)))

    n.notify(format_positions(PositionsReport(
        {"aggressive": [PositionLine("aggressive", "IWM", 3, 292.95, 298.10, 15.45,
                                     target_price=315.0, path_pct=0.43, days_left=9)],
         "moderate": [PositionLine("moderate", "DIA", 1, 513.06, 509.0, -4.06,
                                   target_price=540.0, path_pct=0.0, days_left=18)]},
        11.39, 1316.10)))

    n.notify(format_retro("aggressive", "TSLA", 1, 200.0, 197.6, 212.0, 7,
                          "2026-06-09", "2026-06-13", is_short=False))

    print("Sent 6 preview messages.")


if __name__ == "__main__":
    main()
