from trading.edge.events import EarningsEvent
from trading.edge.documents import EventDocuments
from trading.edge.schema import EdgePrediction, MemoryProbe
from trading.edge.predict import EdgePredictor


class _Parsed:
    def __init__(self, obj):
        self.parsed_output = obj


class _FakeMessages:
    def __init__(self, obj):
        self._obj = obj
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _Parsed(self._obj)


class _FakeClient:
    def __init__(self, obj):
        self.messages = _FakeMessages(obj)


def test_predict_returns_parsed_prediction():
    pred = EdgePrediction(direction="up", magnitude_pct=2.0, confidence=0.6, rationale="x")
    predictor = EdgePredictor(client=_FakeClient(pred), model="m")
    docs = EventDocuments("NVDA", "2026-02-23", transcript="t")
    out = predictor.predict(docs, horizon_days=5)
    assert out.direction == "up"
    assert predictor.client.messages.calls[0]["output_format"] is EdgePrediction


def test_memory_probe_returns_parsed_probe():
    probe = MemoryProbe(knows_outcome=True, evidence="recall")
    predictor = EdgePredictor(client=_FakeClient(probe), model="m")
    ev = EarningsEvent("NVDA", "2026-02-21", "2026-02-23")
    out = predictor.memory_probe(ev)
    assert out.knows_outcome is True
