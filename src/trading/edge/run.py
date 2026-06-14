from __future__ import annotations

from trading.edge.documents import DocumentSource
from trading.edge.events import EarningsEvent
from trading.edge.realize import FetchWindow, realized_market_adjusted, yfinance_window
from trading.edge.report import build_report
from trading.edge.store import EdgeRepository


def run_measurement(*, events: list[EarningsEvent], doc_source: DocumentSource,
                    predictor, repo: EdgeRepository, horizon_days: int,
                    fetch_window: FetchWindow | None = None) -> str:
    """Run the batch over already-selected (post-cutoff) events and return the report.

    Per event: skip if already recorded (idempotent across days) -> fetch docs ->
    skip if no transcript -> memory-probe -> predict -> record -> realize -> store.
    Any per-event data failure degrades to skipping that event — it never aborts the
    batch.
    """
    import os
    import sys

    fetch = fetch_window or yfinance_window
    verbose = bool(os.environ.get("EDGE_VERBOSE"))
    total = len(events)
    for idx, event in enumerate(events, 1):
        if verbose:
            print(f"[{idx}/{total}] {event.symbol} {event.report_date}",
                  file=sys.stderr, flush=True)
        if repo.exists(event.symbol, event.report_date):
            continue
        # Per-event isolation: a transient API/network failure on one event must not
        # abort an unattended multi-hour batch. Skip the event; idempotency resumes it.
        try:
            docs = doc_source.documents(event)
            if not docs.transcript.strip():
                continue  # no transcript -> nothing to deep-read; skip rather than record noise
            probe = predictor.memory_probe(event)
            pred = predictor.predict(docs, horizon_days)
            pid = repo.record(
                symbol=event.symbol, report_date=event.report_date,
                decision_date=event.decision_date, horizon_days=horizon_days,
                direction=pred.direction, magnitude_pct=pred.magnitude_pct,
                confidence=pred.confidence, rationale=pred.rationale,
                knows_outcome=probe.knows_outcome, eps_actual=event.eps_actual,
                eps_consensus=event.eps_consensus, model=predictor.model,
            )
            realized = realized_market_adjusted(event.symbol, event.decision_date,
                                                horizon_days, fetch_window=fetch)
            if realized is not None:
                repo.set_realized(pid, realized)
        except Exception as exc:
            if verbose:
                print(f"  skip {event.symbol} {event.report_date}: {exc!r}",
                      file=sys.stderr, flush=True)
            continue
    return build_report(repo.scored())


def _load_universe() -> list[str]:
    """Pilot universe: env EDGE_UNIVERSE (comma-separated) or config/universe.toml.
    ETFs are dropped — they have no earnings calls."""
    import os
    import tomllib

    env = os.environ.get("EDGE_UNIVERSE", "").strip()
    if env:
        symbols = [s.strip().upper() for s in env.split(",") if s.strip()]
    else:
        path = os.environ.get("EDGE_UNIVERSE_FILE", "config/universe.toml")
        with open(path, "rb") as f:
            symbols = tomllib.load(f)["symbols"]
    etfs = {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI"}
    symbols = [s for s in symbols if s not in etfs]
    max_symbols = int(os.environ.get("EDGE_MAX_SYMBOLS", "0") or 0)
    return symbols[:max_symbols] if max_symbols else symbols


def main() -> None:
    """One-time pilot: pull post-cutoff events + transcripts from Alpha Vantage, measure,
    print the report.

    Config via env: ALPHAVANTAGE_API_KEY, EDGE_CUTOFF (earliest report_date, default
    2026-02-01), EDGE_HORIZON (default 5), EDGE_UNIVERSE / EDGE_MAX_SYMBOLS. The DB lives
    at EDGE_DB (default edge.db) and accumulates across runs — re-run on later days to
    fill more events under the free-tier 25-calls/day limit (idempotent).
    """
    import os

    from trading.edge.events import select_post_cutoff
    from trading.edge.predict import EdgePredictor
    from trading.edge.sources import AlphaVantageSource
    from trading.edge.store import EdgeRepository, init_edge_db
    from trading.persistence.db import connect

    horizon = int(os.environ.get("EDGE_HORIZON", "5"))
    cutoff = os.environ.get("EDGE_CUTOFF", "2026-02-01")
    symbols = _load_universe()

    conn = connect(os.environ.get("EDGE_DB", "edge.db"))
    init_edge_db(conn)
    repo = EdgeRepository(conn)

    av = AlphaVantageSource()
    events = select_post_cutoff(av.calendar(symbols, earliest_report_date=cutoff),
                                earliest_report_date=cutoff)
    print(f"universe {len(symbols)} symbols -> {len(events)} post-cutoff events")

    report = run_measurement(events=events, doc_source=av, predictor=EdgePredictor(),
                             repo=repo, horizon_days=horizon)
    print(report)


if __name__ == "__main__":
    main()
