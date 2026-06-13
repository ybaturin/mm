from __future__ import annotations

import os
import secrets
import time


class TelegramNotifier:
    """Sends messages and asks for confirmations over the Telegram Bot API."""

    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 client=None, confirm_timeout: float = 600.0) -> None:
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        self.chat_id = chat_id or os.environ["TELEGRAM_CHAT_ID"]
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.confirm_timeout = confirm_timeout
        self.admin_ids = self._resolve_admin_ids()
        if client is None:
            import httpx
            client = httpx.Client(timeout=30.0)
        self.client = client

    def _resolve_admin_ids(self) -> set[int]:
        """Telegram user ids allowed to approve trades. From TELEGRAM_ADMIN_IDS
        (comma-separated) if set, else the configured chat_id (correct for a private
        1:1 chat, where the user id equals the chat id)."""
        raw = os.environ.get("TELEGRAM_ADMIN_IDS")
        if raw:
            return {int(x) for x in raw.split(",") if x.strip()}
        try:
            return {int(self.chat_id)}
        except (TypeError, ValueError):
            return set()

    def notify(self, text: str) -> None:
        self.client.post(f"{self.base}/sendMessage",
                         json={"chat_id": self.chat_id, "text": text})

    def request_confirmation(self, text: str) -> bool:
        # Bind this request to a fresh nonce so a stale/replayed callback can't satisfy it.
        nonce = secrets.token_hex(8)
        approve, decline = f"approve:{nonce}", f"decline:{nonce}"

        # Drain any backlog first: advance the offset past everything queued before we
        # ask, so a queued callback is never mistaken for this request's answer.
        offset = None
        drained = self.client.get(f"{self.base}/getUpdates", params={"timeout": 0}).json()
        for upd in drained.get("result", []):
            offset = upd["update_id"] + 1

        keyboard = {"inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": approve},
            {"text": "❌ Decline", "callback_data": decline},
        ]]}
        sent = self.client.post(
            f"{self.base}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "reply_markup": keyboard},
        ).json()
        message_id = sent["result"]["message_id"]

        deadline = time.monotonic() + self.confirm_timeout
        while time.monotonic() < deadline:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            updates = self.client.get(f"{self.base}/getUpdates", params=params).json()
            for upd in updates.get("result", []):
                offset = upd["update_id"] + 1
                cb = upd.get("callback_query")
                if not cb or cb.get("message", {}).get("message_id") != message_id:
                    continue
                if cb.get("from", {}).get("id") not in self.admin_ids:
                    continue                          # ignore callbacks from non-admins
                data = cb.get("data")
                if data not in (approve, decline):
                    continue                          # ignore stale/forged callbacks
                self.client.post(f"{self.base}/answerCallbackQuery",
                                 json={"callback_query_id": cb["id"]})
                return data == approve
        return False  # timed out → safe default: do not trade
