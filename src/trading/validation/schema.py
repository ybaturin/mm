from __future__ import annotations

from pydantic import BaseModel


class Verdict(BaseModel):
    """One validator's judgment on a proposal. The model can only veto or allow."""
    veto: bool
    reason: str
