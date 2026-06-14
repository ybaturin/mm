from __future__ import annotations

from trading.edge.documents import EventDocuments
from trading.edge.events import EarningsEvent

PREDICT_SYSTEM = (
    "You are an equity analyst reading the primary sources from a company's quarterly "
    "earnings release. Read the earnings-call transcript closely — pay attention to "
    "management tone, hedging, and changes in guidance language in the Q&A, not just the "
    "headline numbers. Predict the stock's MARKET-ADJUSTED move (return minus SPY) over "
    "the stated horizon. Return only the structured fields. Base your view solely on the "
    "material provided; do not rely on any later knowledge."
)

PROBE_SYSTEM = (
    "You are checking your own knowledge. Answer honestly whether you already know the "
    "actual stock-price outcome that followed this specific earnings report. If you "
    "recall or can infer the outcome from training knowledge, set knows_outcome true."
)


def build_predict_user_prompt(docs: EventDocuments, horizon_days: int) -> str:
    return (
        f"Company: {docs.symbol}\n"
        f"Decision date (you know nothing after this): {docs.decision_date}\n"
        f"Forecast horizon: {horizon_days} trading days, market-adjusted.\n\n"
        f"=== EARNINGS CALL TRANSCRIPT ===\n{docs.transcript}\n\n"
        f"=== PRESS RELEASE (8-K) ===\n{docs.press_release}\n\n"
        f"=== 10-Q MD&A EXCERPT ===\n{docs.mdna}\n"
    )


def build_probe_user_prompt(event: EarningsEvent) -> str:
    return (
        f"Do you already know how {event.symbol} stock moved in the days after its "
        f"earnings report dated {event.report_date}?"
    )
