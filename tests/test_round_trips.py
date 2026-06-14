from trading.analysis.round_trips import RoundTrip, compute_round_trips


def fill(ts, symbol, intent, quantity, price, decision_id=None):
    return {"ts": ts, "symbol": symbol, "intent": intent,
            "quantity": quantity, "price": price, "decision_id": decision_id}


def test_long_round_trip_profit_and_rationale():
    fills = [
        fill("2026-06-01T13:30:00Z", "AAPL", "open_long", 10, 100.0, decision_id=1),
        fill("2026-06-05T13:30:00Z", "AAPL", "close_long", 10, 110.0),
    ]
    trips = compute_round_trips(fills, {1: "momentum above sma20"})
    assert len(trips) == 1
    t = trips[0]
    assert t == RoundTrip(symbol="AAPL", quantity=10, entry_date="2026-06-01",
                          entry_price=100.0, exit_date="2026-06-05", exit_price=110.0,
                          realized_pnl=100.0, realized_pct=0.10,
                          rationale="momentum above sma20")


def test_short_round_trip_profit_when_price_falls():
    fills = [
        fill("2026-06-01T13:30:00Z", "TSLA", "open_short", 5, 200.0, decision_id=7),
        fill("2026-06-03T13:30:00Z", "TSLA", "close_short", 5, 180.0),
    ]
    trips = compute_round_trips(fills, {7: "overbought"})
    assert trips[0].realized_pnl == 100.0          # (200 - 180) * 5
    assert trips[0].realized_pct == 0.10


def test_partial_close_fifo_matching():
    fills = [
        fill("2026-06-01T13:30:00Z", "AAPL", "open_long", 10, 100.0, decision_id=1),
        fill("2026-06-02T13:30:00Z", "AAPL", "open_long", 10, 120.0, decision_id=2),
        fill("2026-06-05T13:30:00Z", "AAPL", "close_long", 15, 130.0),
    ]
    trips = compute_round_trips(fills, {1: "first", 2: "second"})
    # FIFO: 10 @100 fully closed, then 5 @120 closed
    assert len(trips) == 2
    assert (trips[0].quantity, trips[0].entry_price, trips[0].rationale) == (10, 100.0, "first")
    assert (trips[1].quantity, trips[1].entry_price, trips[1].rationale) == (5, 120.0, "second")
    assert trips[1].realized_pnl == 50.0           # (130 - 120) * 5


def test_open_position_produces_no_round_trip():
    fills = [fill("2026-06-01T13:30:00Z", "AAPL", "open_long", 10, 100.0, decision_id=1)]
    assert compute_round_trips(fills, {1: "x"}) == []
