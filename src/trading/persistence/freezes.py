from __future__ import annotations

import sqlite3

GLOBAL = "GLOBAL"


class FreezeStore:
    """Records which scopes (an agent_id or GLOBAL) are halted. Survives restarts."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def freeze(self, scope: str, reason: str, ts: str) -> None:
        self.conn.execute(
            """
            INSERT INTO freezes (scope, reason, ts) VALUES (?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET reason = excluded.reason, ts = excluded.ts
            """,
            (scope, reason, ts),
        )
        self.conn.commit()

    def unfreeze(self, scope: str) -> None:
        self.conn.execute("DELETE FROM freezes WHERE scope = ?", (scope,))
        self.conn.commit()

    def is_frozen(self, scope: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM freezes WHERE scope = ?", (scope,)).fetchone()
        return row is not None

    def reason(self, scope: str) -> str | None:
        row = self.conn.execute(
            "SELECT reason FROM freezes WHERE scope = ?", (scope,)).fetchone()
        return row["reason"] if row else None

    def frozen_scopes(self) -> list[str]:
        rows = self.conn.execute("SELECT scope FROM freezes ORDER BY scope").fetchall()
        return [r["scope"] for r in rows]
