from trading.config import RiskProfile
from trading.data.briefing import Briefing, SymbolBrief
from trading.domain import Intent
from trading.orchestrator.strategy import FakeStrategy


def make_profile(**o):
    base = dict(name="moderate", budget=5000.0, max_position_pct=0.25, min_positions=5,
                allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
                daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
                auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority")
    base.update(o)
    return RiskProfile(**base)


def brief(symbol, price, sma20, held_qty=0, rsi=50.0):
    return SymbolBrief(symbol, price, sma20, sma20, rsi, 0.0,
                       held_qty, 100.0 if held_qty else None)


def briefing(symbols):
    return Briefing("moderate", "2026-06-15", 5000.0, 5000.0, symbols)


def test_opens_long_on_uptrend_when_not_held():
    # price above sma20, not held, rsi not overbought -> open_long
    b = briefing([brief("AAPL", price=160.0, sma20=150.0, held_qty=0, rsi=55.0)])
    trades = FakeStrategy().propose(b, make_profile())
    assert len(trades) == 1
    t = trades[0]
    assert t.symbol == "AAPL" and t.intent is Intent.OPEN_LONG
    assert t.quantity > 0
    assert t.reference_price == 160.0
    assert t.stop_loss_price is not None and t.stop_loss_price < 160.0


def test_skips_overbought():
    b = briefing([brief("AAPL", price=160.0, sma20=150.0, held_qty=0, rsi=80.0)])
    assert FakeStrategy().propose(b, make_profile()) == []


def test_closes_long_on_downtrend_when_held():
    b = briefing([brief("AAPL", price=140.0, sma20=150.0, held_qty=5, rsi=40.0)])
    trades = FakeStrategy().propose(b, make_profile())
    assert len(trades) == 1
    assert trades[0].intent is Intent.CLOSE_LONG
    assert trades[0].quantity == 5
    assert trades[0].stop_loss_price is None


def test_respects_max_trades_per_day():
    symbols = [brief(f"S{i}", price=160.0, sma20=150.0, held_qty=0, rsi=55.0) for i in range(10)]
    trades = FakeStrategy().propose(briefing(symbols), make_profile(max_trades_per_day=4))
    assert len(trades) == 4


def test_no_signal_when_flat():
    # price equals sma20 -> no trend signal -> nothing
    b = briefing([brief("AAPL", price=150.0, sma20=150.0, held_qty=0, rsi=55.0)])
    assert FakeStrategy().propose(b, make_profile()) == []
