from __future__ import annotations

import sqlite3


class ThesisStore:
    """The live forecast for each open position: target, horizon, entry, opened date.
    One row per (agent_id, symbol). Written on open, deleted on full close."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, agent_id: str, symbol: str, entry_price: float, target_price: float,
               horizon_days: int, opened_on: str, rationale: str) -> None:
        self.conn.execute(
            """
            INSERT INTO theses
                (agent_id, symbol, entry_price, target_price, horizon_days, opened_on, rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id, symbol) DO UPDATE SET
                entry_price = excluded.entry_price,
                target_price = excluded.target_price,
                horizon_days = excluded.horizon_days,
                opened_on = excluded.opened_on,
                rationale = excluded.rationale
            """,
            (agent_id, symbol, entry_price, target_price, horizon_days, opened_on, rationale),
        )
        self.conn.commit()

    def get(self, agent_id: str, symbol: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM theses WHERE agent_id = ? AND symbol = ?",
            (agent_id, symbol),
        ).fetchone()

    def delete(self, agent_id: str, symbol: str) -> None:
        self.conn.execute(
            "DELETE FROM theses WHERE agent_id = ? AND symbol = ?", (agent_id, symbol))
        self.conn.commit()

    def all_for(self, agent_id: str) -> dict[str, sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM theses WHERE agent_id = ?", (agent_id,)).fetchall()
        return {r["symbol"]: r for r in rows}
