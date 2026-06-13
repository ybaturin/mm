from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from trading.domain import Intent, TradeProposal
from trading.guardrails.engine import GuardrailDecision


class JournalRepository:
    """Append-only history: decisions, fills, equity snapshots."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record_decision(
        self, ts: str, proposal: TradeProposal, decision: GuardrailDecision
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO decisions (
                ts, agent_id, symbol, intent, proposed_qty, reference_price,
                stop_loss_price, rationale, outcome, final_qty, reasons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts, proposal.agent_id, proposal.symbol, proposal.intent.value,
                proposal.quantity, proposal.reference_price, proposal.stop_loss_price,
                proposal.rationale, decision.outcome.value, decision.quantity,
                json.dumps(decision.reasons),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def decisions_for(self, agent_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM decisions WHERE agent_id = ? ORDER BY ts, id",
            (agent_id,),
        ).fetchall()

    def reasons_for_latest(self, agent_id: str) -> list[str]:
        row = self.conn.execute(
            "SELECT reasons FROM decisions WHERE agent_id = ? ORDER BY id DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        return json.loads(row["reasons"]) if row else []

    def record_fill(
        self, ts: str, agent_id: str, symbol: str, intent: Intent,
        quantity: int, price: float, decision_id: int | None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO fills (ts, agent_id, symbol, intent, quantity, price, decision_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, agent_id, symbol, intent.value, quantity, price, decision_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def fills_for(self, agent_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM fills WHERE agent_id = ? ORDER BY ts, id",
            (agent_id,),
        ).fetchall()

    def record_equity_snapshot(self, agent_id: str, date: str, equity: float) -> None:
        self.conn.execute(
            """
            INSERT INTO equity_snapshots (agent_id, date, equity)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id, date) DO UPDATE SET equity = excluded.equity
            """,
            (agent_id, date, equity),
        )
        self.conn.commit()

    def equity_curve(self, agent_id: str) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT date, equity FROM equity_snapshots WHERE agent_id = ? ORDER BY date",
            (agent_id,),
        ).fetchall()
        return [(r["date"], r["equity"]) for r in rows]

    def record_veto(self, ts: str, agent_id: str, proposal, quantity: int, verdicts,
                    entry_price: float | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO vetoes (ts, agent_id, symbol, intent, quantity, verdicts, entry_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, agent_id, proposal.symbol, proposal.intent.value, quantity,
             json.dumps([asdict(v) for v in verdicts]), entry_price),
        )
        self.conn.commit()
        return cur.lastrowid

    def vetoes_for(self, agent_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM vetoes WHERE agent_id = ? ORDER BY ts, id",
            (agent_id,),
        ).fetchall()
