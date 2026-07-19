"""Immutable per-fill execution audit record -- Revision 5, Section 18.

Sign convention (Stage 5, frozen here as the single source of truth for the whole
package): `quantity`, `gross_notional`, `commission`, `slippage_cost` are always
non-negative MAGNITUDES; direction is carried entirely by `side` and by the signed
`net_cash_flow` (negative for BUY -- cash leaves the portfolio; positive for SELL --
cash enters it). Both `raw_market_fill_price` (ADR-007's unadjusted simulated price)
and `effective_fill_price` (slippage-adjusted) are always persisted; neither is ever
derived from or overwrites the other after the fact.
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
    raw_market_fill_price: float
    effective_fill_price: float
    quantity: float
    gross_notional: float
    commission: float
    slippage_rate: float
    slippage_cost: float
    net_cash_flow: float
    fill_reason: str
    market_data_snapshot_id: str
    created_at: datetime

    @staticmethod
    def make_id(candidate_id: str, side: str, execution_date: date) -> str:
        return f"{candidate_id}:{side}:{execution_date.isoformat()}"
