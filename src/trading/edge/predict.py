from __future__ import annotations

import os

from trading.edge.documents import EventDocuments
from trading.edge.events import EarningsEvent
from trading.edge.prompts import (PREDICT_SYSTEM, PROBE_SYSTEM,
                                   build_predict_user_prompt, build_probe_user_prompt)
from trading.edge.schema import EdgePrediction, MemoryProbe

DEFAULT_MODEL = os.environ.get("EDGE_MODEL", "claude-opus-4-8")
MAX_TOKENS = 8192


class EdgePredictor:
    """The only Claude caller in the edge module. One deep-read prediction per event,
    plus a memory-probe to drop events whose outcome the model already knows."""

    def __init__(self, client=None, model: str = DEFAULT_MODEL) -> None:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def predict(self, docs: EventDocuments, horizon_days: int) -> EdgePrediction:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=PREDICT_SYSTEM,
            messages=[{"role": "user",
                       "content": build_predict_user_prompt(docs, horizon_days)}],
            output_format=EdgePrediction,
        )
        return response.parsed_output

    def memory_probe(self, event: EarningsEvent) -> MemoryProbe:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=PROBE_SYSTEM,
            messages=[{"role": "user", "content": build_probe_user_prompt(event)}],
            output_format=MemoryProbe,
        )
        return response.parsed_output
