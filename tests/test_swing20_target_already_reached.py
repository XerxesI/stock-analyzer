from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.audit import exclude_target_already_reached_at_entry, run_audit_from_frames
from stock_analyzer.datasets.swing_20.config import SplitConfig
from stock_analyzer.datasets.swing_20.splits import assign_temporal_splits


def _row(symbol: str, date: str, target_reached: bool, positive: bool) -> dict[str, object]:
    return {
        "symbol": symbol,
        "date": date,
        "entry_date": date,
        "window_end_date": date,
        "target_already_reached_at_entry": target_reached,
        "target_20pct_20d": positive,
    }


def _labels_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in ("date", "entry_date", "window_end_date"):
        frame[column] = pd.to_datetime(frame[column])
    return frame


def test_target_already_reached_row_is_excluded_from_primary_population():
    labels = _labels_frame(
        [
            _row("AAA", "2024-01-02", True, True),
            _row("BBB", "2024-01-03", False, True),
        ]
    )

    primary, diagnostics = exclude_target_already_reached_at_entry(labels)

    assert list(primary["symbol"]) == ["BBB"]
    assert diagnostics["excluded_row_count"] == 1


def test_excluded_row_remains_visible_in_diagnostics_not_deleted():
    labels = _labels_frame(
        [
            _row("AAA", "2024-01-02", True, True),
            _row("BBB", "2024-01-03", False, True),
        ]
    )

    primary, diagnostics = exclude_target_already_reached_at_entry(labels)

    assert diagnostics["excluded_by_symbol"] == {"AAA": 1}
    # Nothing was deleted from the frame the caller already holds.
    assert len(labels) == 2
    assert "AAA" in set(labels["symbol"])
    assert len(primary) == 1


def test_ordinary_post_entry_target_hits_remain_positive():
    labels = _labels_frame(
        [
            _row("AAA", "2024-01-02", True, True),
            _row("BBB", "2024-01-03", False, True),
        ]
    )

    primary, _ = exclude_target_already_reached_at_entry(labels)

    bbb = primary[primary["symbol"] == "BBB"].iloc[0]
    assert bool(bbb["target_20pct_20d"]) is True


def test_ordinary_negatives_remain_negative():
    labels = _labels_frame(
        [
            _row("AAA", "2024-01-02", True, True),
            _row("CCC", "2024-01-04", False, False),
        ]
    )

    primary, _ = exclude_target_already_reached_at_entry(labels)

    ccc = primary[primary["symbol"] == "CCC"].iloc[0]
    assert bool(ccc["target_20pct_20d"]) is False


def test_split_counts_reconcile_after_exclusion():
    labels = _labels_frame(
        [
            _row("AAA", "2022-01-03", True, True),
            _row("BBB", "2022-01-04", False, True),
            _row("CCC", "2025-06-01", True, True),
            _row("DDD", "2025-06-02", False, False),
        ]
    )
    labels_with_splits = assign_temporal_splits(labels, SplitConfig())

    primary, diagnostics = exclude_target_already_reached_at_entry(labels_with_splits)

    assert sum(diagnostics["excluded_by_split"].values()) == diagnostics["excluded_row_count"]
    assert diagnostics["observations_before"] == len(labels_with_splits)
    assert diagnostics["observations_after"] == len(primary)
    assert (
        diagnostics["observations_before"] - diagnostics["observations_after"] == diagnostics["excluded_row_count"]
    )


def test_no_excluded_rows_leaves_diagnostics_at_zero_and_labels_untouched():
    labels = _labels_frame(
        [
            _row("BBB", "2024-01-03", False, True),
            _row("CCC", "2024-01-04", False, False),
        ]
    )

    primary, diagnostics = exclude_target_already_reached_at_entry(labels)

    assert diagnostics["excluded_row_count"] == 0
    assert diagnostics["excluded_by_symbol"] == {}
    assert len(primary) == len(labels)


def test_run_audit_from_frames_reports_gap_diagnostics_and_warning():
    labels = _labels_frame(
        [
            _row("AAA", "2024-01-02", True, True),
            _row("BBB", "2024-01-03", False, True),
            _row("CCC", "2024-01-04", False, False),
        ]
    )

    result = run_audit_from_frames(labels=labels)

    assert result.target_already_reached_at_entry["excluded_row_count"] == 1
    assert "TARGET_ALREADY_REACHED_AT_ENTRY_EXCLUDED" in result.decision.warnings
    # The primary label population (and therefore observations) excludes AAA.
    assert result.labels["observations"] == 2
