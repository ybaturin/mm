from __future__ import annotations

import logging
import time

from trading.reporting.format import (
    format_pnl_report, format_positions, format_status, format_trades,
)
from trading.reporting.queries import (
    pnl_report, positions_report, status_report, trades_report,
)

log = logging.getLogger("trading.bot")

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

WELCOME = "👋 Бот торговой системы.\n\nКоманды:\n" + "\n".join(
    f"• /{c['command']} — {c['description']}" for c in COMMANDS
) + "\n\nМеню команд — кнопка «/» слева от поля ввода."


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
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.client.post(f"{self.base}/sendMessage", json=payload)

    def _edit(self, message_id: int, text: str) -> None:
        self.client.post(f"{self.base}/editMessageText",
                         json={"chat_id": self.chat_id, "message_id": message_id,
                               "text": text, "parse_mode": "HTML"})

    def _answer(self, cb_id: str) -> None:
        self.client.post(f"{self.base}/answerCallbackQuery",
                         json={"callback_query_id": cb_id})

    def set_my_commands(self) -> bool:
        resp = self.client.post(f"{self.base}/setMyCommands",
                                json={"commands": COMMANDS}).json()
        return bool(resp.get("ok", False))

    # --- report builders ---
    def _pnl_text(self, period: str) -> str:
        return format_pnl_report(pnl_report(self.journal, self.agent_ids, period))

    # --- dispatch ---
    def handle_update(self, upd: dict) -> None:
        cb = upd.get("callback_query")
        if cb is not None:
            sender = cb.get("from", {}).get("id")
            if sender not in self.admin_ids:
                log.info("ignoring callback from non-admin %s", sender)
                return
            data = cb.get("data", "")
            log.info("callback %r from %s", data, sender)
            if data.startswith("pnl:"):
                period = data.split(":", 1)[1]
                if period in _PERIODS:
                    self._edit(cb["message"]["message_id"], self._pnl_text(period))
            self._answer(cb["id"])
            return

        msg = upd.get("message")
        if not msg:
            return
        sender = msg.get("from", {}).get("id")
        if sender not in self.admin_ids:
            log.info("ignoring message from non-admin %s", sender)
            return
        parts = (msg.get("text") or "").strip().split()
        if not parts:
            return
        cmd = parts[0].split("@")[0].lstrip("/")   # tolerate /cmd@botname
        arg = parts[1] if len(parts) > 1 else None
        log.info("command /%s arg=%r from %s", cmd, arg, sender)

        if cmd in ("start", "help"):
            self._send(WELCOME)
        elif cmd == "positions":
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

    # --- polling loop ---
    def poll_once(self, offset, now_iso=None):
        """One polling step. Returns the next offset, or None if it deferred to the
        running daily cycle (lock active)."""
        if self.run_lock.is_active(now_iso=now_iso):
            log.debug("daily run holds the lock — pausing poll")
            return None
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        resp = self.client.get(f"{self.base}/getUpdates", params=params).json()
        if not resp.get("ok", True):
            # Most often 409 Conflict: another process is also calling getUpdates
            # (e.g. a daily run waiting on a confirmation). Surface it, don't crash.
            log.warning("getUpdates not ok: %s", resp)
            return offset
        updates = resp.get("result", [])
        if updates:
            log.info("received %d update(s)", len(updates))
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                self.handle_update(upd)
            except Exception:  # noqa: BLE001 — one bad update must not kill the loop
                log.exception("handle_update failed for update_id=%s",
                              upd.get("update_id"))
        return offset

    def run_forever(self) -> None:
        log.info("bot starting; registering command menu")
        log.info("setMyCommands ok=%s", self.set_my_commands())
        offset = None
        while True:
            try:
                next_offset = self.poll_once(offset)
            except Exception:  # noqa: BLE001 — keep the daemon alive across transient errors
                log.exception("poll cycle failed; retrying in 3s")
                time.sleep(3)
                continue
            if next_offset is None:
                time.sleep(3)        # daily cycle owns Telegram right now
                continue
            offset = next_offset


def build_bot():
    """Assemble the bot from the environment (mirrors run.py wiring)."""
    import os

    import httpx

    from trading.config import load_profiles
    from trading.data.briefing import load_universe
    from trading.data.yfinance_source import YFinanceSource
    from trading.persistence.accounts import AccountRepository
    from trading.persistence.db import connect
    from trading.persistence.freezes import FreezeStore
    from trading.persistence.journal import JournalRepository
    from trading.persistence.runlock import RunLock
    from trading.persistence.schema import init_db
    from trading.reporting.telegram import resolve_admin_ids
    from trading.run import resolve_db_path

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    # Resolve the DB the same way the run does (mode-tagged), or the bot reads a
    # different file than the run writes to.
    db_path = resolve_db_path()

    conn = connect(db_path)
    conn.execute("PRAGMA busy_timeout = 5000")    # tolerate the daily run's brief writes
    init_db(conn)

    profiles = load_profiles("config/profiles.toml")
    load_universe("config/universe.toml")         # validate config presence at startup
    source = YFinanceSource()

    def price_fn(symbol: str) -> float:
        return source.latest_price(symbol)

    return Bot(
        client=httpx.Client(timeout=30.0),
        base=f"https://api.telegram.org/bot{token}",
        accounts=AccountRepository(conn),
        journal=JournalRepository(conn),
        freezes=FreezeStore(conn),
        run_lock=RunLock(conn),
        agent_ids=list(profiles.keys()),
        price_fn=price_fn,
        chat_id=chat_id,
        admin_ids=resolve_admin_ids(chat_id),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO — and our URLs embed the bot token.
    # Keep it at WARNING so the secret never lands in the journal.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_bot().run_forever()


if __name__ == "__main__":
    main()
