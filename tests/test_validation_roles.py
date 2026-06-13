from trading.validation.roles import ROLES, build_validator_system, build_validator_user
from trading.validation.schema import Verdict
from trading.data.briefing import Briefing, SymbolBrief
from trading.domain import Intent, TradeProposal


def proposal():
    return TradeProposal(agent_id="aggressive", symbol="TSLA", intent=Intent.OPEN_SHORT,
                         quantity=5, reference_price=200.0, stop_loss_price=215.0,
                         rationale="overbought, momentum fading")


def briefing():
    return Briefing("aggressive", "2026-06-15", 2000.0, 5000.0,
                    [SymbolBrief("TSLA", 200.0, 210.0, 220.0, 72.0, 0.08, 0, None)])


def test_three_distinct_roles():
    keys = [r.key for r in ROLES]
    assert keys == ["risk_skeptic", "catalyst_checker", "devils_advocate"]


def test_verdict_schema():
    v = Verdict(veto=True, reason="earnings tomorrow")
    assert v.veto is True and v.reason == "earnings tomorrow"


def test_system_prompt_carries_role_and_subtractive_rule():
    sys = build_validator_system(ROLES[0])
    assert "risk" in sys.lower()
    assert "veto" in sys.lower()
    # the panel can only block, never resize or add
    assert "cannot" in sys.lower() or "only" in sys.lower()


def test_user_prompt_describes_the_trade_and_context():
    u = build_validator_user(proposal(), briefing())
    assert "TSLA" in u
    assert "open_short" in u or "short" in u.lower()
    assert "215" in u            # stop
    assert "rsi" in u.lower() or "72" in u   # market context from briefing
