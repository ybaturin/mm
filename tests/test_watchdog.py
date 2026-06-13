from trading.broker.fake import FakeBroker
from trading.broker.types import Action
from trading.safety.watchdog import Watchdog, flatten, nav


def funded_broker():
    b = FakeBroker(cash=5000.0)
    b.set_price("AAPL", 100.0)
    b.place_market_order("AAPL", Action.BUY, 20)   # spend 2000 -> cash 3000, 20 @ 100
    return b


def test_nav_is_cash_plus_position_value():
    b = funded_broker()
    assert nav(b, {"AAPL": 100.0}) == 5000.0       # 3000 cash + 20*100
    assert nav(b, {"AAPL": 110.0}) == 5200.0       # mark up


def test_watchdog_not_breached_above_floor():
    b = funded_broker()
    wd = Watchdog(starting_nav=5000.0, floor_fraction=0.8)   # floor = 4000
    result = wd.check(b, {"AAPL": 100.0})
    assert result.breached is False
    assert result.nav == 5000.0
    assert result.floor == 4000.0


def test_watchdog_breached_below_floor():
    b = funded_broker()
    wd = Watchdog(starting_nav=5000.0, floor_fraction=0.8)
    # AAPL collapses to 40 -> nav = 3000 + 20*40 = 3800 < 4000
    result = wd.check(b, {"AAPL": 40.0})
    assert result.breached is True
    assert result.nav == 3800.0


def test_flatten_closes_all_positions():
    b = funded_broker()
    b.set_price("AAPL", 90.0)
    fills = flatten(b, {"AAPL": 90.0})
    assert b.positions() == []
    assert len(fills) == 1
    assert fills[0].action is Action.SELL and fills[0].quantity == 20


def test_flatten_buys_back_a_short():
    b = FakeBroker(cash=5000.0)
    b.set_price("TSLA", 200.0)
    b.place_market_order("TSLA", Action.SELL, 5)   # open short
    flatten(b, {"TSLA": 200.0})
    assert b.positions() == []


def test_flatten_cancels_resting_stop_orders():
    b = funded_broker()
    b.place_stop_order("AAPL", Action.SELL, 20, 90.0)   # protective stop sitting at broker
    assert b.stop_orders
    flatten(b, {"AAPL": 90.0})
    assert b.positions() == []
    assert b.stop_orders == []                          # stop cancelled, won't fire post-flatten
