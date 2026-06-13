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


def test_reject_opening_symbol_outside_universe():
    engine = GuardrailsEngine()
    # symbol has a price but is NOT in the allowed universe -> opening is rejected
    decision = engine.evaluate(open_long(), make_state(), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0,
                               universe={"MSFT", "NVDA"})
    assert decision.outcome is Outcome.REJECTED
    assert any("universe" in r.lower() for r in decision.reasons)


def test_allow_closing_symbol_outside_universe():
    engine = GuardrailsEngine()
    close = TradeProposal(agent_id="moderate", symbol="AAPL", intent=Intent.CLOSE_LONG,
                          quantity=3, reference_price=100.0, stop_loss_price=None, rationale="x")
    state = make_state(positions=[Position(symbol="AAPL", quantity=3, avg_price=90.0)])
    decision = engine.evaluate(close, state, make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0,
                               universe={"MSFT"})        # AAPL dropped from universe but held
    assert decision.outcome is not Outcome.REJECTED      # must still be allowed to exit


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


def test_reject_stop_loss_looser_than_profile_limit():
    engine = GuardrailsEngine()
    # profile limit 10%; a stop 20% below market permits too large a loss
    loose = open_long(price=100.0, stop=80.0)
    decision = engine.evaluate(loose, make_state(), make_profile(stop_loss_pct=0.10),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("stop-loss" in r.lower() and "limit" in r.lower() for r in decision.reasons)


def test_accept_stop_loss_within_profile_limit():
    engine = GuardrailsEngine()
    tight = open_long(qty=3, price=100.0, stop=95.0)   # 5% loss < 10% limit
    decision = engine.evaluate(tight, make_state(), make_profile(stop_loss_pct=0.10),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is not Outcome.REJECTED


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


def test_oversized_long_is_trimmed_and_approved():
    engine = GuardrailsEngine()
    # max_position_pct 0.25 * 5000 = 1250 -> at 100 -> 12 shares max
    proposal = open_long(qty=50, price=100.0)
    decision = engine.evaluate(proposal, make_state(cash=5000.0), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.quantity == 12
    # 12 * 100 = 1200 notional > 500 threshold -> confirmation
    assert decision.outcome is Outcome.NEEDS_CONFIRMATION


def test_trimmed_to_zero_is_rejected():
    engine = GuardrailsEngine()
    # price above max notional for even one share: 0.25*5000=1250 cap, price 2000 -> 0 shares
    proposal = TradeProposal(agent_id="moderate", symbol="BRK", intent=Intent.OPEN_LONG,
                             quantity=1, reference_price=2000.0, stop_loss_price=1800.0, rationale="x")
    decision = engine.evaluate(proposal, make_state(cash=5000.0), make_profile(),
                               prices={"BRK": 2000.0}, trades_today=0)
    assert decision.outcome is Outcome.REJECTED
    assert any("position size" in r.lower() for r in decision.reasons)


def test_small_trade_is_auto_approved():
    engine = GuardrailsEngine()
    # 3 shares * 100 = 300 notional < 500 threshold -> auto
    proposal = open_long(qty=3, price=100.0)
    decision = engine.evaluate(proposal, make_state(cash=5000.0), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.APPROVED_AUTO
    assert decision.quantity == 3


def test_confirmation_threshold_uses_min_of_usd_and_pct():
    engine = GuardrailsEngine()
    # pct threshold 0.25 * 5000 = 1250; usd threshold 500 -> min is 500
    # 6 shares * 100 = 600 > 500 -> confirmation
    proposal = open_long(qty=6, price=100.0)
    decision = engine.evaluate(proposal, make_state(cash=5000.0), make_profile(),
                               prices={"AAPL": 100.0}, trades_today=0)
    assert decision.outcome is Outcome.NEEDS_CONFIRMATION
