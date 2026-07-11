from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.audit import run_audit_from_frames
from stock_analyzer.datasets.swing_20.prepare import load_frozen_dataset, write_frozen_dataset


def _price_frame(start: str = "2024-01-01", days: int = 310) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=days)
    close = [10.0 + index * 0.02 for index in range(days)]
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": [price * 1.01 for price in close],
            "Low": [price * 0.99 for price in close],
            "Close": close,
            "Volume": [1_000_000 for _ in range(days)],
        },
        index=dates,
    )
    # Create one clear SWING_20 target after the first eligible entry.
    frame.iloc[252:260, frame.columns.get_loc("High")] = frame.iloc[251]["Open"] * 1.25
    return frame


def test_write_and_load_frozen_dataset_round_trip(tmp_path):
    universe = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "security_name": "AAA Corp",
                "exchange": "NASDAQ",
                "instrument_type": "COMMON_STOCK",
            }
        ]
    )
    manifest = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=universe,
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )

    loaded = load_frozen_dataset(tmp_path)

    assert manifest["storage_format"] == "csv"
    assert manifest["symbol_count_requested"] == 1
    assert manifest["symbol_count_with_prices"] == 1
    assert not loaded["labels"].empty
    assert not loaded["eligibility"].empty
    assert loaded["manifest"]["strategy"] == "SWING_20"


def test_loaded_frozen_dataset_can_drive_audit(tmp_path):
    universe = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "security_name": "AAA Corp",
                "exchange": "NASDAQ",
                "instrument_type": "COMMON_STOCK",
            }
        ]
    )
    write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=universe,
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )
    loaded = load_frozen_dataset(tmp_path)

    result = run_audit_from_frames(
        labels=loaded["labels"],
        eligibility=loaded["eligibility"],
        quality_counts=loaded["quality_counts"],
    )

    assert result.strategy == "SWING_20"
    assert result.labels["observations"] > 0
    assert result.baseline["mean_daily_positive_rate"] is not None
