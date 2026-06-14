"""Manual smoke for the edge network adapters. Needs FMP_API_KEY. Not run in CI.

Usage: FMP_API_KEY=... uv run python scripts/smoke_edge.py
"""
from trading.edge.sources import FMPSource


def main() -> None:
    src = FMPSource()
    events = src.calendar("2026-02-01", "2026-02-28")
    print(f"calendar events: {len(events)}")
    if events:
        docs = src.documents(events[0])
        print(f"first transcript chars: {len(docs.transcript)}")


if __name__ == "__main__":
    main()
