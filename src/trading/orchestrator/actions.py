from __future__ import annotations

from trading.broker.types import Action
from trading.domain import Intent

_BUYS = {Intent.OPEN_LONG, Intent.CLOSE_SHORT}


def action_for(intent: Intent) -> Action:
    """Map a trade Intent to the broker order side.

    Opening a long or covering a short buys; closing a long or opening a short sells.
    """
    return Action.BUY if intent in _BUYS else Action.SELL
