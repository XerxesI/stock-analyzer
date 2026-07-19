"""Daily portfolio equity snapshot -- Revision 5, Section 8.5. Exactly one row per
processed trading day, taken after that day's full entry/monitoring/candidate/
admission sequence. Drawdown and quarterly returns (diagnostics, Stages 12+) read
exclusively from this table, never recomputed with independently re-derived timing
elsewhere. All monetary fields are exact integers in domain/units.py's fixed-point
scale, never a float.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class PortfolioEquitySnapshot:
    snapshot_id: str
    replay_id: str
    as_of_date: date
    cash_units: int  # MONEY_SCALE
    reserved_capital_units: int  # MONEY_SCALE
    open_position_market_value_units: int  # MONEY_SCALE
    total_equity_units: int  # MONEY_SCALE
    open_position_count: int
    reserved_order_count: int
    cumulative_commissions_units: int  # MONEY_SCALE
    cumulative_slippage_cost_units: int  # MONEY_SCALE
    created_at: datetime

    @staticmethod
    def make_id(replay_id: str, as_of_date: date) -> str:
        return f"{replay_id}:{as_of_date.isoformat()}"
