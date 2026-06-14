from __future__ import annotations

from typing import Callable

from trading.broker.base import Broker
from trading.broker.types import Action
from trading.config import RiskProfile
from trading.data.bars import MarketDataSource
from trading.data.briefing import build_briefing
from trading.domain import AgentState, Outcome, TradeProposal
from trading.guardrails.engine import GuardrailDecision, GuardrailsEngine
from trading.orchestrator.actions import action_for
from trading.orchestrator.strategy import Strategy
from trading.persistence.accounts import AccountRepository
from trading.persistence.journal import JournalRepository

ConfirmFn = Callable[[TradeProposal, GuardrailDecision], bool]


def _state_from_broker(agent_id: str, broker: Broker, peak_equity: float,
                       equity_day_start: float) -> AgentState:
    return AgentState(
        agent_id=agent_id, cash=broker.cash(), positions=broker.positions(),
        peak_equity=peak_equity, equity_day_start=equity_day_start,
    )


def run_cycle(
    agent_id: str,
    profile: RiskProfile,
    broker: Broker,
    source: MarketDataSource,
    accounts: AccountRepository,
    journal: JournalRepository,
    strategy: Strategy,
    universe: list[str],
    as_of_date: str,
    ts: str,
    confirm: ConfirmFn | None = None,
    panel=None,
    notifier=None,
    news_source=None,
) -> AgentState:
    """Run one agent's full daily cycle. The keystone that connects every component.

    briefing -> strategy proposes -> guardrails evaluate -> execute approved -> record.
    `confirm` decides NEEDS_CONFIRMATION trades (defaults to auto-approve, as in simulation).
    """
    if confirm is None:
        # Fail-safe default: an unconfirmed large trade is NOT executed. Callers that
        # want unattended auto-approval (e.g. offline simulation) must opt in explicitly.
        confirm = lambda proposal, decision: False  # noqa: E731
    engine = GuardrailsEngine()

    held = [p.symbol for p in broker.positions()]
    symbols = sorted(set(universe) | set(held))
    prices = {s: source.latest_price(s, as_of_date=as_of_date) for s in symbols}

    def equity_now() -> float:
        return broker.cash() + sum(p.quantity * prices[p.symbol] for p in broker.positions())

    prev = accounts.get_state(agent_id)
    start_equity = equity_now()
    peak = max(prev.peak_equity, start_equity) if prev else start_equity

    state = _state_from_broker(agent_id, broker, peak, start_equity)
    briefing = build_briefing(state, universe, source, as_of_date,
                              journal=journal, news_source=news_source)
    proposals = strategy.propose(briefing, profile)

    trades_today = 0
    for proposal in proposals:
        decision = engine.evaluate(proposal, state, profile, prices, trades_today,
                                   universe=set(universe))
        decision_id = journal.record_decision(ts, proposal, decision)

        if decision.outcome is Outcome.REJECTED:
            continue
        if decision.outcome is Outcome.NEEDS_CONFIRMATION and not confirm(proposal, decision):
            journal.set_decision_outcome(decision_id, "declined")   # you rejected it in Telegram
            continue

        if panel is not None:
            result = panel.review(proposal, briefing, profile.veto_rule)
            if result.blocked:
                journal.record_veto(ts, agent_id, proposal, decision.quantity,
                                    result.verdicts, entry_price=prices.get(proposal.symbol))
                continue

        entry_action = action_for(proposal.intent)
        fill = broker.place_market_order(proposal.symbol, entry_action, decision.quantity)
        journal.record_fill(ts, agent_id, proposal.symbol, proposal.intent,
                            fill.quantity, fill.price, decision_id)
        if notifier is not None:
            from trading.reporting.format import format_fill
            notifier.notify(format_fill(agent_id, fill))

        # Transmit the protective stop to the broker so the position is guarded
        # between daily runs — not just journaled. Guardrails guarantee a valid
        # stop_loss_price on opening trades; the stop trades the opposite side.
        if proposal.intent.is_opening and proposal.stop_loss_price is not None:
            stop_action = Action.SELL if entry_action is Action.BUY else Action.BUY
            broker.place_stop_order(
                proposal.symbol, stop_action, fill.quantity, proposal.stop_loss_price)

        trades_today += 1
        state = _state_from_broker(agent_id, broker, peak, start_equity)

    final_equity = equity_now()
    peak = max(peak, final_equity)
    final_state = _state_from_broker(agent_id, broker, peak, start_equity)
    accounts.save_state(final_state)
    journal.record_equity_snapshot(agent_id, as_of_date, final_equity)
    return final_state
