from __future__ import annotations

import os
import time


class TelegramNotifier:
    """Sends messages and asks for confirmations over the Telegram Bot API."""

    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 client=None, confirm_timeout: float = 600.0) -> None:
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        self.chat_id = chat_id or os.environ["TELEGRAM_CHAT_ID"]
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.confirm_timeout = confirm_timeout
        if client is None:
            import httpx
            client = httpx.Client(timeout=30.0)
        self.client = client

    def notify(self, text: str) -> None:
        self.client.post(f"{self.base}/sendMessage",
                         json={"chat_id": self.chat_id, "text": text})

    def request_confirmation(self, text: str) -> bool:
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": "approve"},
            {"text": "❌ Decline", "callback_data": "decline"},
        ]]}
        sent = self.client.post(
            f"{self.base}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "reply_markup": keyboard},
        ).json()
        message_id = sent["result"]["message_id"]

        deadline = time.monotonic() + self.confirm_timeout
        offset = None
        while time.monotonic() < deadline:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            updates = self.client.get(f"{self.base}/getUpdates", params=params).json()
            for upd in updates.get("result", []):
                offset = upd["update_id"] + 1
                cb = upd.get("callback_query")
                if cb and cb.get("message", {}).get("message_id") == message_id:
                    self.client.post(f"{self.base}/answerCallbackQuery",
                                     json={"callback_query_id": cb["id"]})
                    return cb["data"] == "approve"
        return False  # timed out → safe default: do not trade
