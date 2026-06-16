from trading.edge.portfolio import PeadRecord
from trading.edge.pead_study import Config, ConfigResult, build_report


def test_report_shows_chosen_config_and_train_vs_test():
    chosen = ConfigResult(Config("small", 20, "sigma"), net_long_short=0.031, n=80)
    test_recs = [PeadRecord("A", "2026-05-01", "small", 2.0, 0.02),
                 PeadRecord("B", "2026-05-02", "small", -2.0, -0.01)]
    report = build_report(chosen, test_recs, all_ranked=[chosen])
    assert "PRE-REGISTERED CONFIG" in report
    assert "small" in report and "20" in report and "sigma" in report
    assert "TRAIN net long-short: +0.0310" in report
    assert "HELD-OUT TEST" in report
    assert "configs evaluated: 1" in report   # multiple-testing visible


def test_report_handles_empty_test():
    chosen = ConfigResult(Config("large", 5, "price"), net_long_short=0.0, n=0)
    report = build_report(chosen, [], all_ranked=[chosen])
    assert "insufficient" in report.lower()
