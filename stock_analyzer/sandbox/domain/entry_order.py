"""A pending/filled/expired virtual entry order, plus its per-session attempt log.

See docs/04_decisions/ADR-007-Next-Day-Entry-Simulation.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

PENDING = "PENDING"
FILLED = "FILLED"
EXPIRED = "EXPIRED"
SKIPPED = "SKIPPED"

FILLED_AT_OPEN = "FILLED_AT_OPEN"
FILLED_AT_CEILING = "FILLED_AT_CEILING"
NO_FILL = "NO_FILL"


@dataclass
class EntryOrder:
    order_id: str
    candidate_id: str
    symbol: str
    signal_date: date
    created_date: date
    valid_until: date
    max_entry_price: float
    status: str
    fill_date: date | None = None
    fill_price: float | None = None
    fill_reason: str | None = None
    no_fill_reason: str | None = None

    @staticmethod
    def make_id(candidate_id: str) -> str:
        return f"{candidate_id}:order"


@dataclass(frozen=True)
class EntryOrderAttempt:
    attempt_id: str
    order_id: str
    symbol: str
    attempt_date: date
    session_open: float | None
    session_high: float | None
    session_low: float | None
    session_close: float | None
    max_entry_price: float
    outcome: str
    fill_price: float | None
    reason: str | None

    @staticmethod
    def make_id(order_id: str, attempt_date: date) -> str:
        return f"{order_id}:{attempt_date.isoformat()}"
