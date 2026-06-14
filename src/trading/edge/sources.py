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


# --- Alpha Vantage (free tier includes transcripts: 25 calls/day, 5/min) ---------------

AV_BASE = "https://www.alphavantage.co/query"


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_av_earnings(payload: dict, symbol: str, earliest_report_date: str,
                      decision_offset_days: int) -> list[EarningsEvent]:
    """Map Alpha Vantage EARNINGS `quarterlyEarnings` rows to post-cutoff EarningsEvents.
    Keeps rows whose reportedDate is on/after the cutoff. A throttle/premium message has
    no `quarterlyEarnings` key, so this degrades to []."""
    out: list[EarningsEvent] = []
    for row in payload.get("quarterlyEarnings", []):
        reported = str(row.get("reportedDate", ""))[:10]
        if not reported or reported < earliest_report_date:
            continue
        decision = (date.fromisoformat(reported)
                    + timedelta(days=decision_offset_days)).isoformat()
        out.append(EarningsEvent(
            symbol=symbol, report_date=reported, decision_date=decision,
            eps_actual=_to_float(row.get("reportedEPS")),
            eps_consensus=_to_float(row.get("estimatedEPS")),
        ))
    return out


def parse_av_transcript(payload: dict) -> str:
    """Flatten Alpha Vantage EARNINGS_CALL_TRANSCRIPT into text, prefixing each turn with
    speaker + title so the model sees who is talking (CFO tone in Q&A is the signal)."""
    items = payload.get("transcript", [])
    if not items:
        return ""
    lines: list[str] = []
    for it in items:
        speaker = (it.get("speaker") or "").strip()
        title = (it.get("title") or "").strip()
        content = (it.get("content") or "").strip()
        head = f"{speaker} ({title})" if title else speaker
        lines.append(f"{head}: {content}" if head else content)
    return "\n".join(lines)


class AlphaVantageSource:
    """Alpha Vantage earnings calendar (per symbol) + transcripts. Never raises —
    degrades to []/blank. Free tier includes both endpoints (25 calls/day, 5/min).
    Requires ALPHAVANTAGE_API_KEY."""

    def __init__(self, api_key: str | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY", "")
        self.timeout = timeout

    def _get(self, params: dict) -> dict:
        r = httpx.get(AV_BASE, params={**params, "apikey": self.api_key},
                      timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def calendar(self, symbols: list[str], earliest_report_date: str,
                 decision_offset_days: int = 2) -> list[EarningsEvent]:
        events: list[EarningsEvent] = []
        for symbol in symbols:
            try:
                payload = self._get({"function": "EARNINGS", "symbol": symbol})
                events.extend(parse_av_earnings(
                    payload, symbol, earliest_report_date, decision_offset_days))
            except Exception:
                continue
        return events

    def documents(self, event: EarningsEvent) -> EventDocuments:
        transcript = ""
        try:
            year, quarter = _year_quarter(event.report_date)
            payload = self._get({"function": "EARNINGS_CALL_TRANSCRIPT",
                                 "symbol": event.symbol, "quarter": f"{year}Q{quarter}"})
            transcript = parse_av_transcript(payload)
        except Exception:
            transcript = ""
        return EventDocuments(symbol=event.symbol, decision_date=event.decision_date,
                              transcript=transcript)

