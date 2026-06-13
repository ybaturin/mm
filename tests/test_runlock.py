import pytest
from trading.persistence.db import connect
from trading.persistence.runlock import RunLock
from trading.persistence.schema import init_db


@pytest.fixture
def lock(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return RunLock(conn)


def test_inactive_by_default(lock):
    assert lock.is_active(now_iso="2026-06-13T13:30:00Z") is False


def test_acquire_makes_active(lock):
    lock.acquire(now_iso="2026-06-13T13:30:00Z")
    assert lock.is_active(now_iso="2026-06-13T13:30:30Z") is True


def test_release_makes_inactive(lock):
    lock.acquire(now_iso="2026-06-13T13:30:00Z")
    lock.release()
    assert lock.is_active(now_iso="2026-06-13T13:30:30Z") is False


def test_stale_lock_is_treated_inactive(lock):
    # since 20 minutes ago with stale_after_s=900 (15 min) -> treated as inactive
    lock.acquire(now_iso="2026-06-13T13:00:00Z")
    assert lock.is_active(now_iso="2026-06-13T13:20:01Z") is False
