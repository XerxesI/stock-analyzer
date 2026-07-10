"""Volatility Compression signal: is the stock's recent price range unusually tight
relative to its own history? Per ChatGPT's Cycle #2 direction, tested first as a
pure STATE diagnostic (does compression predict outcome DISTRIBUTION - direction
and/or amplitude?) before any activation/breakout trigger is considered.

Implemented:
    compression_pct - where today's Bollinger Band Width sits relative to its own
    trailing lookback window's min/max (0 = tightest bands seen in the window,
    i.e. maximum compression; 1 = widest bands seen, i.e. maximum expansion).

    Uses the existing BBU/BBL/BBM columns from core.indicators.calculate_indicators
    (no new indicator library calls needed). This is a computationally cheap
    proxy for a rolling percentile rank - conceptually the same idea (ChatGPT's
    "ATR percentile" / "Bollinger Band Width percentile" / "realized-vol
    percentile") without the cost of a rolling apply over every window.

Causality: rolling min/max only look backward - no look-ahead risk.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_LOOKBACK = 100  # trailing window for the compression percentile proxy


def calculate_compression_state(
    df: pd.DataFrame,
    lookback: int = DEFAULT_LOOKBACK,
) -> pd.DataFrame:
    """Compute Bollinger Band Width and its trailing-window compression percentile.

    Args:
        df: DataFrame with BBU, BBL, BBM columns (from calculate_indicators).
        lookback: Trailing window (trading days) used to normalize today's
            bandwidth against its own recent range.

    Returns:
        DataFrame aligned to df's index with columns:
            bbw: Bollinger Band Width = (BBU - BBL) / BBM
            compression_pct: (bbw - rolling_min) / (rolling_max - rolling_min),
                0 = tightest (most compressed) in the lookback window,
                1 = widest (most expanded) in the lookback window

    Raises:
        ValueError: If required columns are missing or df is empty.
    """

    if df.empty:
        raise ValueError("Input data is empty.")
    required = {"BBU", "BBL", "BBM"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input data is missing required columns: {sorted(missing)}")

    bbw = (df["BBU"] - df["BBL"]) / df["BBM"]
    rolling_min = bbw.rolling(lookback).min()
    rolling_max = bbw.rolling(lookback).max()
    range_ = rolling_max - rolling_min
    compression_pct = (bbw - rolling_min) / range_.where(range_ != 0)

    return pd.DataFrame({"bbw": bbw, "compression_pct": compression_pct}, index=df.index)
