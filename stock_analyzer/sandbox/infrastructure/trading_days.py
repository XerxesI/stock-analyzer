"""Naive calendar-day trading-session approximation (weekends skipped; US market
holidays not modeled in MVP 2 -- see MVP 2 spec section 18, known provisional
assumptions). Used only for informational/display date estimates (order validity
window display, planned time-exit date). Never used for point-in-time correctness --
actual entry/exit timing is always driven by counting real observed trading-day bars
(entry_order_attempts, position_snapshots), not by matching a calendar estimate.
"""

from __future__ import annotations

from datetime import date


def add_trading_sessions(start: date, sessions: int) -> date:
    current = start
    remaining = sessions
    while remaining > 0:
        current = date.fromordinal(current.toordinal() + 1)
        if current.weekday() < 5:
            remaining -= 1
    return current
