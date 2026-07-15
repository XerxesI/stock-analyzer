from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.config import LabelConfig
from stock_analyzer.datasets.swing_20.events import deduplicate_positive_events
from stock_analyzer.datasets.swing_20.labels import label_at, label_frame


def _frame(rows: list[dict[str, float | int | None]]) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=len(rows))
    return pd.DataFrame(rows, index=index)


def test_signal_day_high_never_counts_toward_swing20_target():
    df = _frame(
        [
            {"Open": 100, "High": 200, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 110, "Low": 99, "Close": 105, "Volume": 1000},
            {"Open": 105, "High": 110, "Low": 100, "Close": 108, "Volume": 1000},
        ]
    )

    result, counts = label_at(df, 0, LabelConfig(horizon_days=2, target_return=0.20))

    assert counts["missing_entry_open_count"] == 0
    assert result is not None
    assert result["target_20pct_20d"] is False


def test_missing_next_day_open_is_not_replaced_with_close():
    df = _frame(
        [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": None, "High": 150, "Low": 99, "Close": 120, "Volume": 1000},
            {"Open": 120, "High": 150, "Low": 110, "Close": 130, "Volume": 1000},
        ]
    )

    result, counts = label_at(df, 0, LabelConfig(horizon_days=2, target_return=0.20))

    assert result is None
    assert counts["missing_entry_open_count"] == 1


def test_fixed_stop_before_target_does_not_change_primary_target_label():
    df = _frame(
        [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 101, "Low": 90, "Close": 95, "Volume": 1000},
            {"Open": 95, "High": 125, "Low": 94, "Close": 120, "Volume": 1000},
            {"Open": 120, "High": 126, "Low": 119, "Close": 124, "Volume": 1000},
        ]
    )

    result, _ = label_at(
        df,
        0,
        LabelConfig(horizon_days=3, target_return=0.20, fixed_stop=-0.08),
    )

    assert result is not None
    assert result["fixed_stop_hit"] is True
    assert result["target_20pct_20d"] is True
    assert result["target_before_fixed_stop"] is False


def test_overlapping_positive_windows_form_one_economic_event():
    df = _frame(
        [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 125, "Low": 99, "Close": 122, "Volume": 1000},
            {"Open": 122, "High": 130, "Low": 120, "Close": 125, "Volume": 1000},
            {"Open": 125, "High": 130, "Low": 120, "Close": 126, "Volume": 1000},
        ]
    )
    labels = label_frame("TEST", df, LabelConfig(horizon_days=3, target_return=0.20)).labels

    events = deduplicate_positive_events(labels)

    assert int(labels["target_20pct_20d"].sum()) == 2
    assert len(events) == 1
    assert int(events.iloc[0]["raw_observation_count"]) == 2
