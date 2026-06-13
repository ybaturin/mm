from __future__ import annotations

from typing import Callable

from trading.broker.base import Broker
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
) -> AgentState:
    """Run one agent's full daily cycle. The keystone that connects every component.

    briefing -> strategy proposes -> guardrails evaluate -> execute approved -> record.
    `confirm` decides NEEDS_CONFIRMATION trades (defaults to auto-approve, as in simulation).
    """
    if confirm is None:
        confirm = lambda proposal, decision: True  # noqa: E731
    engine = GuardrailsEngine()

    held = [p.symbol for p in broker.positions()]
    symbols = sorted(set(universe) | set(held))
    prices = {s: source.latest_price(s) for s in symbols}

    def equity_now() -> float:
        return broker.cash() + sum(p.quantity * prices[p.symbol] for p in broker.positions())

    prev = accounts.get_state(agent_id)
    start_equity = equity_now()
    peak = max(prev.peak_equity, start_equity) if prev else start_equity

    state = _state_from_broker(agent_id, broker, peak, start_equity)
    briefing = build_briefing(state, universe, source, as_of_date)
    proposals = strategy.propose(briefing, profile)

    trades_today = 0
    for proposal in proposals:
        decision = engine.evaluate(proposal, state, profile, prices, trades_today)
        decision_id = journal.record_decision(ts, proposal, decision)

        if decision.outcome is Outcome.REJECTED:
            continue
        if decision.outcome is Outcome.NEEDS_CONFIRMATION and not confirm(proposal, decision):
            continue

        if panel is not None:
            result = panel.review(proposal, briefing, profile.veto_rule)
            if result.blocked:
                journal.record_veto(ts, agent_id, proposal, decision.quantity, result.verdicts)
                continue

        fill = broker.place_market_order(
            proposal.symbol, action_for(proposal.intent), decision.quantity)
        journal.record_fill(ts, agent_id, proposal.symbol, proposal.intent,
                            fill.quantity, fill.price, decision_id)
        trades_today += 1
        state = _state_from_broker(agent_id, broker, peak, start_equity)

    final_equity = equity_now()
    peak = max(peak, final_equity)
    final_state = _state_from_broker(agent_id, broker, peak, start_equity)
    accounts.save_state(final_state)
    journal.record_equity_snapshot(agent_id, as_of_date, final_equity)
    return final_state
