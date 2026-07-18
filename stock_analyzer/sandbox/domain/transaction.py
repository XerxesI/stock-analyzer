"""Append-only virtual BUY/SELL execution log entry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

BUY = "BUY"
SELL = "SELL"


@dataclass(frozen=True)
class VirtualTransaction:
    transaction_id: str
    position_id: str
    symbol: str
    transaction_type: str
    transaction_date: date
    price: float
    quantity: float
    notional: float
    reason: str

    @staticmethod
    def make_id(position_id: str, transaction_type: str, transaction_date: date) -> str:
        return f"{position_id}:{transaction_type}:{transaction_date.isoformat()}"
