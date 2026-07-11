"""Date-specific eligible-universe baseline calculations."""

from __future__ import annotations

import pandas as pd


def daily_baseline(labels: pd.DataFrame) -> pd.DataFrame:
    """Compute target-hit rate for the eligible universe on each signal date."""

    if labels.empty:
        return pd.DataFrame(columns=["date", "eligible_count", "positive_count", "daily_positive_rate"])
    required = {"date", "target_20pct_20d"}
    missing = required - set(labels.columns)
    if missing:
        raise ValueError(f"Labels frame is missing required columns: {sorted(missing)}")

    grouped = labels.copy()
    grouped["date"] = pd.to_datetime(grouped["date"])
    result = (
        grouped.groupby("date")["target_20pct_20d"]
        .agg(eligible_count="size", positive_count="sum")
        .reset_index()
    )
    result["positive_count"] = result["positive_count"].astype(int)
    result["daily_positive_rate"] = result["positive_count"] / result["eligible_count"]
    return result


def baseline_summary(labels: pd.DataFrame) -> dict[str, object]:
    """Return aggregate date-specific baseline diagnostics."""

    daily = daily_baseline(labels)
    if daily.empty:
        return {
            "daily_count": 0,
            "mean_daily_positive_rate": None,
            "median_daily_positive_rate": None,
        }
    return {
        "daily_count": int(len(daily)),
        "mean_daily_positive_rate": float(daily["daily_positive_rate"].mean()),
        "median_daily_positive_rate": float(daily["daily_positive_rate"].median()),
        "min_daily_positive_rate": float(daily["daily_positive_rate"].min()),
        "max_daily_positive_rate": float(daily["daily_positive_rate"].max()),
    }

