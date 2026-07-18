"""A single (symbol, as-of date) row of the daily shadow top-10 ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RankedCandidate:
    candidate_id: str
    run_id: str
    as_of_date: date
    symbol: str
    daily_rank: int
    model_score: float
    signal_close: float
    atr14: float | None
    max_entry_price: float | None
    shadow_top10: bool
    actionable: bool
    exclusion_reason: str | None
    adv_quintile: str | None
    market_regime: str | None

    @staticmethod
    def make_id(as_of_date: date, symbol: str) -> str:
        return f"{as_of_date.isoformat()}:{symbol}"
