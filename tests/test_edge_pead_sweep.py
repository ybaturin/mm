from trading.edge.portfolio import PeadRecord
from trading.edge.pead_study import sweep, Config


def _recs(signal_realized, tier="small"):
    return [PeadRecord("S%d" % i, "2026-02-%02d" % (1 + i), tier, sig, real)
            for i, (sig, real) in enumerate(signal_realized)]


def test_sweep_ranks_configs_by_net_long_short():
    good = _recs([(3.0, 0.05), (2.0, 0.03), (-2.0, -0.02), (-3.0, -0.05)])
    bad = _recs([(3.0, -0.05), (2.0, -0.03), (-2.0, 0.02), (-3.0, 0.05)])
    configs = [Config("small", 5, "price"), Config("small", 20, "price")]

    def builder(cfg, events):
        return good if cfg.horizon == 5 else bad

    ranked = sweep(configs, events=[], builder=builder)
    # Best (highest net long-short) first; the 'good' config (horizon 5) wins.
    assert ranked[0].config.horizon == 5
    assert ranked[0].net_long_short > ranked[1].net_long_short
