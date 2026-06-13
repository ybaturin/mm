from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

GLOBAL = "GLOBAL"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str) -> datetime:
    # Принимаем и "...Z", и "...+00:00".
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class RunLock:
    """Cooperative lock so the command daemon pauses its getUpdates polling while a
    daily cycle is running (only one process may consume Telegram updates at a time)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def acquire(self, scope: str = GLOBAL, now_iso: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO run_state (scope, active, since) VALUES (?, 1, ?)
            ON CONFLICT(scope) DO UPDATE SET active = 1, since = excluded.since
            """,
            (scope, now_iso or _now_iso()),
        )
        self.conn.commit()

    def release(self, scope: str = GLOBAL) -> None:
        self.conn.execute(
            "UPDATE run_state SET active = 0 WHERE scope = ?", (scope,))
        self.conn.commit()

    def is_active(self, now_iso: str | None = None, scope: str = GLOBAL,
                  stale_after_s: float = 900.0) -> bool:
        row = self.conn.execute(
            "SELECT active, since FROM run_state WHERE scope = ?", (scope,)).fetchone()
        if row is None or not row["active"]:
            return False
        now = _parse(now_iso) if now_iso else datetime.now(timezone.utc)
        age = (now - _parse(row["since"])).total_seconds()
        return age < stale_after_s
