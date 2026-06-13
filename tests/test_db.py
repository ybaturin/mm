from trading.persistence.db import connect
from trading.persistence.schema import init_db


def test_init_db_creates_all_tables(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert names == {"accounts", "positions", "decisions", "fills", "equity_snapshots"}


def test_init_db_is_idempotent(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    init_db(conn)  # second call must not raise
    count = conn.execute(
        "SELECT count(*) AS c FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()["c"]
    assert count == 5


def test_connection_uses_row_factory(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    row = conn.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1
