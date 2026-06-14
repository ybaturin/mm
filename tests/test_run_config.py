from trading.run import resolve_db_path


def test_resolve_db_path_is_mode_tagged_for_fake(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("BROKER", "fake")
    assert resolve_db_path() == "data/trading-fake.db"


def test_resolve_db_path_honors_explicit_override(monkeypatch):
    monkeypatch.setenv("DB_PATH", "/tmp/custom.db")
    assert resolve_db_path() == "/tmp/custom.db"


def test_bot_build_resolves_same_path_as_run():
    # Regression: the bot once read data/trading.db while the run wrote
    # data/trading-fake.db. They must share one resolver.
    import trading.bot as bot
    import trading.run as run
    assert "resolve_db_path" in bot.build_bot.__code__.co_names
    assert run.resolve_db_path is resolve_db_path
