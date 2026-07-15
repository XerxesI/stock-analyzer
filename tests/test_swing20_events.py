from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.config import LabelConfig
from stock_analyzer.datasets.swing_20.events import deduplicate_positive_events
from stock_analyzer.datasets.swing_20.labels import label_frame

# 2024-01-03 (Wed) is deliberately skipped to simulate a market holiday: a
# real trading calendar has gaps that plain BDay arithmetic does not know
# about, so the row immediately after 2024-01-02 is 2024-01-04, not 2024-01-03.
HOLIDAY_GAP_INDEX = pd.to_datetime(
    ["2024-01-01", "2024-01-02", "2024-01-04", "2024-01-05", "2024-01-08"]
)


def _frame(rows: list[dict[str, float]], index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(rows, index=index)


def test_window_end_date_is_the_actual_next_trading_bar_not_a_bday_offset():
    # horizon_days=1: the label's only future bar is the very next row.
    df = _frame(
        [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 125, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
        ],
        HOLIDAY_GAP_INDEX,
    )
    labels = label_frame("TEST", df, LabelConfig(horizon_days=1, target_return=0.20)).labels

    signal_at_jan2 = labels[labels["date"] == pd.Timestamp("2024-01-02")].iloc[0]

    # The actual next trading bar is 2024-01-04 (holiday skipped the 3rd).
    assert signal_at_jan2["window_end_date"] == pd.Timestamp("2024-01-04")
    # A naive `date + BDay(1)` would have landed on the holiday itself, which
    # never traded and is not in the data at all.
    naive_bday_estimate = pd.Timestamp("2024-01-02") + pd.offsets.BDay(1)
    assert naive_bday_estimate == pd.Timestamp("2024-01-03")
    assert signal_at_jan2["window_end_date"] != naive_bday_estimate


def test_overlapping_actual_windows_merge_across_a_holiday_gap():
    # Three consecutive positive signals (Jan1, Jan2, Jan4). Using each label's
    # real window_end_date, all three chain together into one event because
    # Jan2's actual window reaches Jan4 (the holiday pushed it out by a day).
    # A naive `date + BDay(horizon)` computation would have Jan2's window end
    # on Jan3 (the holiday, not in the data) and incorrectly split this into
    # two events instead of one.
    df = _frame(
        [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},  # Jan1: signal A
            {"Open": 100, "High": 125, "Low": 99, "Close": 100, "Volume": 1000},  # Jan2: signal B / A's future
            {"Open": 100, "High": 125, "Low": 99, "Close": 100, "Volume": 1000},  # Jan4: signal C / B's future
            {"Open": 100, "High": 125, "Low": 99, "Close": 100, "Volume": 1000},  # Jan5: C's future
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},  # Jan8: padding
        ],
        HOLIDAY_GAP_INDEX,
    )
    labels = label_frame("TEST", df, LabelConfig(horizon_days=1, target_return=0.20)).labels

    assert int(labels["target_20pct_20d"].sum()) == 3

    events = deduplicate_positive_events(labels)

    assert len(events) == 1
    assert int(events.iloc[0]["raw_observation_count"]) == 3


def test_nonoverlapping_actual_windows_remain_separate_events():
    df = _frame(
        [
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},  # Jan1: signal A
            {"Open": 100, "High": 125, "Low": 99, "Close": 100, "Volume": 1000},  # Jan2: A's future
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},  # Jan4: unrelated gap bar
            {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},  # Jan5: signal B
            {"Open": 100, "High": 125, "Low": 99, "Close": 100, "Volume": 1000},  # Jan8: B's future
        ],
        HOLIDAY_GAP_INDEX,
    )
    labels = label_frame("TEST", df, LabelConfig(horizon_days=1, target_return=0.20)).labels

    assert int(labels["target_20pct_20d"].sum()) == 2

    events = deduplicate_positive_events(labels)

    assert len(events) == 2
    assert list(events["raw_observation_count"]) == [1, 1]
