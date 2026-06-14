from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

from trading.edge.documents import EventDocuments
from trading.edge.events import EarningsEvent

FMP_BASE = "https://financialmodelingprep.com/api/v3"


def parse_fmp_transcript(raw: list[dict]) -> str:
    """FMP returns a list of transcript objects; take the content of the first."""
    if not raw:
        return ""
    return str(raw[0].get("content", "") or "")


def parse_fmp_calendar(raw: list[dict], decision_offset_days: int) -> list[EarningsEvent]:
    """Map FMP earnings-calendar rows to EarningsEvent. decision_date is report_date
    plus an offset (results land after close; we act the next trading day or two)."""
    out: list[EarningsEvent] = []
    for row in raw:
        report = str(row.get("date", ""))[:10]
        if not report:
            continue
        decision = (date.fromisoformat(report)
                    + timedelta(days=decision_offset_days)).isoformat()
        out.append(EarningsEvent(
            symbol=row.get("symbol", ""), report_date=report, decision_date=decision,
            eps_actual=row.get("epsActual"), eps_consensus=row.get("epsEstimated"),
        ))
    return out


class FMPSource:
    """FMP-backed earnings calendar + transcripts. Never raises — degrades to []/blank.
    Requires FMP_API_KEY (one-time paid month; cancel after the pilot pull)."""

    def __init__(self, api_key: str | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("FMP_API_KEY", "")
        self.timeout = timeout

    def calendar(self, from_date: str, to_date: str) -> list[EarningsEvent]:
        try:
            r = httpx.get(f"{FMP_BASE}/earning_calendar",
                          params={"from": from_date, "to": to_date,
                                  "apikey": self.api_key}, timeout=self.timeout)
            r.raise_for_status()
            return parse_fmp_calendar(r.json(), decision_offset_days=2)
        except Exception:
            return []

    def documents(self, event: EarningsEvent) -> EventDocuments:
        transcript = ""
        try:
            year, quarter = _year_quarter(event.report_date)
            r = httpx.get(f"{FMP_BASE}/earning_call_transcript/{event.symbol}",
                          params={"year": year, "quarter": quarter,
                                  "apikey": self.api_key}, timeout=self.timeout)
            r.raise_for_status()
            transcript = parse_fmp_transcript(r.json())
        except Exception:
            transcript = ""
        return EventDocuments(symbol=event.symbol, decision_date=event.decision_date,
                              transcript=transcript)


def _year_quarter(report_date: str) -> tuple[int, int]:
    d = date.fromisoformat(report_date)
    return d.year, (d.month - 1) // 3 + 1
