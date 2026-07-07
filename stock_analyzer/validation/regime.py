"""Market regime tagging: trend + volatility, per Research Protocol v1.2 section 4.3.

Phase 1 deliberately uses only two dimensions (trend, volatility) - breadth and sector
rotation are noted in the Protocol as future extensions and are NOT implemented here,
but the output DataFrame is a plain date-indexed table with one column per dimension,
so adding a third dimension later is just adding a column, not a redesign.

Volatility uses a 3-tier fallback chain (Protocol section 4.3):
    1. ^VIX (direct, preferred)
    2. SPY realized volatility (20-day rolling, annualized) - used if VIX data is
       unavailable or unreliable
    3. SPY ATR% (last-resort proxy, terciles instead of fixed thresholds since the
       scale isn't directly comparable to VIX)

Causality note: a regime tag for date `t` uses SPY/VIX data available AT `t` (same-day
close), matching the convention used elsewhere in this project (e.g.
validation/labeling.py's entry_price = Close at t). This is not a look-ahead violation:
the regime for day t is knowable at the close of day t, same as any other same-day
indicator value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

VIX_LOW_THRESHOLD = 15.0
VIX_HIGH_THRESHOLD = 25.0

REALIZED_VOL_WINDOW = 20
TRADING_DAYS_PER_YEAR = 252


def _trend_regime(spy_close: pd.Series, spy_sma200: pd.Series) -> pd.Series:
    """Bull if SPY Close > SMA200, else Bear. NaN (warm-up period) stays NaN."""

    trend = pd.Series(index=spy_close.index, dtype=object)
    valid = spy_sma200.notna()
    trend.loc[valid & (spy_close > spy_sma200)] = "Bull"
    trend.loc[valid & (spy_close <= spy_sma200)] = "Bear"
    return trend


def _volatility_bucket(level: pd.Series) -> pd.Series:
    bucket = pd.Series(index=level.index, dtype=object)
    valid = level.notna()
    bucket.loc[valid & (level < VIX_LOW_THRESHOLD)] = "Low"
    bucket.loc[valid & (level >= VIX_LOW_THRESHOLD) & (level < VIX_HIGH_THRESHOLD)] = "Normal"
    bucket.loc[valid & (level >= VIX_HIGH_THRESHOLD)] = "High"
    return bucket


def _realized_volatility(spy_close: pd.Series, window: int = REALIZED_VOL_WINDOW) -> pd.Series:
    """Annualized realized volatility (in VIX-comparable percentage-point units)."""

    returns = spy_close.pct_change()
    return returns.rolling(window).std() * np.sqrt(TRADING_DAYS_PER_YEAR) * 100


def _atr_pct_tercile_bucket(atr_pct: pd.Series) -> pd.Series:
    """Last-resort volatility bucket: terciles of the ATR% series itself.

    Not directly comparable to VIX thresholds, so we use relative buckets
    (lowest/middle/highest third of the AVAILABLE history) instead of fixed levels.
    """

    valid = atr_pct.dropna()
    if len(valid) < 30:
        return pd.Series(index=atr_pct.index, dtype=object)
    low_cut, high_cut = valid.quantile([1 / 3, 2 / 3])
    bucket = pd.Series(index=atr_pct.index, dtype=object)
    mask = atr_pct.notna()
    bucket.loc[mask & (atr_pct < low_cut)] = "Low"
    bucket.loc[mask & (atr_pct >= low_cut) & (atr_pct < high_cut)] = "Normal"
    bucket.loc[mask & (atr_pct >= high_cut)] = "High"
    return bucket


def build_market_regime(
    spy_df: pd.DataFrame,
    vix_close: pd.Series | None = None,
) -> pd.DataFrame:
    """Build a date-indexed market regime table (trend + volatility dimensions).

    Args:
        spy_df: SPY OHLCV DataFrame, already enriched with SMA200 (and ideally ATR14)
            via ``core.indicators.calculate_indicators``.
        vix_close: Optional ^VIX Close series, same date index convention as spy_df.
            If None or mostly empty, falls back to SPY realized volatility, then to
            SPY ATR% terciles (see module docstring for the fallback chain).

    Returns:
        DataFrame indexed by date with columns:
            trend: "Bull" | "Bear" | NaN (during SMA200 warm-up)
            volatility: "Low" | "Normal" | "High" | NaN
            volatility_source: "vix" | "realized_spy" | "atr_spy" (constant per run,
                recorded for transparency/reproducibility)
            regime: combined label, e.g. "Bull_Low" (NaN if either input is NaN)

    Raises:
        ValueError: If ``spy_df`` is empty or missing required columns.
    """

    if spy_df.empty:
        raise ValueError("spy_df is empty.")
    if "Close" not in spy_df.columns or "SMA200" not in spy_df.columns:
        raise ValueError("spy_df must contain Close and SMA200 (run calculate_indicators first).")

    trend = _trend_regime(spy_df["Close"], spy_df["SMA200"])

    volatility_source = "vix"
    volatility = pd.Series(dtype=object)
    if vix_close is not None:
        vix_aligned = vix_close.reindex(spy_df.index)
        if vix_aligned.notna().sum() >= 30:
            volatility = _volatility_bucket(vix_aligned)
        else:
            vix_close = None  # fall through

    if vix_close is None or volatility.empty or volatility.notna().sum() < 30:
        realized = _realized_volatility(spy_df["Close"])
        if realized.notna().sum() >= 30:
            volatility = _volatility_bucket(realized)
            volatility_source = "realized_spy"
        elif "ATR14" in spy_df.columns:
            atr_pct = spy_df["ATR14"] / spy_df["Close"] * 100
            volatility = _atr_pct_tercile_bucket(atr_pct)
            volatility_source = "atr_spy"
        else:
            volatility = pd.Series(index=spy_df.index, dtype=object)
            volatility_source = "unavailable"

    regime = pd.Series(index=spy_df.index, dtype=object)
    both_valid = trend.notna() & volatility.notna()
    regime.loc[both_valid] = trend.loc[both_valid] + "_" + volatility.loc[both_valid]

    return pd.DataFrame(
        {
            "trend": trend,
            "volatility": volatility,
            "volatility_source": volatility_source,
            "regime": regime,
        },
        index=spy_df.index,
    )


def tag_observations(
    obs: pd.DataFrame,
    regime_df: pd.DataFrame,
    date_col: str = "date",
) -> pd.DataFrame:
    """Attach regime columns (trend, volatility, regime) to an observations DataFrame.

    Uses a forward-filled as-of join so that observation dates falling on non-SPY
    trading days (shouldn't normally happen, but defensively handled) still get a
    regime tag from the most recent prior SPY session.

    Args:
        obs: Long-format observations DataFrame (e.g. from validation.labeling.label_frame,
            possibly joined with a signal), containing a date column.
        regime_df: Output of ``build_market_regime``.
        date_col: Name of the date column in ``obs``.

    Returns:
        Copy of ``obs`` with added columns: trend, volatility, regime.
    """

    regime_sorted = regime_df.sort_index()
    regime_sorted.index = pd.to_datetime(regime_sorted.index).astype("datetime64[ns]")

    obs_sorted = obs.sort_values(date_col).copy()
    obs_sorted[date_col] = pd.to_datetime(obs_sorted[date_col]).astype("datetime64[ns]")

    merged = pd.merge_asof(
        obs_sorted,
        regime_sorted[["trend", "volatility", "regime"]],
        left_on=date_col,
        right_index=True,
        direction="backward",
    )
    return merged