"""Append-only data-quality finding (missing bar, stale data, corporate action, ...)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
INVALID_PRICE = "INVALID_PRICE"
MISSING_ATR = "MISSING_ATR"
STALE_DATA = "STALE_DATA"
CORPORATE_ACTION_OR_SYMBOL_INTEGRITY_FAILURE = "CORPORATE_ACTION_OR_SYMBOL_INTEGRITY_FAILURE"
NO_NEXT_SESSION_DATA = "NO_NEXT_SESSION_DATA"


@dataclass(frozen=True)
class DataQualityEvent:
    event_id: str
    symbol: str
    as_of_date: date
    event_type: str
    details: str | None

    @staticmethod
    def make_id(symbol: str, as_of_date: date, event_type: str) -> str:
        return f"{symbol}:{as_of_date.isoformat()}:{event_type}"
