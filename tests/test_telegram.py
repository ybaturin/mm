"""Authorization tests for the live Telegram confirmation channel.

A fake HTTP client stands in for httpx: it captures the inline-keyboard callback_data
the notifier sends, then replays a Telegram-shaped callback_query from a chosen sender.
"""
from trading.reporting.telegram import TelegramNotifier

ADMIN = 12345


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_notify_sends_html_parse_mode():
    sent = {}

    class C:
        def post(self, url, json=None):
            sent.update(json or {})
            return _Resp({"ok": True})

    n = TelegramNotifier(token="t", chat_id=str(ADMIN), client=C())
    n.notify("hello")
    assert sent["parse_mode"] == "HTML"


class FakeTelegramClient:
    """Simulates the Telegram Bot API for sendMessage / getUpdates / answerCallbackQuery."""

    def __init__(self, sender_id, serve="approve", drain_backlog=None):
        self.sender_id = sender_id
        self.serve = serve                       # which button the user "presses"
        self.message_id = 555
        self.buttons = {}                        # {"approve": "approve:<nonce>", ...}
        self.answered = []
        self._drain = drain_backlog or []
        self._drained = False
        self._served = False

    def post(self, url, json=None):
        if url.endswith("/sendMessage"):
            kb = (json or {}).get("reply_markup", {}).get("inline_keyboard")
            if kb:
                self.buttons = {b["callback_data"].split(":")[0]: b["callback_data"]
                                for b in kb[0]}
            return _Resp({"result": {"message_id": self.message_id}})
        if url.endswith("/answerCallbackQuery"):
            self.answered.append(json["callback_query_id"])
            return _Resp({"ok": True})
        return _Resp({"ok": True})

    def get(self, url, params=None):
        if not self._drained:                    # the pre-send backlog drain
            self._drained = True
            return _Resp({"result": self._drain})
        if self.buttons and not self._served and self.serve is not None:  # button press, once
            self._served = True
            return _Resp({"result": [{
                "update_id": 1001,
                "callback_query": {
                    "id": "cbq1",
                    "from": {"id": self.sender_id},
                    "message": {"message_id": self.message_id},
                    "data": self.buttons[self.serve],
                },
            }]})
        return _Resp({"result": []})


def _notifier(client):
    return TelegramNotifier(token="t", chat_id=str(ADMIN), client=client,
                            confirm_timeout=0.2)


def test_confirmation_accepts_approval_from_the_authorized_sender():
    client = FakeTelegramClient(sender_id=ADMIN, serve="approve")
    assert _notifier(client).request_confirmation("buy?") is True
    assert client.answered                       # the callback was acknowledged


def test_confirmation_ignores_approval_from_an_unauthorized_sender():
    client = FakeTelegramClient(sender_id=99999, serve="approve")  # not the admin
    assert _notifier(client).request_confirmation("buy?") is False


def test_confirmation_decline_is_honored():
    client = FakeTelegramClient(sender_id=ADMIN, serve="decline")
    assert _notifier(client).request_confirmation("buy?") is False


def test_resolve_admin_ids_from_env(monkeypatch):
    from trading.reporting.telegram import resolve_admin_ids
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "111, 222")
    assert resolve_admin_ids("999") == {111, 222}


def test_resolve_admin_ids_falls_back_to_chat_id(monkeypatch):
    from trading.reporting.telegram import resolve_admin_ids
    monkeypatch.delenv("TELEGRAM_ADMIN_IDS", raising=False)
    assert resolve_admin_ids("999") == {999}


def test_confirmation_ignores_stale_backlog_callback():
    # A queued approve from BEFORE this request (drained on entry, and not carrying
    # this request's nonce) must never satisfy it. With no fresh press afterwards,
    # the request must time out to False rather than consume the backlog.
    stale = [{
        "update_id": 900,
        "callback_query": {
            "id": "old",
            "from": {"id": ADMIN},
            "message": {"message_id": 555},
            "data": "approve",
        },
    }]
    client = FakeTelegramClient(sender_id=ADMIN, serve=None, drain_backlog=stale)
    assert _notifier(client).request_confirmation("buy?") is False
