from trading.agent.prompts import build_system_prompt, build_user_prompt
from trading.config import RiskProfile
from trading.data.briefing import Briefing, SymbolBrief


def make_profile(**o):
    base = dict(name="aggressive", budget=5000.0, max_position_pct=0.40, min_positions=3,
                allow_shorts=True, stop_loss_pct=0.12, max_trades_per_day=8,
                daily_loss_limit_pct=0.08, max_drawdown_pct=0.25,
                auto_exec_threshold_usd=500.0, auto_exec_threshold_pct=0.25,
                veto_rule="majority", mandate="")
    base.update(o)
    return RiskProfile(**base)


def test_mandate_appears_in_system_prompt():
    p = make_profile(mandate="Hunt momentum and act faster than the others.")
    assert "Hunt momentum and act faster than the others." in build_system_prompt(p)


def test_different_mandates_produce_different_prompts():
    a = build_system_prompt(make_profile(name="conservative", mandate="Preserve capital; trade rarely."))
    b = build_system_prompt(make_profile(name="aggressive", mandate="Hunt momentum; concentrate."))
    assert a != b
    assert "Preserve capital" in a and "Hunt momentum" in b


def briefing():
    return Briefing(
        agent_id="aggressive", as_of_date="2026-06-15", cash=2000.0, equity=2795.0,
        symbols=[
            SymbolBrief("AAPL", 159.0, 150.0, 140.0, 60.0, 0.03, 5, 120.0),
            SymbolBrief("MSFT", 410.0, 400.0, 390.0, 55.0, 0.01, 0, None),
        ],
    )


def test_system_prompt_states_profile_and_constraints():
    p = build_system_prompt(make_profile())
    assert "aggressive" in p
    assert "40%" in p or "0.40" in p or "40 %" in p     # max position
    assert "short" in p.lower()                          # shorts allowed mention
    assert "propose" in p.lower()                        # it proposes, does not execute


def test_system_prompt_forbids_shorts_when_disallowed():
    p = build_system_prompt(make_profile(name="conservative", allow_shorts=False))
    assert "short" in p.lower()
    assert "not" in p.lower() or "no shorting" in p.lower() or "long only" in p.lower()


def test_user_prompt_includes_account_and_symbols():
    u = build_user_prompt(briefing())
    assert "2026-06-15" in u
    assert "AAPL" in u and "MSFT" in u
    assert "159" in u                                    # AAPL price
    assert "2000" in u                                   # cash


def test_user_prompt_marks_held_positions():
    u = build_user_prompt(briefing())
    # AAPL is held (5 @ 120), MSFT is not — the prompt must distinguish them
    assert "AAPL" in u
    aapl_line = next(line for line in u.splitlines() if line.startswith("AAPL"))
    assert "5" in aapl_line


from trading.analysis.round_trips import RoundTrip
from trading.data.briefing import Memory, OpenPositionMemory, SelfStats
from trading.data.news import Headline


def briefing_with_memory_and_news():
    base = briefing()
    mem = Memory(
        open_positions=[OpenPositionMemory("AAPL", 5, 120.0, "bought the breakout", -0.08)],
        recent_closed=[RoundTrip("MSFT", 3, "2026-06-01", 400.0, "2026-06-04", 380.0,
                                 -60.0, -0.05, "chased the rally")],
        stats=SelfStats(closed_trades=4, win_rate=0.25, avg_win=40.0,
                        avg_loss=-50.0, total_realized_pnl=-30.0),
    )
    news = {"AAPL": [Headline("AAPL", "Antitrust probe opens", "Reuters", "2026-06-14")]}
    return Briefing(agent_id=base.agent_id, as_of_date=base.as_of_date, cash=base.cash,
                    equity=base.equity, symbols=base.symbols, memory=mem, news=news)


def test_user_prompt_renders_memory_block():
    u = build_user_prompt(briefing_with_memory_and_news())
    assert "bought the breakout" in u           # open position rationale
    assert "chased the rally" in u              # closed trade rationale
    assert "25%" in u                           # win rate


def test_user_prompt_renders_news_block():
    u = build_user_prompt(briefing_with_memory_and_news())
    assert "Antitrust probe opens" in u
    assert "Reuters" in u


def test_user_prompt_omits_empty_memory_and_news():
    u = build_user_prompt(briefing())           # no memory, no news
    assert "track record" not in u.lower()
    assert "recent news" not in u.lower()


def test_system_prompt_instructs_learning_and_news_discipline():
    p = build_system_prompt(make_profile())
    assert "track record" in p.lower() or "past trade" in p.lower()
    assert "invent" in p.lower() or "not listed" in p.lower()


def test_prompt_demands_target_and_horizon():
    p = build_system_prompt(make_profile())
    assert "target_price" in p
    assert "horizon_days" in p


def test_prompt_forbids_indicator_jargon_in_rationale():
    p = build_system_prompt(make_profile()).lower()
    assert "rsi" in p          # the instruction references jargon to avoid
    assert "without" in p
