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
