"""Portfolio admission and slot reservation -- Revision 5, Section 8.1.

`PortfolioAdmission` is the portfolio-level ACCEPTED/NO_CAPACITY decision for an
already-independently-actionable `RankedCandidate` (see
stock_analyzer.sandbox.domain.candidate.RankedCandidate). It never mutates that
candidate's `actionable`/`exclusion_reason` -- ranking and portfolio admission are
deliberately separate facts (Section 8.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

ACCEPTED = "ACCEPTED"
NO_CAPACITY = "NO_CAPACITY"

RESERVED = "RESERVED"
CONVERTED = "CONVERTED"
RELEASED = "RELEASED"


@dataclass(frozen=True)
class PortfolioAdmission:
    admission_id: str
    replay_id: str
    candidate_id: str
    symbol: str
    as_of_date: date
    decision: str
    rank_at_admission: int
    slot_budget: float | None
    reason: str | None
    created_at: datetime

    @staticmethod
    def make_id(candidate_id: str) -> str:
        """admission_id IS candidate_id (Section 8.2: one candidate, at most one
        admission decision, ever) -- this identity function exists only to make that
        invariant explicit and greppable at every call site, not to derive anything."""

        return candidate_id


@dataclass(frozen=True)
class SlotReservation:
    reservation_id: str
    replay_id: str
    admission_id: str
    candidate_id: str
    symbol: str
    reserved_amount: float
    status: str
    created_at: datetime
    resolved_at: datetime | None = None

    @staticmethod
    def make_id(admission_id: str) -> str:
        return f"{admission_id}:reservation"
