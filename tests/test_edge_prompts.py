from trading.edge.events import EarningsEvent
from trading.edge.documents import EventDocuments, FakeDocumentSource
from trading.edge.prompts import (build_predict_user_prompt, build_probe_user_prompt,
                                   PREDICT_SYSTEM, PROBE_SYSTEM)


def _docs():
    return EventDocuments(symbol="NVDA", decision_date="2026-02-23",
                          transcript="CEO: demand was exceptional...",
                          press_release="Q4 revenue up 80%", mdna="risks: supply")


def test_fake_document_source_returns_registered_docs():
    ev = EarningsEvent("NVDA", "2026-02-21", "2026-02-23")
    src = FakeDocumentSource({"NVDA": _docs()})
    assert src.documents(ev).transcript.startswith("CEO:")


def test_predict_prompt_includes_documents_and_horizon():
    prompt = build_predict_user_prompt(_docs(), horizon_days=5)
    assert "NVDA" in prompt
    assert "exceptional" in prompt          # transcript embedded
    assert "5" in prompt                      # horizon stated
    assert "market-adjusted" in PREDICT_SYSTEM.lower()


def test_probe_prompt_names_symbol_and_date():
    ev = EarningsEvent("NVDA", "2026-02-21", "2026-02-23")
    prompt = build_probe_user_prompt(ev)
    assert "NVDA" in prompt and "2026-02-21" in prompt
    assert "outcome" in PROBE_SYSTEM.lower()
