from __future__ import annotations

import sqlite3

EDGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS edge_predictions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    report_date      TEXT NOT NULL,        -- YYYY-MM-DD results released
    decision_date    TEXT NOT NULL,        -- YYYY-MM-DD point-in-time boundary
    horizon_days     INTEGER NOT NULL,
    direction        TEXT NOT NULL,        -- up | down | neutral
    magnitude_pct    REAL NOT NULL,        -- expected absolute move vs market, %
    confidence       REAL NOT NULL,        -- 0..1
    rationale        TEXT NOT NULL,
    knows_outcome    INTEGER NOT NULL,     -- 1 if memory-probe says model knows the future
    eps_actual       REAL,
    eps_consensus    REAL,
    model            TEXT NOT NULL,
    realized_return  REAL                  -- market-adjusted, filled in after the horizon
);
"""


def init_edge_db(conn: sqlite3.Connection) -> None:
    conn.executescript(EDGE_SCHEMA_SQL)
    conn.commit()


class EdgeRepository:
    """Append-only journal of edge predictions and their realized outcomes."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, *, symbol: str, report_date: str, decision_date: str,
               horizon_days: int, direction: str, magnitude_pct: float,
               confidence: float, rationale: str, knows_outcome: bool,
               eps_actual: float | None, eps_consensus: float | None,
               model: str) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO edge_predictions (
                symbol, report_date, decision_date, horizon_days, direction,
                magnitude_pct, confidence, rationale, knows_outcome,
                eps_actual, eps_consensus, model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, report_date, decision_date, horizon_days, direction,
             magnitude_pct, confidence, rationale, int(knows_outcome),
             eps_actual, eps_consensus, model),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_realized(self, prediction_id: int, realized_return: float) -> None:
        self.conn.execute(
            "UPDATE edge_predictions SET realized_return = ? WHERE id = ?",
            (realized_return, prediction_id),
        )
        self.conn.commit()

    def exists(self, symbol: str, report_date: str) -> bool:
        """True if this (symbol, report_date) was already recorded — lets a run resume
        across days (free-tier rate limits) without inserting duplicates."""
        row = self.conn.execute(
            "SELECT 1 FROM edge_predictions WHERE symbol = ? AND report_date = ? LIMIT 1",
            (symbol, report_date),
        ).fetchone()
        return row is not None

    def all(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM edge_predictions ORDER BY id").fetchall()

    def scored(self) -> list[sqlite3.Row]:
        """Rows usable for metrics: realized known AND the model was blind to outcome."""
        return self.conn.execute(
            "SELECT * FROM edge_predictions "
            "WHERE realized_return IS NOT NULL AND knows_outcome = 0 "
            "ORDER BY id"
        ).fetchall()
