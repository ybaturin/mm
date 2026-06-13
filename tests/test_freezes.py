import pytest
from trading.persistence.db import connect
from trading.persistence.freezes import FreezeStore, GLOBAL
from trading.persistence.schema import init_db


@pytest.fixture
def store(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return FreezeStore(conn)


def test_unfrozen_by_default(store):
    assert store.is_frozen("moderate") is False
    assert store.is_frozen(GLOBAL) is False


def test_freeze_and_check(store):
    store.freeze("moderate", "daily loss limit", "2026-06-15T13:00:00Z")
    assert store.is_frozen("moderate") is True
    assert store.is_frozen("aggressive") is False


def test_global_freeze_is_its_own_scope(store):
    store.freeze(GLOBAL, "NAV floor breached", "2026-06-15T13:00:00Z")
    assert store.is_frozen(GLOBAL) is True
    assert store.is_frozen("moderate") is False


def test_unfreeze(store):
    store.freeze("moderate", "x", "2026-06-15T13:00:00Z")
    store.unfreeze("moderate")
    assert store.is_frozen("moderate") is False


def test_freeze_is_idempotent_and_updates_reason(store):
    store.freeze("moderate", "first", "2026-06-15T13:00:00Z")
    store.freeze("moderate", "second", "2026-06-16T13:00:00Z")
    assert store.is_frozen("moderate") is True
    assert store.reason("moderate") == "second"


def test_frozen_scopes_lists_all(store):
    store.freeze("moderate", "x", "t")
    store.freeze(GLOBAL, "y", "t")
    assert set(store.frozen_scopes()) == {"moderate", GLOBAL}
