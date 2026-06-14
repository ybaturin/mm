from trading.persistence.db import connect
from trading.edge.store import init_edge_db, EdgeRepository


def _repo():
    conn = connect(":memory:")
    init_edge_db(conn)
    return EdgeRepository(conn)


def test_record_and_fetch_roundtrip():
    repo = _repo()
    rid = repo.record(
        symbol="NVDA", report_date="2026-02-21", decision_date="2026-02-23",
        horizon_days=5, direction="up", magnitude_pct=3.0, confidence=0.7,
        rationale="confident CFO tone", knows_outcome=False,
        eps_actual=5.1, eps_consensus=4.6, model="claude-opus-4-8",
    )
    rows = repo.all()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["direction"] == "up"
    assert rows[0]["realized_return"] is None
    assert rid == rows[0]["id"]


def test_set_realized_and_scored_filter():
    repo = _repo()
    a = repo.record(symbol="A", report_date="2026-02-01", decision_date="2026-02-03",
                    horizon_days=5, direction="up", magnitude_pct=2.0, confidence=0.6,
                    rationale="", knows_outcome=False, eps_actual=None,
                    eps_consensus=None, model="m")
    repo.record(symbol="B", report_date="2026-02-01", decision_date="2026-02-03",
                horizon_days=5, direction="down", magnitude_pct=1.0, confidence=0.5,
                rationale="", knows_outcome=True, eps_actual=None,
                eps_consensus=None, model="m")
    repo.set_realized(a, 0.012)
    scored = repo.scored()
    # Only the row with a realized return AND knows_outcome == False qualifies.
    assert [r["symbol"] for r in scored] == ["A"]
    assert scored[0]["realized_return"] == 0.012
