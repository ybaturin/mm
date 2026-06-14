from trading.persistence.db import connect
from trading.edge.store import init_edge_db, EdgeRepository
from trading.edge.report import build_report


def _repo_with_rows():
    conn = connect(":memory:")
    init_edge_db(conn)
    repo = EdgeRepository(conn)
    # Two scored, blind rows where the up-call won and the down-call won.
    a = repo.record(symbol="A", report_date="2026-02-01", decision_date="2026-02-03",
                    horizon_days=5, direction="up", magnitude_pct=2.0, confidence=0.8,
                    rationale="", knows_outcome=False, eps_actual=5.0,
                    eps_consensus=4.0, model="m")
    b = repo.record(symbol="B", report_date="2026-02-01", decision_date="2026-02-03",
                    horizon_days=5, direction="down", magnitude_pct=2.0, confidence=0.7,
                    rationale="", knows_outcome=False, eps_actual=3.0,
                    eps_consensus=4.0, model="m")
    repo.set_realized(a, 0.03)
    repo.set_realized(b, -0.02)
    return repo


def test_report_contains_core_sections():
    report = build_report(_repo_with_rows().scored())
    assert "Sample size: 2" in report
    assert "Information coefficient" in report
    assert "Long-short" in report
    assert "Hit rate" in report
    assert "dumb PEAD" in report


def test_report_handles_empty_sample():
    conn = connect(":memory:")
    init_edge_db(conn)
    report = build_report(EdgeRepository(conn).scored())
    assert "Sample size: 0" in report
    assert "insufficient data" in report.lower()
