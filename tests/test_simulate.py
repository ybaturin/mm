import pytest
from trading.config import RiskProfile
from trading.data.bars import Bar
from trading.orchestrator.simulate import run_simulation, synthetic_series
from trading.persistence.accounts import AccountRepository
from trading.persistence.db import connect
from trading.persistence.journal import JournalRepository
from trading.persistence.schema import init_db


def make_profiles():
    def p(name, **o):
        base = dict(name=name, budget=5000.0, max_position_pct=0.25, min_positions=5,
                    allow_shorts=False, stop_loss_pct=0.10, max_trades_per_day=4,
                    daily_loss_limit_pct=0.05, max_drawdown_pct=0.15,
                    auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25, veto_rule="majority")
        base.update(o)
        return RiskProfile(**base)
    return {"conservative": p("conservative", max_position_pct=0.15),
            "moderate": p("moderate"),
            "aggressive": p("aggressive", max_position_pct=0.40)}


@pytest.fixture
def repos(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    return AccountRepository(conn), JournalRepository(conn)


def test_synthetic_series_has_enough_bars():
    series = synthetic_series(["AAPL", "MSFT"], total_bars=90)
    assert set(series) == {"AAPL", "MSFT"}
    assert len(series["AAPL"]) == 90
    assert all(isinstance(b, Bar) for b in series["AAPL"])


def test_simulation_records_equity_for_every_day_and_agent(repos):
    accounts, journal = repos
    universe = ["AAPL", "MSFT"]
    series = synthetic_series(universe, total_bars=70)
    profiles = make_profiles()

    results = run_simulation(days=5, profiles=profiles, universe=universe,
                             series=series, accounts=accounts, journal=journal)

    for name in profiles:
        assert len(results[name]) == 5                       # one equity point per day
        assert len(journal.equity_curve(name)) == 5          # persisted too
        # every agent ends with a finite equity and a saved account
        assert accounts.get_state(name) is not None


def test_simulation_never_breaches_position_cap(repos):
    accounts, journal = repos
    universe = ["AAPL", "MSFT"]
    series = synthetic_series(universe, total_bars=70)
    profiles = make_profiles()

    run_simulation(days=5, profiles=profiles, universe=universe,
                   series=series, accounts=accounts, journal=journal)

    # final positions for each agent must respect its per-position cap
    final_prices = {s: series[s][-1].close for s in universe}
    for name, profile in profiles.items():
        state = accounts.get_state(name)
        for p in state.positions:
            notional = abs(p.quantity) * final_prices[p.symbol]
            assert notional <= profile.max_position_pct * profile.budget + 1.0
