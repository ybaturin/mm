from __future__ import annotations

import sqlite3


def connect(path: str) -> sqlite3.Connection:
    """Open a SQLite connection with dict-like rows and FK enforcement."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
