"""Daily portfolio equity snapshot -- Revision 5, Section 8.5. Exactly one row per
processed trading day, taken after that day's full entry/monitoring/candidate/
admission sequence -- see application/replay.py (Stage 8) for where it is written.
Drawdown and quarterly returns (diagnostics, Stages 12+) read exclusively from this
table, never recomputed with independently re-derived timing elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class PortfolioEquitySnapshot:
    snapshot_id: str
    replay_id: str
    as_of_date: date
    cash: float
    reserved_capital: float
    open_position_market_value: float
    total_equity: float
    open_position_count: int
    reserved_order_count: int
    cumulative_commissions: float
    cumulative_slippage_cost: float
    created_at: datetime

    @staticmethod
    def make_id(replay_id: str, as_of_date: date) -> str:
        return f"{replay_id}:{as_of_date.isoformat()}"
