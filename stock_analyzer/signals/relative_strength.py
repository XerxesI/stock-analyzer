"""Relative Strength signals: does a stock outperform a benchmark, and is that
outperformance improving? Per ChatGPT's Cycle #2 guidance (Research Protocol v1.2
Phase 4), these are tested as INDIVIDUAL phenomena, not a composite "RS Score" -
same discipline that Trade Score v1's failure taught us.

Implemented now (require only SPY, which the pipeline already fetches for regime):
    RS1 - stock's return vs SPY's return over the same lookback window
    RS3 - RS slope: is the outperformance widening or narrowing recently?
    RS4 - RS acceleration: is the slope itself speeding up or slowing down?

NOT implemented yet (deferred - Cycle #2 note):
    RS2 - stock vs SECTOR return. Requires (a) a reliable sector label per ticker
    and (b) a sector benchmark return series (e.g. mapping sector -> ETF like XLK,
    XLF, XLE). The project's existing sector lookup (data/fundamentals.py) has a
    known reliability problem (yfinance sector queries rate-limit and fall back to
    "unknown" for many tickers - see prior project notes). Wiring RS2 up properly
    is a separate small project, not done in this module yet.

Causality: all three signals use only pct_change() over a trailing window, which by
construction only looks backward from the evaluation date - no look-ahead risk.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_LOOKBACK = 20  # primary, per Protocol/ChatGPT pre-registration
DEFAULT_SLOPE_WINDOW = 5  # short window for slope/acceleration


def calculate_relative_strength(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    lookback: int = DEFAULT_LOOKBACK,
    slope_window: int = DEFAULT_SLOPE_WINDOW,
) -> pd.DataFrame:
    """Compute RS1 (vs benchmark), RS3 (slope), RS4 (acceleration) - causal, vectorized.

    Args:
        stock_close: Stock's Close price series, indexed chronologically.
        benchmark_close: Benchmark's (e.g. SPY) Close price series. Will be
            reindexed to ``stock_close``'s index (forward-filled) if the calendars
            don't match exactly.
        lookback: Trading days for the base return comparison (RS1). Default 20
            (primary horizon, per pre-registration - avoid testing many lookbacks
            and picking the best one after the fact).
        slope_window: Trading days for the slope/acceleration differencing.

    Returns:
        DataFrame aligned to ``stock_close``'s index with columns:
            rs: stock's trailing `lookback`-day return minus benchmark's
            rs_slope: rs(t) - rs(t - slope_window)  (positive = strengthening)
            rs_accel: rs_slope(t) - rs_slope(t - slope_window)

    Raises:
        ValueError: If either input series is empty.
    """

    if stock_close.empty or benchmark_close.empty:
        raise ValueError("Input series must not be empty.")

    benchmark_aligned = benchmark_close.reindex(stock_close.index).ffill()

    stock_return = stock_close.pct_change(lookback)
    benchmark_return = benchmark_aligned.pct_change(lookback)
    rs = stock_return - benchmark_return

    rs_slope = rs - rs.shift(slope_window)
    rs_accel = rs_slope - rs_slope.shift(slope_window)

    return pd.DataFrame({"rs": rs, "rs_slope": rs_slope, "rs_accel": rs_accel}, index=stock_close.index)
