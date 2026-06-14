import pytest
from trading.bot import Bot
from trading.domain import AgentState, Position
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.freezes import FreezeStore
from trading.persistence.journal import JournalRepository
from trading.persistence.runlock import RunLock
from trading.persistence.schema import init_db

ADMIN = 12345


class FakeClient:
    """Records outbound calls; mirrors the post/get shape of httpx used elsewhere."""

    def __init__(self):
        self.sent = []          # list of (text, reply_markup)
        self.edits = []         # list of (message_id, text)
        self.answered = []
        self.commands = None

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def post(self, url, json=None):
        if url.endswith("/sendMessage"):
            self.sent.append((json["text"], json.get("reply_markup")))
            return self._Resp({"result": {"message_id": 1}})
        if url.endswith("/editMessageText"):
            self.edits.append((json["message_id"], json["text"]))
            return self._Resp({"result": {}})
        if url.endswith("/answerCallbackQuery"):
            self.answered.append(json["callback_query_id"])
            return self._Resp({"ok": True})
        if url.endswith("/setMyCommands"):
            self.commands = json["commands"]
            return self._Resp({"ok": True})
        return self._Resp({"ok": True})


@pytest.fixture
def bot():
    conn = connect(":memory:")
    init_db(conn)
    acc = AccountRepository(conn)
    jr = JournalRepository(conn)
    fr = FreezeStore(conn)
    acc.save_state(AgentState("momentum", cash=1000.0,
                              positions=[Position("AAPL", 10, 200.0)]))
    jr.record_equity_snapshot("momentum", "2026-06-05", 3000.0)
    jr.record_equity_snapshot("momentum", "2026-06-13", 3000.0)
    client = FakeClient()
    bot = Bot(client=client, base="https://api.telegram.org/botT",
              accounts=acc, journal=jr, freezes=fr, run_lock=RunLock(conn),
              agent_ids=["momentum"], price_fn=lambda s: 210.0,
              chat_id=str(ADMIN), admin_ids={ADMIN})
    return bot, client


def _message(text, sender=ADMIN):
    return {"update_id": 1, "message": {"from": {"id": sender}, "text": text}}


def test_positions_command_replies(bot):
    b, client = bot
    b.handle_update(_message("/positions"))
    assert client.sent
    assert "AAPL" in client.sent[0][0]


def test_status_command_replies(bot):
    b, client = bot
    b.handle_update(_message("/status"))
    assert "Статус" in client.sent[0][0]


def test_pnl_without_arg_sends_period_buttons(bot):
    b, client = bot
    b.handle_update(_message("/pnl"))
    text, markup = client.sent[0]
    assert markup is not None
    datas = [btn["callback_data"] for btn in markup["inline_keyboard"][0]]
    assert "pnl:week" in datas


def test_pnl_with_arg_replies_directly(bot):
    b, client = bot
    b.handle_update(_message("/pnl week"))
    assert "P&L" in client.sent[0][0]
    assert client.sent[0][1] is None       # no buttons


def test_pnl_callback_edits_message(bot):
    b, client = bot
    cb = {"update_id": 2, "callback_query": {
        "id": "cb1", "from": {"id": ADMIN},
        "message": {"message_id": 77}, "data": "pnl:month"}}
    b.handle_update(cb)
    assert client.edits and client.edits[0][0] == 77
    assert "P&L" in client.edits[0][1]
    assert client.answered == ["cb1"]


def test_ignores_non_admin(bot):
    b, client = bot
    b.handle_update(_message("/positions", sender=99999))
    assert client.sent == []


def test_start_command_greets_and_lists_commands(bot):
    b, client = bot
    b.handle_update(_message("/start"))
    assert client.sent
    text = client.sent[0][0]
    assert "/positions" in text
    assert "/pnl" in text
    assert "/status" in text
    assert "/trades" in text


def test_poll_once_skips_when_run_lock_active(bot):
    b, client = bot
    b.run_lock.acquire(now_iso="2026-06-13T13:30:00Z")
    polled = b.poll_once(offset=None, now_iso="2026-06-13T13:30:10Z")
    assert polled is None                  # did not poll getUpdates
    assert client.sent == []


def test_poll_once_processes_updates_when_unlocked(bot):
    b, client = bot

    def fake_get(url, params=None):
        return FakeClient._Resp({"result": [_message("/status")]})

    b.client.get = fake_get
    new_offset = b.poll_once(offset=None, now_iso="2026-06-13T13:30:10Z")
    assert client.sent                     # /status handled
    assert new_offset == 2                 # update_id (1) + 1


def test_poll_once_survives_handler_error(bot):
    b, client = bot

    def boom(upd):
        raise RuntimeError("boom")

    b.handle_update = boom

    def fake_get(url, params=None):
        return FakeClient._Resp({"ok": True, "result": [_message("/positions")]})

    b.client.get = fake_get
    # the bad update must not propagate; offset still advances past it
    assert b.poll_once(offset=None, now_iso="2026-06-13T13:30:10Z") == 2


def test_poll_once_handles_getupdates_conflict(bot):
    b, client = bot

    def fake_get(url, params=None):
        return FakeClient._Resp({"ok": False, "error_code": 409, "description": "Conflict"})

    b.client.get = fake_get
    # a 409 (another getUpdates consumer) must not crash; offset is preserved
    assert b.poll_once(offset=5, now_iso="2026-06-13T13:30:10Z") == 5


def test_send_uses_html_parse_mode():
    sent = []

    class C:
        def post(self, url, json=None):
            sent.append(json)
            return FakeClient._Resp({"ok": True, "result": {}})

        def get(self, url, params=None):
            return FakeClient._Resp({"ok": True, "result": []})

    bot = Bot(client=C(), base="https://api.telegram.org/botX",
              accounts=None, journal=None, freezes=None, run_lock=None,
              agent_ids=[], price_fn=lambda s: 0.0, chat_id=str(ADMIN),
              admin_ids={ADMIN})
    bot._send("hi")
    bot._edit(7, "edited")
    assert sent[0]["parse_mode"] == "HTML"
    assert sent[1]["parse_mode"] == "HTML"
