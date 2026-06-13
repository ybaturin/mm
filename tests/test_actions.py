from trading.broker.types import Action
from trading.domain import Intent
from trading.orchestrator.actions import action_for


def test_open_long_and_close_short_are_buys():
    assert action_for(Intent.OPEN_LONG) is Action.BUY
    assert action_for(Intent.CLOSE_SHORT) is Action.BUY


def test_close_long_and_open_short_are_sells():
    assert action_for(Intent.CLOSE_LONG) is Action.SELL
    assert action_for(Intent.OPEN_SHORT) is Action.SELL
