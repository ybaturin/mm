from trading.edge.schema import EdgePrediction, MemoryProbe, signal_value


def test_edge_prediction_parses():
    p = EdgePrediction(direction="up", magnitude_pct=3.0, confidence=0.7,
                       rationale="tone")
    assert p.direction == "up"
    assert p.confidence == 0.7


def test_signal_value_signs_by_direction():
    assert signal_value("up", 3.0) == 3.0
    assert signal_value("down", 3.0) == -3.0
    assert signal_value("neutral", 3.0) == 0.0


def test_memory_probe_parses():
    m = MemoryProbe(knows_outcome=True, evidence="I recall the stock jumped")
    assert m.knows_outcome is True
