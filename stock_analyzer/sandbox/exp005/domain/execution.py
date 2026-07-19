"""Immutable per-fill execution audit record -- Revision 5, Section 18.

Every numeric field is an exact integer, in the fixed-point units defined by
domain/units.py -- never a float, never a SQLite REAL. Sign convention (Stage 5,
frozen here as the single source of truth for the whole package): `quantity_units`,
`gross_notional_units`, `commission_units`, `slippage_cost_units` are always
non-negative MAGNITUDES; direction is carried entirely by `side` and by the signed
`net_cash_flow_units` (negative for BUY -- cash leaves the portfolio; positive for
SELL -- cash enters it). Both `raw_market_fill_price_units` (ADR-007's unadjusted
simulated price) and `effective_fill_price_units` (slippage-adjusted) are always
persisted; neither is ever derived from or overwrites the other after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

BUY = "BUY"
SELL = "SELL"


@dataclass(frozen=True)
class Execution:
    execution_id: str
    replay_id: str
    variant_id: str
    control_seed: int | None
    order_id: str | None  # BUY side only
    candidate_id: str
    position_id: str | None
    symbol: str
    side: str
    decision_date: date
    execution_date: date
    raw_market_fill_price_units: int  # PRICE_SCALE
    effective_fill_price_units: int  # PRICE_SCALE
    quantity_units: int  # QUANTITY_SCALE
    gross_notional_units: int  # MONEY_SCALE
    commission_units: int  # MONEY_SCALE
    slippage_rate_units: int  # RATE_SCALE (basis points)
    slippage_cost_units: int  # MONEY_SCALE
    net_cash_flow_units: int  # MONEY_SCALE
    fill_reason: str
    market_data_snapshot_id: str
    created_at: datetime

    @staticmethod
    def make_id(candidate_id: str, side: str, execution_date: date) -> str:
        return f"{candidate_id}:{side}:{execution_date.isoformat()}"
