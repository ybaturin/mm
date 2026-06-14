from __future__ import annotations

from trading.data.bars import MarketDataSource
from trading.edge.documents import DocumentSource
from trading.edge.events import EarningsEvent
from trading.edge.realize import realized_market_adjusted
from trading.edge.report import build_report
from trading.edge.store import EdgeRepository


def run_measurement(*, events: list[EarningsEvent], source: MarketDataSource,
                    doc_source: DocumentSource, predictor, repo: EdgeRepository,
                    horizon_days: int) -> str:
    """Run the batch over already-selected (post-cutoff) events and return the report.

    Per event: memory-probe -> fetch docs -> predict -> record -> realize -> store.
    A memory-probe hit is still recorded (with knows_outcome=1) but excluded from
    scoring. Any per-event data failure degrades to skipping that event's realized
    return — it never aborts the batch.
    """
    for event in events:
        probe = predictor.memory_probe(event)
        try:
            docs = doc_source.documents(event)
        except Exception:
            continue
        pred = predictor.predict(docs, horizon_days)
        pid = repo.record(
            symbol=event.symbol, report_date=event.report_date,
            decision_date=event.decision_date, horizon_days=horizon_days,
            direction=pred.direction, magnitude_pct=pred.magnitude_pct,
            confidence=pred.confidence, rationale=pred.rationale,
            knows_outcome=probe.knows_outcome, eps_actual=event.eps_actual,
            eps_consensus=event.eps_consensus, model=predictor.model,
        )
        realized = realized_market_adjusted(source, event.symbol,
                                            event.decision_date, horizon_days)
        if realized is not None:
            repo.set_realized(pid, realized)
    return build_report(repo.scored())


def main() -> None:
    """One-time pilot: pull post-cutoff events from FMP, measure, print the report.

    Config via env: FMP_API_KEY, EDGE_CUTOFF (earliest report_date, e.g. 2026-02-01),
    EDGE_FROM / EDGE_TO (calendar window), EDGE_HORIZON (default 5). The DB lives at
    EDGE_DB (default edge.db) and accumulates across runs (approach A->C).
    """
    import os

    from trading.data.yfinance_source import YFinanceSource
    from trading.edge.events import select_post_cutoff
    from trading.edge.predict import EdgePredictor
    from trading.edge.sources import FMPSource
    from trading.edge.store import EdgeRepository, init_edge_db
    from trading.persistence.db import connect

    horizon = int(os.environ.get("EDGE_HORIZON", "5"))
    cutoff = os.environ.get("EDGE_CUTOFF", "2026-02-01")
    frm = os.environ.get("EDGE_FROM", cutoff)
    to = os.environ.get("EDGE_TO", "2026-05-31")

    conn = connect(os.environ.get("EDGE_DB", "edge.db"))
    init_edge_db(conn)
    repo = EdgeRepository(conn)

    fmp = FMPSource()
    events = select_post_cutoff(fmp.calendar(frm, to), earliest_report_date=cutoff)
    print(f"selected {len(events)} post-cutoff events")

    report = run_measurement(
        events=events, source=YFinanceSource(), doc_source=fmp,
        predictor=EdgePredictor(), repo=repo, horizon_days=horizon,
    )
    print(report)


if __name__ == "__main__":
    main()
