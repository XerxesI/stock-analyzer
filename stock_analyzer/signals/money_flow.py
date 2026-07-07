"""Money Flow signals: does unusual volume / accumulation-distribution behavior
precede better swing outcomes? Per ChatGPT's Cycle #2 Step 3 guidance (Research
Protocol v1.2 Phase 4), tested as INDIVIDUAL phenomena, not a composite score -
same discipline as Relative Strength and the original Trade Score v1 lesson.

Implemented (all causal - only use trailing/rolling windows, no look-ahead):
    RVOL      - Relative Volume: today's volume vs its own trailing rolling average.
                Above 1.0 = higher than usual (recent) trading activity.
    OBV_SLOPE - On-Balance Volume slope, normalized by trailing volume so it's
                comparable across stocks of different sizes/liquidity.
    AD_SLOPE  - Accumulation/Distribution line slope, same normalization approach.

Both OBV and A/D are CUMULATIVE indicators (their raw level depends on how long
the price history is and the stock's total volume) - looking at their raw values
or even raw differences would not be comparable across stocks. Normalizing the
slope by the trailing volume sum over the same window turns it into a "net
buying/selling pressure per unit of volume traded" measure, which is roughly
scale-free and comparable across different stocks.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta

DEFAULT_RVOL_WINDOW = 20
DEFAULT_SLOPE_WINDOW = 10


def calculate_money_flow_features(
    df: pd.DataFrame,
    rvol_window: int = DEFAULT_RVOL_WINDOW,
    slope_window: int = DEFAULT_SLOPE_WINDOW,
) -> pd.DataFrame:
    """Compute RVOL, OBV slope, and A/D slope - causal, vectorized.

    Args:
        df: OHLCV DataFrame with High, Low, Close, Volume columns, indexed
            chronologically.
        rvol_window: Trailing window (trading days) for RVOL's average volume.
        slope_window: Trailing window (trading days) for the OBV/A-D slope
            differencing and volume normalization.

    Returns:
        DataFrame aligned to ``df``'s index with columns:
            rvol: today's Volume / trailing rvol_window-day average Volume
            obv_slope: (OBV(t) - OBV(t-slope_window)) / trailing volume sum
                over the same window (net buying pressure per unit volume)
            ad_slope: same normalization, for the Accumulation/Distribution line

    Raises:
        ValueError: If required columns are missing or df is empty.
    """

    required = {"High", "Low", "Close", "Volume"}
    if df.empty:
        raise ValueError("Input data is empty.")
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input data is missing required columns: {sorted(missing)}")

    volume = df["Volume"]
    rolling_avg_volume = volume.rolling(rvol_window).mean()
    rvol = volume / rolling_avg_volume

    obv = ta.obv(close=df["Close"], volume=volume)
    ad = ta.ad(high=df["High"], low=df["Low"], close=df["Close"], volume=volume)

    trailing_volume_sum = volume.rolling(slope_window).sum()
    obv_slope = (obv - obv.shift(slope_window)) / trailing_volume_sum
    ad_slope = (ad - ad.shift(slope_window)) / trailing_volume_sum

    return pd.DataFrame(
        {"rvol": rvol, "obv_slope": obv_slope, "ad_slope": ad_slope},
        index=df.index,
    )
