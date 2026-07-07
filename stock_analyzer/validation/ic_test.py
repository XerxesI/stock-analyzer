"""Walk-forward IC testing with multi-horizon support, hold-out split, and diagnostic
segment analysis. Implements Research Protocol v1.2 sections 4.2, 4.4, 4.5.

This module is deliberately generic: it operates on a long-format "observations"
DataFrame (one row per date/symbol/horizon, with a signal column and a target column -
typically produced by joining a signal's values with validation.labeling.label_frame's
output). It does not know or care which specific signal or which specific target
(success, mfe, mae, r_multiple) is being tested - that is the caller's choice, per the
Protocol's "keep components separate" principle (section 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

HARD_MIN_N = 50  # below this, no segment analysis at all (Protocol section 4.4)
SOFT_MIN_N = 200  # below this, exploratory only, flagged "Low Statistical Confidence"


def time_split(
    dates: pd.Series | pd.Index,
    train_fraction: float = 0.8,
) -> pd.Timestamp:
    """Return the cutoff date for an 80/20 (default) chronological train/hold-out split.

    Per Protocol section 4.2: simple time-based split, not rolling/expanding window.

    Args:
        dates: All observation dates (duplicates fine, only order matters).
        train_fraction: Fraction of the date RANGE (by unique sorted dates) assigned
            to the training/exploratory period.

    Returns:
        The cutoff timestamp: dates <= cutoff are training, dates > cutoff are hold-out.
    """

    unique_dates = pd.Series(pd.to_datetime(pd.Series(dates).unique())).sort_values()
    if unique_dates.empty:
        raise ValueError("No dates provided.")
    cutoff_idx = int(len(unique_dates) * train_fraction) - 1
    cutoff_idx = max(0, min(cutoff_idx, len(unique_dates) - 1))
    return unique_dates.iloc[cutoff_idx]


def split_train_holdout(
    obs: pd.DataFrame,
    date_col: str = "date",
    train_fraction: float = 0.8,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split observations into (train, holdout) DataFrames by ``time_split``."""

    cutoff = time_split(obs[date_col], train_fraction=train_fraction)
    train = obs[obs[date_col] <= cutoff].copy()
    holdout = obs[obs[date_col] > cutoff].copy()
    return train, holdout


def spearman_ic(signal: pd.Series, target: pd.Series) -> float:
    """Spearman rank correlation between a signal and a target, NaN-safe."""

    paired = pd.DataFrame({"signal": signal, "target": target}).dropna()
    if len(paired) < 2:
        return float("nan")
    return paired["signal"].rank().corr(paired["target"].rank())


@dataclass
class HorizonICResult:
    horizon: int
    train_ic: float
    train_n: int
    holdout_ic: float
    holdout_n: int


def run_walk_forward_ic(
    obs: pd.DataFrame,
    signal_col: str,
    target_col: str,
    horizon_col: str = "horizon",
    date_col: str = "date",
    train_fraction: float = 0.8,
) -> list[HorizonICResult]:
    """Compute IC per horizon, separately for the training and hold-out periods.

    Args:
        obs: Long-format observations with columns [date_col, horizon_col, signal_col,
            target_col] at minimum.
        signal_col: Column holding the signal's value at each observation.
        target_col: Column holding the label/target (e.g. "r_multiple", "mfe", or a
            0/1 "success" column) to correlate the signal against.
        horizon_col: Column identifying which horizon (in trading days) each row is for.
        date_col: Column holding the observation date.
        train_fraction: Passed to ``split_train_holdout``.

    Returns:
        One ``HorizonICResult`` per unique horizon found in ``obs``, sorted by horizon.
    """

    train, holdout = split_train_holdout(obs, date_col=date_col, train_fraction=train_fraction)

    results = []
    for horizon in sorted(obs[horizon_col].unique()):
        train_h = train[train[horizon_col] == horizon]
        holdout_h = holdout[holdout[horizon_col] == horizon]

        train_valid = train_h[[signal_col, target_col]].dropna()
        holdout_valid = holdout_h[[signal_col, target_col]].dropna()

        results.append(
            HorizonICResult(
                horizon=int(horizon),
                train_ic=spearman_ic(train_h[signal_col], train_h[target_col]),
                train_n=len(train_valid),
                holdout_ic=spearman_ic(holdout_h[signal_col], holdout_h[target_col]),
                holdout_n=len(holdout_valid),
            )
        )
    return results


def quintile_summary(
    obs: pd.DataFrame,
    signal_col: str,
    target_col: str,
) -> pd.DataFrame | None:
    """Mean target value by signal quintile (Q1=lowest signal, Q5=highest).

    Returns None if there isn't enough data to form 5 distinct quantile bins.
    """

    sub = obs[[signal_col, target_col]].dropna().copy()
    if len(sub) < 50:
        return None
    try:
        sub["quintile"] = pd.qcut(sub[signal_col], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
    except ValueError:
        return None
    summary = sub.groupby("quintile", observed=True)[target_col].agg(["mean", "count"])
    return summary


@dataclass
class SegmentDiagnosticResult:
    """Result of one diagnostic segment test (Protocol section 4.4).

    confidence is one of:
        "insufficient_data"       n < HARD_MIN_N, no IC computed
        "low_statistical_confidence"  HARD_MIN_N <= n < SOFT_MIN_N, IC computed but flagged
        "ok"                      n >= SOFT_MIN_N
    """

    segment_value: Any
    n: int
    ic: float
    confidence: str


def diagnostic_segment_ic(
    obs: pd.DataFrame,
    signal_col: str,
    target_col: str,
    segment_col: str,
    hard_min_n: int = HARD_MIN_N,
    soft_min_n: int = SOFT_MIN_N,
) -> list[SegmentDiagnosticResult]:
    """Compute IC separately per segment value, with explicit confidence flags.

    IMPORTANT (Protocol section 4.4): this function is DIAGNOSTIC, not confirmatory.
    It must only be called for a segment_col that corresponds to a hypothesis stated
    BEFORE looking at results (e.g. "RVOL should work better in the High-ATR segment")
    - not used to trawl through every possible segmentation looking for something that
    "works". Any finding that influences a decision must still be confirmed on the
    hold-out set (this function does not do that split itself - call it separately on
    train and holdout data if you need both).

    Args:
        obs: Long-format observations with [signal_col, target_col, segment_col].
        signal_col: Column holding the signal's value.
        target_col: Column holding the target/label.
        segment_col: Column defining the segments to break out (e.g. "regime",
            "volatility", a market-cap bucket, etc.)
        hard_min_n: Below this sample size, no IC is computed at all.
        soft_min_n: Below this (but >= hard_min_n), IC is computed but flagged
            "low_statistical_confidence".

    Returns:
        One ``SegmentDiagnosticResult`` per distinct segment value present in ``obs``.
    """

    results = []
    for segment_value, group in obs.groupby(segment_col, observed=True):
        valid = group[[signal_col, target_col]].dropna()
        n = len(valid)
        if n < hard_min_n:
            results.append(
                SegmentDiagnosticResult(
                    segment_value=segment_value, n=n, ic=float("nan"), confidence="insufficient_data"
                )
            )
            continue
        ic = spearman_ic(group[signal_col], group[target_col])
        confidence = "low_statistical_confidence" if n < soft_min_n else "ok"
        results.append(
            SegmentDiagnosticResult(segment_value=segment_value, n=n, ic=ic, confidence=confidence)
        )
    return results
