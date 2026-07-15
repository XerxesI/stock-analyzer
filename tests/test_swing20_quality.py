from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.quality import decide_trainability, ohlcv_quality_counts


def _frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=len(rows))
    return pd.DataFrame(rows, index=index)


def test_exact_valid_ohlc_has_no_inconsistencies():
    df = _frame(
        [
            {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},
            {"Open": 100.5, "High": 102.0, "Low": 100.0, "Close": 101.5},
        ]
    )

    counts = ohlcv_quality_counts(df)

    assert counts["ohlc_inconsistency_count"] == 0
    assert counts["ohlc_material_inconsistency_count"] == 0
    assert counts["ohlc_rounding_tolerance_count"] == 0
    assert counts["maximum_ohlc_deviation"] == 0.0


def test_float_epsilon_deviation_is_rounding_tolerance_not_material():
    # Low is 1e-10 above Close, with Open/High otherwise consistent: adjusted-price
    # float noise, not a real print error.
    df = _frame(
        [
            {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},
            {"Open": 12.814406, "High": 12.890683, "Low": 12.769912 + 1e-10, "Close": 12.769912},
        ]
    )

    counts = ohlcv_quality_counts(df)

    assert counts["ohlc_inconsistency_count"] == 1
    assert counts["ohlc_material_inconsistency_count"] == 0
    assert counts["ohlc_rounding_tolerance_count"] == 1
    assert counts["maximum_ohlc_deviation"] < 1e-6


def test_material_deviation_is_flagged_as_material_inconsistency():
    # Open (72.62) sits well below Low (73.07): a real, non-negligible bad print.
    df = _frame(
        [
            {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},
            {"Open": 72.62, "High": 74.23, "Low": 73.07, "Close": 73.85},
        ]
    )

    counts = ohlcv_quality_counts(df)

    assert counts["ohlc_inconsistency_count"] == 1
    assert counts["ohlc_material_inconsistency_count"] == 1
    assert counts["ohlc_rounding_tolerance_count"] == 0
    assert counts["maximum_ohlc_deviation"] > 0.4
    assert counts["maximum_ohlc_deviation_bps"] > 50


def test_trainability_decision_ignores_rounding_tolerance_artifacts():
    quality_counts = {
        "ohlc_inconsistency_count": 3,
        "ohlc_material_inconsistency_count": 0,
        "ohlc_rounding_tolerance_count": 3,
    }
    labels = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=5)})
    split_summary = {"locked_test": {"raw_positive_observations": 30}, "train": {"raw_positive_observations": 30}}

    decision = decide_trainability(labels, split_summary, quality_counts)

    assert "UNRESOLVED_SPLIT_ARTIFACTS" not in decision.hard_blockers
    assert "OHLC_ROUNDING_TOLERANCE_ARTIFACTS_PRESENT" in decision.warnings


def test_trainability_decision_hard_blocks_on_material_inconsistency():
    quality_counts = {
        "ohlc_inconsistency_count": 1,
        "ohlc_material_inconsistency_count": 1,
        "ohlc_rounding_tolerance_count": 0,
    }
    labels = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=5)})
    split_summary = {"locked_test": {"raw_positive_observations": 30}, "train": {"raw_positive_observations": 30}}

    decision = decide_trainability(labels, split_summary, quality_counts)

    assert "UNRESOLVED_SPLIT_ARTIFACTS" in decision.hard_blockers
    assert decision.status == "NOT_TRAINABLE_AS_DEFINED"
