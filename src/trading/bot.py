from __future__ import annotations

from trading.reporting.format import (
    format_pnl_report, format_positions, format_status, format_trades,
)
from trading.reporting.queries import (
    pnl_report, positions_report, status_report, trades_report,
)

_PERIODS = ("day", "week", "month", "all")
_PNL_BUTTONS = {"inline_keyboard": [[
    {"text": "Сегодня", "callback_data": "pnl:day"},
    {"text": "Неделя", "callback_data": "pnl:week"},
    {"text": "Месяц", "callback_data": "pnl:month"},
    {"text": "Всё", "callback_data": "pnl:all"},
]]}

COMMANDS = [
    {"command": "positions", "description": "Активные позиции"},
    {"command": "pnl", "description": "P&L за период"},
    {"command": "status", "description": "Краткая сводка"},
    {"command": "trades", "description": "Последние сделки"},
]


class Bot:
    """Telegram command dispatcher: reads the DB and answers /positions, /pnl,
    /status, /trades. Transport is injected (httpx-shaped client)."""

    def __init__(self, client, base: str, accounts, journal, freezes, run_lock,
                 agent_ids: list[str], price_fn, chat_id: str, admin_ids: set[int]) -> None:
        self.client = client
        self.base = base
        self.accounts = accounts
        self.journal = journal
        self.freezes = freezes
        self.run_lock = run_lock
        self.agent_ids = agent_ids
        self.price_fn = price_fn
        self.chat_id = chat_id
        self.admin_ids = admin_ids

    # --- transport helpers ---
    def _send(self, text: str, reply_markup=None) -> None:
        payload = {"chat_id": self.chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.client.post(f"{self.base}/sendMessage", json=payload)

    def _edit(self, message_id: int, text: str) -> None:
        self.client.post(f"{self.base}/editMessageText",
                         json={"chat_id": self.chat_id, "message_id": message_id,
                               "text": text})

    def _answer(self, cb_id: str) -> None:
        self.client.post(f"{self.base}/answerCallbackQuery",
                         json={"callback_query_id": cb_id})

    def set_my_commands(self) -> None:
        self.client.post(f"{self.base}/setMyCommands", json={"commands": COMMANDS})

    # --- report builders ---
    def _pnl_text(self, period: str) -> str:
        return format_pnl_report(pnl_report(self.journal, self.agent_ids, period))

    # --- dispatch ---
    def handle_update(self, upd: dict) -> None:
        cb = upd.get("callback_query")
        if cb is not None:
            if cb.get("from", {}).get("id") not in self.admin_ids:
                return
            data = cb.get("data", "")
            if data.startswith("pnl:"):
                period = data.split(":", 1)[1]
                if period in _PERIODS:
                    self._edit(cb["message"]["message_id"], self._pnl_text(period))
            self._answer(cb["id"])
            return

        msg = upd.get("message")
        if not msg:
            return
        if msg.get("from", {}).get("id") not in self.admin_ids:
            return
        parts = (msg.get("text") or "").strip().split()
        if not parts:
            return
        cmd = parts[0].split("@")[0].lstrip("/")   # tolerate /cmd@botname
        arg = parts[1] if len(parts) > 1 else None

        if cmd == "positions":
            self._send(format_positions(
                positions_report(self.accounts, self.agent_ids, self.price_fn)))
        elif cmd == "status":
            self._send(format_status(status_report(
                self.accounts, self.journal, self.freezes, self.agent_ids, self.price_fn)))
        elif cmd == "trades":
            self._send(format_trades(trades_report(self.journal, self.agent_ids)))
        elif cmd == "pnl":
            if arg in _PERIODS:
                self._send(self._pnl_text(arg))
            else:
                self._send("Выбери период:", reply_markup=_PNL_BUTTONS)
