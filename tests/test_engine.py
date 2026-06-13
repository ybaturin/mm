import pytest
from trading.config import RiskProfile
from trading.domain import AgentState, Intent, Outcome, Position, TradeProposal
from trading.guardrails.engine import GuardrailDecision, GuardrailsEngine


def make_profile(**overrides) -> RiskProfile:
    base = dict(
        name="moderate", budget=5000.0, max_position_pct=0.25, min_positions=5,
        allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
        daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
        auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority",
    )
    base.update(overrides)
    return RiskProfile(**base)


def make_state(**overrides) -> AgentState:
    base = dict(
        agent_id="moderate", cash=5000.0, positions=[],
        peak_equity=5000.0, equity_day_start=5000.0,
    )
    base.update(overrides)
    return AgentState(**base)


def open_long(qty=10, price=100.0, stop=90.0) -> TradeProposal:
    return TradeProposal(
        agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_LONG,
        quantity=qty, reference_price=price, stop_loss_price=stop, rationale="x",
    )


def test_reject_unknown_symbol():
    engine = GuardrailsEngine()
    decision = engine.evaluate(open_long(), make_state(), make_profile(),
                               prices={}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("price" in r.lower() for r in decision.reasons)


def test_reject_stale_reference_price():
    engine = GuardrailsEngine()
    proposal = open_long(price=50.0)        # claims 50
    decision = engine.evaluate(proposal, make_state(), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("reference price" in r.lower() for r in decision.reasons)


def test_reject_short_when_profile_disallows():
    engine = GuardrailsEngine()
    short = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.OPEN_SHORT,
                          quantity=5, reference_price=100.0, stop_loss_price=110.0, rationale="x")
    decision = engine.evaluate(short, make_state(), make_profile(allow_shorts=False),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("short" in r.lower() for r in decision.reasons)


def test_reject_missing_stop_on_open():
    engine = GuardrailsEngine()
    no_stop = open_long(stop=None)
    decision = engine.evaluate(no_stop, make_state(), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("stop" in r.lower() for r in decision.reasons)


def test_reject_insufficient_cash():
    engine = GuardrailsEngine()
    decision = engine.evaluate(open_long(qty=10, price=100.0),
                               make_state(cash=400.0), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("cash" in r.lower() for r in decision.reasons)


def test_reject_close_more_than_owned():
    engine = GuardrailsEngine()
    close = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.CLOSE_LONG,
                          quantity=10, reference_price=100.0, stop_loss_price=None, rationale="x")
    state = make_state(positions=[Position(symbol="AAPL", quantity=3, avg_price=90.0)])
    decision = engine.evaluate(close, state, make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("hold" in r.lower() or "own" in r.lower() for r in decision.reasons)


def test_reject_when_daily_loss_breached():
    engine = GuardrailsEngine()
    state = make_state(equity_day_start=5000.0, cash=4700.0)  # equity now 4700, loss 300 > 250
    decision = engine.evaluate(open_long(), state, make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("daily loss" in r.lower() for r in decision.reasons)


def test_reject_when_drawdown_breached():
    engine = GuardrailsEngine()
    state = make_state(peak_equity=6000.0, cash=5000.0)  # equity 5000, dd 16.7% > 15%
    decision = engine.evaluate(open_long(), state, make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("drawdown" in r.lower() for r in decision.reasons)


def test_reject_when_trade_limit_reached():
    engine = GuardrailsEngine()
    decision = engine.evaluate(open_long(), make_state(), make_profile(max_trades_per_day=4),
                               prices={"AAPL": 100.0}, trades_today=4)
    assert decision.outcome is Outcome.REJECTED
    assert any("trade limit" in r.lower() for r in decision.reasons)
