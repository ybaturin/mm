from __future__ import annotations

import sqlite3

from trading.domain import AgentState, Position


class AccountRepository:
    """Live ledger for the virtual sub-accounts. Reads/writes domain objects directly."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save_state(self, state: AgentState) -> None:
        """Upsert the account row and fully replace its positions (snapshot semantics)."""
        self.conn.execute(
            """
            INSERT INTO accounts (agent_id, cash, peak_equity, equity_day_start)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                cash = excluded.cash,
                peak_equity = excluded.peak_equity,
                equity_day_start = excluded.equity_day_start
            """,
            (state.agent_id, state.cash, state.peak_equity, state.equity_day_start),
        )
        self.conn.execute("DELETE FROM positions WHERE agent_id = ?", (state.agent_id,))
        self.conn.executemany(
            "INSERT INTO positions (agent_id, symbol, quantity, avg_price) VALUES (?, ?, ?, ?)",
            [(state.agent_id, p.symbol, p.quantity, p.avg_price) for p in state.positions],
        )
        self.conn.commit()

    def get_state(self, agent_id: str) -> AgentState | None:
        row = self.conn.execute(
            "SELECT cash, peak_equity, equity_day_start FROM accounts WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        pos_rows = self.conn.execute(
            "SELECT symbol, quantity, avg_price FROM positions WHERE agent_id = ? ORDER BY symbol",
            (agent_id,),
        ).fetchall()
        positions = [
            Position(symbol=r["symbol"], quantity=r["quantity"], avg_price=r["avg_price"])
            for r in pos_rows
        ]
        return AgentState(
            agent_id=agent_id,
            cash=row["cash"],
            positions=positions,
            peak_equity=row["peak_equity"],
            equity_day_start=row["equity_day_start"],
        )
