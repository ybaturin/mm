from trading.data.bars import Bar
from trading.edge.events import EarningsEvent
from trading.edge.documents import EventDocuments, FakeDocumentSource
from trading.edge.schema import EdgePrediction, MemoryProbe
from trading.edge.store import init_edge_db, EdgeRepository
from trading.persistence.db import connect
from trading.edge.run import run_measurement


class _FakeSource:
    """MarketDataSource returning a fixed upward ramp for any symbol except SPY (flat)."""

    def history(self, symbol, days, as_of_date=None):
        base = [Bar(f"2026-02-{d:02d}", 100, 100, 100,
                    100.0 if symbol == "SPY" else 100.0 + d, 0)
                for d in range(23, 23 + 12)]
        return base

    def latest_price(self, symbol, as_of_date=None):
        return self.history(symbol, 1)[-1].close


class _FakePredictor:
    def __init__(self):
        self.model = "fake"

    def memory_probe(self, event):
        # The model "remembers" SKIP only.
        return MemoryProbe(knows_outcome=event.symbol == "SKIP", evidence="")

    def predict(self, docs, horizon_days):
        return EdgePrediction(direction="up", magnitude_pct=2.0, confidence=0.7,
                              rationale="up")


def test_run_skips_remembered_events_and_scores_the_rest():
    conn = connect(":memory:")
    init_edge_db(conn)
    repo = EdgeRepository(conn)
    events = [
        EarningsEvent("NVDA", "2026-02-21", "2026-02-23", 5.0, 4.0),
        EarningsEvent("SKIP", "2026-02-21", "2026-02-23", 5.0, 4.0),
    ]
    docs = {e.symbol: EventDocuments(e.symbol, e.decision_date, transcript="t")
            for e in events}
    report = run_measurement(
        events=events, source=_FakeSource(),
        doc_source=FakeDocumentSource(docs), predictor=_FakePredictor(),
        repo=repo, horizon_days=5,
    )
    rows = repo.all()
    # Both recorded, but SKIP flagged knows_outcome -> excluded from scored().
    assert len(rows) == 2
    assert len(repo.scored()) == 1
    assert repo.scored()[0]["symbol"] == "NVDA"
    assert repo.scored()[0]["realized_return"] is not None
    assert "EDGE MEASURER REPORT" in report
