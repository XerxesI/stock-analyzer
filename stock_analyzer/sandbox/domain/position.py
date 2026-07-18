"""A virtual position (current-state) and its append-only daily snapshots.

Holding-day convention: the entry day is holding day 1 (mirrors the frozen SWING_20
label's own window definition -- see MVP 2 spec section 11).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

OPEN = "OPEN"
CLOSED = "CLOSED"


@dataclass
class VirtualPosition:
    position_id: str
    symbol: str
    candidate_id: str
    order_id: str
    signal_date: date
    entry_date: date
    entry_price: float
    quantity: float
    initial_rank: int
    initial_model_score: float
    signal_close: float
    max_entry_price: float
    initial_adv_quintile: str | None
    initial_market_regime: str | None
    target_price: float
    planned_time_exit_date: date
    status: str = OPEN
    current_holding_day_count: int = 0
    current_close: float | None = None
    unrealized_return: float | None = None
    mfe: float = 0.0
    mae: float = 0.0
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    realized_return: float | None = None

    @staticmethod
    def make_id(symbol: str, entry_date: date) -> str:
        return f"{symbol}:{entry_date.isoformat()}"


@dataclass(frozen=True)
class PositionSnapshot:
    snapshot_id: str
    position_id: str
    symbol: str
    as_of_date: date
    close_price: float | None
    daily_return: float | None
    cumulative_unrealized_return: float | None
    holding_day_count: int
    mfe: float
    mae: float
    distance_to_target: float | None
    current_rank: int | None
    current_model_score: float | None
    rank_change_from_entry: int | None
    current_adv_quintile: str | None
    current_market_regime: str | None
    data_quality_status: str
    recommendation: str

    @staticmethod
    def make_id(position_id: str, as_of_date: date) -> str:
        return f"{position_id}:{as_of_date.isoformat()}"
