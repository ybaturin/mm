from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    agent_id          TEXT PRIMARY KEY,
    cash              REAL NOT NULL,
    peak_equity       REAL NOT NULL,
    equity_day_start  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    agent_id   TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    quantity   INTEGER NOT NULL,   -- signed: + long, - short
    avg_price  REAL NOT NULL,
    PRIMARY KEY (agent_id, symbol),
    FOREIGN KEY (agent_id) REFERENCES accounts(agent_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,        -- ISO 8601, supplied by caller
    agent_id         TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    intent           TEXT NOT NULL,
    proposed_qty     INTEGER NOT NULL,
    reference_price  REAL NOT NULL,
    stop_loss_price  REAL,
    rationale        TEXT NOT NULL,
    outcome          TEXT NOT NULL,        -- Outcome value
    final_qty        INTEGER NOT NULL,
    reasons          TEXT NOT NULL         -- JSON array of strings
);

CREATE TABLE IF NOT EXISTS fills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    intent       TEXT NOT NULL,
    quantity     INTEGER NOT NULL,         -- shares actually filled (unsigned)
    price        REAL NOT NULL,
    decision_id  INTEGER,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    agent_id  TEXT NOT NULL,
    date      TEXT NOT NULL,               -- YYYY-MM-DD
    equity    REAL NOT NULL,
    PRIMARY KEY (agent_id, date)
);

CREATE TABLE IF NOT EXISTS vetoes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    intent       TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    verdicts     TEXT NOT NULL,      -- JSON: [{role, veto, reason}, ...]
    entry_price  REAL                -- market price at veto time, for counterfactual P&L
);

CREATE TABLE IF NOT EXISTS freezes (
    scope   TEXT PRIMARY KEY,    -- an agent_id, or 'GLOBAL'
    reason  TEXT NOT NULL,
    ts      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_state (
    scope   TEXT PRIMARY KEY,    -- 'GLOBAL' (один прогон за раз)
    active  INTEGER NOT NULL,    -- 1 = идёт ежедневный цикл
    since   TEXT                 -- ISO 8601 wall-clock момент захвата лока
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
