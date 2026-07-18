"""Append-only recommendation event. The fixed MVP 2 vocabulary lives here as constants
so services and tests reference one source of truth (MVP 2 spec section 11)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Candidate-level
BUY_PENDING = "BUY_PENDING"
BUY_FILLED = "BUY_FILLED"
SKIP_PRICE_TOO_HIGH = "SKIP_PRICE_TOO_HIGH"
EXPIRED_ENTRY = "EXPIRED_ENTRY"
SKIP_DATA_QUALITY = "SKIP_DATA_QUALITY"
SKIP_ALREADY_OPEN = "SKIP_ALREADY_OPEN"

# Position-level
HOLD = "HOLD"
SELL_TARGET = "SELL_TARGET"
SELL_TIME = "SELL_TIME"
SELL_DATA_FAILURE = "SELL_DATA_FAILURE"

CANDIDATE_RECOMMENDATIONS = frozenset(
    {BUY_PENDING, BUY_FILLED, SKIP_PRICE_TOO_HIGH, EXPIRED_ENTRY, SKIP_DATA_QUALITY, SKIP_ALREADY_OPEN}
)
POSITION_RECOMMENDATIONS = frozenset({HOLD, SELL_TARGET, SELL_TIME, SELL_DATA_FAILURE})

ENTITY_CANDIDATE = "candidate"
ENTITY_POSITION = "position"


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    entity_type: str
    entity_id: str
    symbol: str
    as_of_date: date
    recommendation: str
    reason: str | None

    @staticmethod
    def make_id(entity_type: str, entity_id: str, as_of_date: date) -> str:
        return f"{entity_type}:{entity_id}:{as_of_date.isoformat()}"
