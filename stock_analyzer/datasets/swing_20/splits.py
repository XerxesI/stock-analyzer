"""Temporal split helpers for SWING_20."""

from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.config import SplitConfig


def assign_temporal_splits(labels: pd.DataFrame, config: SplitConfig = SplitConfig()) -> pd.DataFrame:
    """Assign train/validation/locked_test labels by sorted unique signal date."""

    config.validate()
    if labels.empty:
        result = labels.copy()
        result["split"] = pd.Series(dtype=object)
        return result
    if "date" not in labels.columns:
        raise ValueError("Labels frame must contain a date column.")

    result = labels.copy()
    result["date"] = pd.to_datetime(result["date"])
    dates = sorted(result["date"].dropna().unique())
    if len(dates) < 3:
        raise ValueError("At least three unique dates are required for temporal splits.")

    train_end = max(1, int(len(dates) * config.train_fraction))
    validation_end = max(train_end + 1, int(len(dates) * (config.train_fraction + config.validation_fraction)))
    validation_end = min(validation_end, len(dates) - 1)

    train_dates = set(dates[:train_end])
    validation_dates = set(dates[train_end:validation_end])

    def split_for(date: pd.Timestamp) -> str:
        if date in train_dates:
            return "train"
        if date in validation_dates:
            return "validation"
        return "locked_test"

    result["split"] = result["date"].map(split_for)
    return result


def split_summary(labels: pd.DataFrame) -> dict[str, dict[str, object]]:
    """Return observation and positive-label counts by split."""

    if labels.empty or "split" not in labels.columns:
        return {}
    summary: dict[str, dict[str, object]] = {}
    for split, group in labels.groupby("split", sort=False):
        positives = int(group["target_20pct_20d"].sum()) if "target_20pct_20d" in group else 0
        summary[str(split)] = {
            "start": str(pd.to_datetime(group["date"]).min().date()) if "date" in group else None,
            "end": str(pd.to_datetime(group["date"]).max().date()) if "date" in group else None,
            "observations": int(len(group)),
            "raw_positive_observations": positives,
            "positive_rate": float(positives / len(group)) if len(group) else None,
        }
    return summary

