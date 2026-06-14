import sqlite3

from trading.persistence.schema import init_db
from trading.persistence.theses import ThesisStore


def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return ThesisStore(conn)


def test_upsert_then_get():
    s = _store()
    s.upsert("aggressive", "AAPL", entry_price=185.0, target_price=200.0,
             horizon_days=14, opened_on="2026-06-14", rationale="rebound")
    row = s.get("aggressive", "AAPL")
    assert row["target_price"] == 200.0
    assert row["entry_price"] == 185.0
    assert row["opened_on"] == "2026-06-14"


def test_upsert_overwrites_existing():
    s = _store()
    s.upsert("aggressive", "AAPL", 185.0, 200.0, 14, "2026-06-14", "v1")
    s.upsert("aggressive", "AAPL", 190.0, 205.0, 10, "2026-06-15", "v2")
    row = s.get("aggressive", "AAPL")
    assert row["entry_price"] == 190.0
    assert row["target_price"] == 205.0
    assert row["rationale"] == "v2"


def test_delete_removes_row():
    s = _store()
    s.upsert("aggressive", "AAPL", 185.0, 200.0, 14, "2026-06-14", "x")
    s.delete("aggressive", "AAPL")
    assert s.get("aggressive", "AAPL") is None


def test_all_for_returns_symbol_map():
    s = _store()
    s.upsert("aggressive", "AAPL", 185.0, 200.0, 14, "2026-06-14", "x")
    s.upsert("aggressive", "IWM", 290.0, 315.0, 9, "2026-06-14", "y")
    s.upsert("moderate", "DIA", 500.0, 540.0, 20, "2026-06-14", "z")
    by_symbol = s.all_for("aggressive")
    assert set(by_symbol) == {"AAPL", "IWM"}
