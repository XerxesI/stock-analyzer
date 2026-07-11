"""Data-quality and trainability decision helpers for SWING_20."""

from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.schema import AuditDecision


def ohlcv_quality_counts(df: pd.DataFrame) -> dict[str, int]:
    """Return basic OHLCV quality counts for one price frame."""

    counts = {
        "ohlc_inconsistency_count": 0,
        "stale_price_count": 0,
        "duplicate_bar_count": 0,
        "extreme_gap_count": 0,
    }
    if df.empty:
        return counts

    ordered = df.sort_index()
    counts["duplicate_bar_count"] = int(ordered.index.duplicated().sum())
    required = {"Open", "High", "Low", "Close"}
    if required.issubset(ordered.columns):
        invalid_ohlc = (
            (ordered["High"] < ordered[["Open", "Close", "Low"]].max(axis=1))
            | (ordered["Low"] > ordered[["Open", "Close", "High"]].min(axis=1))
        )
        counts["ohlc_inconsistency_count"] = int(invalid_ohlc.sum())
        stale = (
            (ordered["Open"] == ordered["High"])
            & (ordered["High"] == ordered["Low"])
            & (ordered["Low"] == ordered["Close"])
        )
        counts["stale_price_count"] = int(stale.sum())
        gap = ordered["Open"].pct_change().abs()
        counts["extreme_gap_count"] = int((gap > 0.50).sum())
    return counts


def merge_counts(counts: list[dict[str, int]]) -> dict[str, int]:
    """Sum homogenous integer counter dictionaries."""

    merged: dict[str, int] = {}
    for item in counts:
        for key, value in item.items():
            merged[key] = merged.get(key, 0) + int(value)
    return merged


def decide_trainability(
    labels: pd.DataFrame,
    split_summary: dict[str, dict[str, object]],
    quality_counts: dict[str, int],
    warnings: list[str] | None = None,
) -> AuditDecision:
    """Make a deterministic first-pass trainability decision."""

    hard_blockers: list[str] = []
    warning_list = list(warnings or [])
    reasons: list[str] = []

    if labels.empty:
        hard_blockers.append("TARGET_TOO_RARE_TO_EVALUATE")
        reasons.append("No label observations were produced.")

    if quality_counts.get("ohlc_inconsistency_count", 0) > 0:
        hard_blockers.append("UNRESOLVED_SPLIT_ARTIFACTS")
        reasons.append("OHLC inconsistencies were detected.")

    if quality_counts.get("missing_entry_open_count", 0) > 0:
        warning_list.append("MISSING_ENTRY_PRICE_ROWS_EXCLUDED")

    locked = split_summary.get("locked_test", {})
    locked_positives = int(locked.get("raw_positive_observations") or 0)
    if labels.empty or "locked_test" not in split_summary:
        hard_blockers.append("TEMPORAL_LOCKED_TEST_NOT_POSSIBLE")
    elif locked_positives == 0:
        hard_blockers.append("INSUFFICIENT_TEST_POSITIVES")
        reasons.append("Locked-test split has no positive observations.")
    elif locked_positives < 30:
        warning_list.append("LOW_LOCKED_TEST_POSITIVES")
        reasons.append("Locked-test split has fewer than 30 raw positive observations.")

    train = split_summary.get("train", {})
    if int(train.get("raw_positive_observations") or 0) < 30:
        warning_list.append("LOW_TRAIN_POSITIVES")

    if "SURVIVORSHIP_BIAS_PRESENT" not in warning_list:
        warning_list.append("SURVIVORSHIP_BIAS_PRESENT")

    if hard_blockers:
        status = "NOT_TRAINABLE_AS_DEFINED"
        next_step = "Fix hard blockers before model training."
    elif warning_list:
        status = "CONDITIONALLY_TRAINABLE"
        next_step = "Review warnings before proceeding to baseline modeling."
    else:
        status = "TRAINABLE"
        next_step = "Proceed to frozen baselines."

    if not reasons and not hard_blockers:
        reasons.append("No hard blockers were detected by the initial audit checks.")

    return AuditDecision(
        status=status,  # type: ignore[arg-type]
        hard_blockers=sorted(set(hard_blockers)),
        warnings=sorted(set(warning_list)),
        reasons=reasons,
        recommended_next_step=next_step,
    )

