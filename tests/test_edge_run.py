from datetime import date, timedelta

from trading.data.bars import Bar
from trading.edge.events import EarningsEvent
from trading.edge.documents import EventDocuments, FakeDocumentSource
from trading.edge.schema import EdgePrediction, MemoryProbe
from trading.edge.store import init_edge_db, EdgeRepository
from trading.persistence.db import connect
from trading.edge.run import run_measurement


def _fake_fetch_window(symbol, start_date, end_date):
    """Upward ramp for any symbol except SPY (flat) -> positive market-adjusted move."""
    d0 = date.fromisoformat(start_date)
    bars = []
    for i in range(20):
        d = (d0 + timedelta(days=i)).isoformat()
        close = 100.0 if symbol == "SPY" else 100.0 + i
        bars.append(Bar(d, close, close, close, close, 0))
    return bars


class _FakePredictor:
    def __init__(self):
        self.model = "fake"

    def memory_probe(self, event):
        return MemoryProbe(knows_outcome=event.symbol == "SKIP", evidence="")

    def predict(self, docs, horizon_days):
        return EdgePrediction(direction="up", magnitude_pct=2.0, confidence=0.7,
                              rationale="up")


def _events():
    return [
        EarningsEvent("NVDA", "2026-02-21", "2026-02-23", 5.0, 4.0),
        EarningsEvent("SKIP", "2026-02-21", "2026-02-23", 5.0, 4.0),
    ]


def _docs(events):
    return FakeDocumentSource(
        {e.symbol: EventDocuments(e.symbol, e.decision_date, transcript="t")
         for e in events})


def test_run_skips_remembered_events_and_scores_the_rest():
    conn = connect(":memory:")
    init_edge_db(conn)
    repo = EdgeRepository(conn)
    events = _events()
    report = run_measurement(
        events=events, doc_source=_docs(events), predictor=_FakePredictor(),
        repo=repo, horizon_days=5, fetch_window=_fake_fetch_window,
    )
    rows = repo.all()
    # Both recorded, but SKIP flagged knows_outcome -> excluded from scored().
    assert len(rows) == 2
    assert len(repo.scored()) == 1
    assert repo.scored()[0]["symbol"] == "NVDA"
    assert repo.scored()[0]["realized_return"] is not None
    assert "EDGE MEASURER REPORT" in report


def test_run_is_idempotent_across_reruns():
    conn = connect(":memory:")
    init_edge_db(conn)
    repo = EdgeRepository(conn)
    events = _events()
    for _ in range(2):
        run_measurement(events=events, doc_source=_docs(events),
                        predictor=_FakePredictor(), repo=repo, horizon_days=5,
                        fetch_window=_fake_fetch_window)
    assert len(repo.all()) == 2   # second run inserts no duplicates


def test_run_skips_events_without_transcript():
    conn = connect(":memory:")
    init_edge_db(conn)
    repo = EdgeRepository(conn)
    ev = EarningsEvent("NODOC", "2026-02-21", "2026-02-23", 5.0, 4.0)
    docs = FakeDocumentSource({"NODOC": EventDocuments("NODOC", "2026-02-23",
                                                       transcript="   ")})
    run_measurement(events=[ev], doc_source=docs, predictor=_FakePredictor(),
                    repo=repo, horizon_days=5, fetch_window=_fake_fetch_window)
    assert repo.all() == []   # empty transcript -> not recorded
