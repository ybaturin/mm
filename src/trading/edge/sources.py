from __future__ import annotations

import os
import time
from datetime import date, timedelta

import httpx

from trading.data.bars import Bar
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


def parse_av_daily(payload: dict) -> list[Bar]:
    """Parse Alpha Vantage TIME_SERIES_DAILY into ascending Bars. Throttle/error payload
    (no 'Time Series (Daily)') -> []."""
    ts = payload.get("Time Series (Daily)", {})
    out: list[Bar] = []
    for d, row in ts.items():
        try:
            out.append(Bar(date=d[:10], open=float(row["1. open"]),
                           high=float(row["2. high"]), low=float(row["3. low"]),
                           close=float(row["4. close"]),
                           volume=int(float(row["5. volume"]))))
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda b: b.date)
    return out


def parse_av_earnings_series(payload: dict) -> list[dict]:
    """All quarterly rows (no cutoff filter) as {report_date, eps_actual, eps_consensus}
    for SUE-by-sigma priors. EPS 'None'/missing -> None. Throttle payload -> []."""
    out: list[dict] = []
    for row in payload.get("quarterlyEarnings", []):
        reported = str(row.get("reportedDate", ""))[:10]
        if not reported:
            continue
        out.append({
            "report_date": reported,
            "eps_actual": _to_float(row.get("reportedEPS")),
            "eps_consensus": _to_float(row.get("estimatedEPS")),
        })
    return out


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

    def __init__(self, api_key: str | None = None, timeout: float = 30.0,
                 delay_sec: float | None = None) -> None:
        self.api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY", "")
        self.timeout = timeout
        # Space out calls to stay under the per-minute cap on dense bursts (the calendar
        # phase fires one call per universe symbol with no gaps). Override via EDGE_AV_DELAY.
        self.delay_sec = (float(os.environ.get("EDGE_AV_DELAY", "0.8"))
                          if delay_sec is None else delay_sec)
        # Per-symbol caches: full daily prices and full EPS history are each fetched
        # once and reused across every config/horizon in a sweep (kills both yfinance
        # throttling and redundant refetching).
        self._daily_cache: dict[str, list[Bar]] = {}
        self._earnings_cache: dict[str, list[dict]] = {}

    def _get(self, params: dict) -> dict:
        if self.delay_sec:
            time.sleep(self.delay_sec)
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

    def earnings_series(self, symbol: str) -> list[dict]:
        """Full quarterly EPS history for one symbol (for SUE-by-sigma priors). Cached."""
        if symbol in self._earnings_cache:
            return self._earnings_cache[symbol]
        try:
            payload = self._get({"function": "EARNINGS", "symbol": symbol})
            rows = parse_av_earnings_series(payload)
        except Exception:
            rows = []
        self._earnings_cache[symbol] = rows
        return rows

    def daily_series(self, symbol: str) -> list[Bar]:
        """Full daily OHLCV history for one symbol, ascending by date. Cached — one call
        per symbol serves every window/horizon. Degrades to []."""
        if symbol in self._daily_cache:
            return self._daily_cache[symbol]
        try:
            payload = self._get({"function": "TIME_SERIES_DAILY", "symbol": symbol,
                                 "outputsize": "full"})
            bars = parse_av_daily(payload)
        except Exception:
            bars = []
        self._daily_cache[symbol] = bars
        return bars

    def price_window(self, symbol: str, start_date: str, end_date: str) -> list[Bar]:
        """Bars in [start_date, end_date] from the cached daily series. Satisfies the
        FetchWindow signature used by realize/pead_study."""
        return [b for b in self.daily_series(symbol) if start_date <= b.date <= end_date]

    def price_at(self, symbol: str, as_of_date: str) -> float:
        """Close on the latest bar on/before as_of_date (point-in-time). 0.0 if none."""
        prior = [b for b in self.daily_series(symbol) if b.date <= as_of_date]
        return prior[-1].close if prior else 0.0

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

