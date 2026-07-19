"""Portfolio admission and slot reservation -- Revision 5, Section 8.1.

`PortfolioAdmission` is the portfolio-level ACCEPTED/NO_CAPACITY decision for an
already-independently-actionable `RankedCandidate` (see
stock_analyzer.sandbox.domain.candidate.RankedCandidate). It never mutates that
candidate's `actionable`/`exclusion_reason` -- ranking and portfolio admission are
deliberately separate facts (Section 8.1). `portfolio_admissions` itself IS
append-only (one row per candidate_id, ever); `slot_reservations` is NOT -- it is a
mutable current-state table, the same category as the core sandbox's
`entry_orders`/`virtual_positions`, whose `status` column transitions
RESERVED -> CONVERTED or RESERVED -> RELEASED on the SAME row (Section 8.3). Do not
describe `slot_reservations` as an immutable/append-only fact table anywhere in this
codebase's documentation -- it is not one.

All monetary fields are exact integers in domain/units.py's fixed-point scale, never
a float.
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
    slot_budget_units: int | None  # MONEY_SCALE; None iff decision == NO_CAPACITY
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
    reserved_amount_units: int  # MONEY_SCALE
    status: str  # mutable current-state field -- see module docstring
    created_at: datetime
    resolved_at: datetime | None = None

    @staticmethod
    def make_id(admission_id: str) -> str:
        return f"{admission_id}:reservation"
