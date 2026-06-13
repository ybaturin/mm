"""Manual check: send a message and ask for a confirmation over Telegram.

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.
Run:  uv run python scripts/smoke_telegram.py
"""
from __future__ import annotations

from trading.reporting.telegram import TelegramNotifier


def main() -> None:
    n = TelegramNotifier(confirm_timeout=120.0)
    n.notify("✅ Trading system smoke test: hello from the Reporter.")
    print("Sent a test message. Now requesting a confirmation — tap a button in Telegram.")
    approved = n.request_confirmation("Smoke test: approve this pretend trade?")
    print(f"You {'approved' if approved else 'declined (or timed out)'}.")


if __name__ == "__main__":
    main()
