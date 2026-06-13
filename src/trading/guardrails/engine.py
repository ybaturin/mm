from __future__ import annotations

from dataclasses import dataclass, field

from trading.config import RiskProfile
from trading.domain import AgentState, Intent, Outcome, TradeProposal
from trading.guardrails import checks

REFERENCE_PRICE_TOLERANCE = 0.05


@dataclass(frozen=True)
class GuardrailDecision:
    outcome: Outcome
    quantity: int                       # final (possibly trimmed) share count
    reasons: list[str] = field(default_factory=list)


class GuardrailsEngine:
    """Deterministic evaluation of a single proposal. Never calls an LLM or network.

    Precedence: any hard violation -> REJECTED. Otherwise sizing is trimmed, then the
    notional decides APPROVED_AUTO vs NEEDS_CONFIRMATION (added in Task 5).
    """

    def evaluate(
        self,
        proposal: TradeProposal,
        state: AgentState,
        profile: RiskProfile,
        prices: dict[str, float],
        trades_today: int,
        universe: set[str] | None = None,
    ) -> GuardrailDecision:
        reasons: list[str] = []

        # 1. Symbol must have a known market price.
        market = prices.get(proposal.symbol)
        if market is None or market <= 0:
            return GuardrailDecision(Outcome.REJECTED, 0,
                                     [f"No market price for {proposal.symbol}"])

        # 1b. Whitelist: only OPEN positions in allowed symbols. Closing trades are
        # always permitted so a holding dropped from the universe can still be exited.
        if (universe is not None and proposal.intent.is_opening
                and proposal.symbol not in universe):
            reasons.append(f"{proposal.symbol} is not in the allowed universe")

        # 2. Daily-loss kill switch (freezes new and closing activity for the day).
        equity_now = state.equity(prices)
        if checks.daily_loss_breached(equity_now, state.equity_day_start,
                                      profile.budget, profile.daily_loss_limit_pct):
            reasons.append("Daily loss limit reached — agent frozen for today")

        # 3. Drawdown kill switch (full suspension pending manual review).
        if checks.drawdown_breached(equity_now, state.peak_equity, profile.max_drawdown_pct):
            reasons.append("Max drawdown reached — agent suspended")

        # 4. Per-day trade count.
        if trades_today >= profile.max_trades_per_day:
            reasons.append("Daily trade limit reached")

        # 5. Shorts permission.
        if proposal.intent == Intent.OPEN_SHORT and not profile.allow_shorts:
            reasons.append("Shorting not allowed for this profile")

        # 6. Reference price sanity.
        if not checks.reference_price_ok(proposal.reference_price, market,
                                         REFERENCE_PRICE_TOLERANCE):
            reasons.append(
                f"Reference price {proposal.reference_price} too far from market {market}")

        # 7. Stop loss validity (opening trades).
        if not checks.stop_loss_ok(proposal.intent, proposal.stop_loss_price, market):
            reasons.append("Missing or invalid stop-loss for opening trade")

        # 8. Holdings sufficiency for closing trades.
        if proposal.intent in (Intent.CLOSE_LONG, Intent.CLOSE_SHORT):
            position = state.position_for(proposal.symbol)
            if not checks.owns_enough_to_close(position, proposal.intent, proposal.quantity):
                reasons.append(f"Does not hold enough {proposal.symbol} to close")

        # 9. Cash sufficiency for opening longs.
        if proposal.intent == Intent.OPEN_LONG:
            if not checks.has_sufficient_cash(state.cash, proposal.quantity, market):
                reasons.append("Insufficient cash for this buy")

        if reasons:
            return GuardrailDecision(Outcome.REJECTED, 0, reasons)

        # Sizing: trim opening trades to the per-position cap. Closing trades keep size.
        quantity = proposal.quantity
        if proposal.intent.is_opening:
            quantity = checks.capped_quantity(
                proposal.quantity, market, profile.max_position_pct, profile.budget)
            if quantity <= 0:
                return GuardrailDecision(
                    Outcome.REJECTED, 0,
                    ["Position size cap leaves zero shares for this price"])

        # Auto-execute small trades; large ones need Telegram confirmation.
        notional = quantity * market
        threshold = min(profile.auto_exec_threshold_usd,
                        profile.auto_exec_threshold_pct * profile.budget)
        if notional > threshold:
            return GuardrailDecision(Outcome.NEEDS_CONFIRMATION, quantity, [])
        return GuardrailDecision(Outcome.APPROVED_AUTO, quantity, [])
