"""Data-quality and trainability decision helpers for SWING_20."""

from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.config import QualityConfig
from stock_analyzer.datasets.swing_20.schema import AuditDecision

# maximum_* fields are peak values, not additive counters: merge_counts() must
# take their max across frames rather than summing them.
_MAX_AGGREGATE_KEYS = {"maximum_ohlc_deviation", "maximum_ohlc_deviation_bps"}

# Stable reason code for the symbol-level data-quality quarantine (see
# evaluate_symbol_price_quality): a symbol's entire price history is dropped
# from the model-eligible universe, not just the offending rows, because a
# corrupt adjustment series (e.g. a botched reverse-split back-calculation)
# taints every return computed from it, not only the rows that fail the
# raw OHLC check.
DATA_QUALITY_EXCLUSION_REASON = "INVALID_PRICE_SERIES"


def _ohlc_deviation(df: pd.DataFrame) -> pd.Series:
    """Per-row OHLC deviation: how far Open/Close falls outside [Low, High]."""

    high_deficit = (df[["Open", "Close", "Low"]].max(axis=1) - df["High"]).clip(lower=0)
    low_deficit = (df["Low"] - df[["Open", "Close", "High"]].min(axis=1)).clip(lower=0)
    return pd.concat([high_deficit, low_deficit], axis=1).max(axis=1)


def _material_mask(
    df: pd.DataFrame,
    deviation: pd.Series,
    ohlc_absolute_tolerance: float,
    ohlc_relative_tolerance: float,
) -> pd.Series:
    reference_price = df["Close"].abs()
    tolerance = (reference_price * ohlc_relative_tolerance).clip(lower=ohlc_absolute_tolerance)
    return (deviation > 0) & (deviation > tolerance)


def ohlcv_quality_counts(
    df: pd.DataFrame,
    ohlc_absolute_tolerance: float = QualityConfig().ohlc_absolute_tolerance,
    ohlc_relative_tolerance: float = QualityConfig().ohlc_relative_tolerance,
) -> dict[str, int]:
    """Return basic OHLCV quality counts for one price frame.

    ``ohlc_inconsistency_count`` is every raw ``close/open`` value that falls
    outside ``[Low, High]``, including sub-cent float-rounding noise from
    adjusted prices. That raw count is split into
    ``ohlc_material_inconsistency_count`` (deviation exceeds the configured
    tolerance) and ``ohlc_rounding_tolerance_count`` (deviation is within
    tolerance); only the material count should gate trainability.
    """

    counts: dict[str, int | float] = {
        "ohlc_inconsistency_count": 0,
        "ohlc_material_inconsistency_count": 0,
        "ohlc_rounding_tolerance_count": 0,
        "maximum_ohlc_deviation": 0.0,
        "maximum_ohlc_deviation_bps": 0.0,
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
        deviation = _ohlc_deviation(ordered)
        invalid = deviation > 0
        counts["ohlc_inconsistency_count"] = int(invalid.sum())

        if invalid.any():
            material = _material_mask(ordered, deviation, ohlc_absolute_tolerance, ohlc_relative_tolerance)
            counts["ohlc_material_inconsistency_count"] = int(material.sum())
            counts["ohlc_rounding_tolerance_count"] = int(invalid.sum() - material.sum())
            counts["maximum_ohlc_deviation"] = float(deviation[invalid].max())
            reference_price = ordered["Close"].abs()
            deviation_bps = (deviation / reference_price.where(reference_price > 0)) * 10000
            counts["maximum_ohlc_deviation_bps"] = float(deviation_bps[invalid].max())

        stale = (
            (ordered["Open"] == ordered["High"])
            & (ordered["High"] == ordered["Low"])
            & (ordered["Low"] == ordered["Close"])
        )
        counts["stale_price_count"] = int(stale.sum())
        gap = ordered["Open"].pct_change().abs()
        counts["extreme_gap_count"] = int((gap > 0.50).sum())
    return counts


def evaluate_symbol_price_quality(
    prices: pd.DataFrame,
    ohlc_absolute_tolerance: float = QualityConfig().ohlc_absolute_tolerance,
    ohlc_relative_tolerance: float = QualityConfig().ohlc_relative_tolerance,
) -> pd.DataFrame:
    """Evaluate every symbol in a concatenated ``prices`` frame for quarantine.

    ``prices`` must have ``symbol``, ``date``, and OHLC columns (the frozen
    snapshot's ``prices`` artifact shape). Returns one row per symbol with a
    ``quality_counts`` dict (same shape as :func:`ohlcv_quality_counts`) and a
    quarantine verdict: a symbol is quarantined if any row has a non-positive
    Open/High/Low/Close, High below Low, or a material OHLC deviation --
    conditions under which a return computed from that row is not
    economically interpretable.
    """

    columns = [
        "symbol",
        "is_quarantined",
        "non_positive_price_rows",
        "high_below_low_rows",
        "material_ohlc_inconsistency_rows",
        "affected_row_count",
        "first_affected_date",
        "last_affected_date",
        "quality_counts",
    ]
    if prices.empty:
        return pd.DataFrame(columns=columns)

    prices = prices.assign(date=pd.to_datetime(prices["date"]))

    records: list[dict[str, object]] = []
    for symbol, group in prices.sort_values("date").groupby("symbol", sort=True):
        ordered = group.set_index("date")
        quality_counts = ohlcv_quality_counts(ordered, ohlc_absolute_tolerance, ohlc_relative_tolerance)

        non_positive_mask = (ordered[["Open", "High", "Low", "Close"]] <= 0).any(axis=1)
        high_below_low_mask = ordered["High"] < ordered["Low"]
        deviation = _ohlc_deviation(ordered)
        material_mask = _material_mask(ordered, deviation, ohlc_absolute_tolerance, ohlc_relative_tolerance)

        invalid_mask = non_positive_mask | high_below_low_mask | material_mask
        affected_dates = ordered.index[invalid_mask]

        records.append(
            {
                "symbol": symbol,
                "is_quarantined": bool(invalid_mask.any()),
                "non_positive_price_rows": int(non_positive_mask.sum()),
                "high_below_low_rows": int(high_below_low_mask.sum()),
                "material_ohlc_inconsistency_rows": int(material_mask.sum()),
                "affected_row_count": int(invalid_mask.sum()),
                "first_affected_date": affected_dates.min() if len(affected_dates) else None,
                "last_affected_date": affected_dates.max() if len(affected_dates) else None,
                "quality_counts": quality_counts,
            }
        )
    return pd.DataFrame(records, columns=columns)


def merge_counts(counts: list[dict[str, int]]) -> dict[str, int]:
    """Sum homogenous integer counter dictionaries.

    ``_MAX_AGGREGATE_KEYS`` fields are peak values and are combined with
    ``max`` instead of being summed across frames.
    """

    merged: dict[str, int] = {}
    for item in counts:
        for key, value in item.items():
            if key in _MAX_AGGREGATE_KEYS:
                merged[key] = max(float(merged.get(key, 0.0)), float(value))
            else:
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

    if quality_counts.get("ohlc_material_inconsistency_count", 0) > 0:
        hard_blockers.append("UNRESOLVED_SPLIT_ARTIFACTS")
        reasons.append("Material OHLC inconsistencies were detected.")
    elif quality_counts.get("ohlc_rounding_tolerance_count", 0) > 0:
        warning_list.append("OHLC_ROUNDING_TOLERANCE_ARTIFACTS_PRESENT")

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

