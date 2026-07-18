from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.artifacts import price_data_to_frame
from stock_analyzer.datasets.swing_20.audit import apply_data_quality_quarantine, run_audit_from_frames
from stock_analyzer.datasets.swing_20.config import LabelConfig, Swing20Config, UniverseConfig
from stock_analyzer.datasets.swing_20.prepare import load_frozen_dataset, write_frozen_dataset
from stock_analyzer.datasets.swing_20.quality import DATA_QUALITY_EXCLUSION_REASON, evaluate_symbol_price_quality


def _clean_price_frame(days: int = 310, spike_day: int = 252) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    close = [10.0 + index * 0.02 for index in range(days)]
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": [price * 1.01 for price in close],
            "Low": [price * 0.99 for price in close],
            "Close": close,
            "Volume": [1_000_000] * days,
        },
        index=dates,
    )
    frame.iloc[spike_day : spike_day + 8, frame.columns.get_loc("High")] = frame.iloc[spike_day - 1]["Open"] * 1.25
    return frame


def _universe_frame(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": symbol, "security_name": None, "exchange": "NASDAQ", "instrument_type": "COMMON_STOCK"}
            for symbol in symbols
        ]
    )


def test_symbol_with_negative_prices_is_quarantined():
    good = _clean_price_frame()
    bad = _clean_price_frame().mul(-1)  # every OHLC value goes negative
    prices = price_data_to_frame({"GOOD": good, "BADNEG": bad})

    evaluation = evaluate_symbol_price_quality(prices)

    good_row = evaluation[evaluation["symbol"] == "GOOD"].iloc[0]
    bad_row = evaluation[evaluation["symbol"] == "BADNEG"].iloc[0]
    assert not good_row["is_quarantined"]
    assert bad_row["is_quarantined"]
    assert bad_row["non_positive_price_rows"] > 0


def test_symbol_with_material_ohlc_corruption_is_quarantined():
    good = _clean_price_frame()
    corrupt = _clean_price_frame()
    # A single row where Open sits far outside [Low, High]: a real bad print,
    # not float noise.
    corrupt.iloc[10, corrupt.columns.get_loc("Open")] = corrupt.iloc[10]["High"] * 3.0
    prices = price_data_to_frame({"GOOD": good, "CORRUPT": corrupt})

    evaluation = evaluate_symbol_price_quality(prices)

    corrupt_row = evaluation[evaluation["symbol"] == "CORRUPT"].iloc[0]
    assert corrupt_row["is_quarantined"]
    assert corrupt_row["material_ohlc_inconsistency_rows"] == 1
    assert corrupt_row["non_positive_price_rows"] == 0


def test_float_rounding_noise_does_not_quarantine():
    good = _clean_price_frame()
    noisy = _clean_price_frame()
    # Low is 1e-10 above Close: adjusted-price float noise, not a real error.
    close_col = noisy.columns.get_loc("Close")
    low_col = noisy.columns.get_loc("Low")
    noisy.iloc[5, low_col] = noisy.iloc[5, close_col] + 1e-10
    prices = price_data_to_frame({"GOOD": good, "NOISY": noisy})

    evaluation = evaluate_symbol_price_quality(prices)

    noisy_row = evaluation[evaluation["symbol"] == "NOISY"].iloc[0]
    assert not noisy_row["is_quarantined"]
    assert noisy_row["material_ohlc_inconsistency_rows"] == 0


def test_one_corrupt_symbol_does_not_block_the_valid_universe(tmp_path):
    good_a = _clean_price_frame(spike_day=252)
    good_b = _clean_price_frame(spike_day=260)
    bad = _clean_price_frame().mul(-1)

    price_data = {"GOODA": good_a, "GOODB": good_b, "BADNEG": bad}
    manifest = write_frozen_dataset(
        price_data=price_data,
        universe=_universe_frame(list(price_data)),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )
    frozen = load_frozen_dataset(manifest["snapshot_dir"])

    result = run_audit_from_frames(
        labels=frozen["labels"],
        eligibility=frozen["eligibility"],
        quality_counts=frozen["quality_counts"],
        prices=frozen["prices"],
    )

    assert "UNRESOLVED_SPLIT_ARTIFACTS" not in result.decision.hard_blockers
    assert result.data_quality_quarantine["data_quality_excluded_symbol_count"] == 1
    assert result.data_quality_quarantine["data_quality_excluded_symbols"][0]["symbol"] == "BADNEG"
    assert result.data_quality_quarantine["data_quality_excluded_symbols"][0]["exclusion_reason"] == (
        DATA_QUALITY_EXCLUSION_REASON
    )


def test_raw_frozen_snapshot_is_unchanged_by_quarantine(tmp_path):
    good = _clean_price_frame()
    bad = _clean_price_frame().mul(-1)
    price_data = {"GOOD": good, "BADNEG": bad}

    manifest = write_frozen_dataset(
        price_data=price_data,
        universe=_universe_frame(list(price_data)),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )
    frozen_before = load_frozen_dataset(manifest["snapshot_dir"])
    assert "BADNEG" in set(frozen_before["prices"]["symbol"])
    assert "BADNEG" in set(frozen_before["eligibility"]["symbol"])

    run_audit_from_frames(
        labels=frozen_before["labels"],
        eligibility=frozen_before["eligibility"],
        quality_counts=frozen_before["quality_counts"],
        prices=frozen_before["prices"],
    )

    # Re-load from disk: the audit run above must not have mutated the frozen
    # artifacts written by write_frozen_dataset.
    frozen_after = load_frozen_dataset(manifest["snapshot_dir"])
    assert "BADNEG" in set(frozen_after["prices"]["symbol"])
    assert "BADNEG" in set(frozen_after["eligibility"]["symbol"])
    pd.testing.assert_frame_equal(
        frozen_before["prices"].reset_index(drop=True), frozen_after["prices"].reset_index(drop=True)
    )


def test_audit_decision_uses_the_cleaned_eligible_universe(tmp_path):
    good = _clean_price_frame()
    bad = _clean_price_frame().mul(-1)
    price_data = {"GOOD": good, "BADNEG": bad}

    manifest = write_frozen_dataset(
        price_data=price_data,
        universe=_universe_frame(list(price_data)),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )
    frozen = load_frozen_dataset(manifest["snapshot_dir"])

    with_quarantine = run_audit_from_frames(
        labels=frozen["labels"],
        eligibility=frozen["eligibility"],
        quality_counts=frozen["quality_counts"],
        prices=frozen["prices"],
    )
    without_quarantine = run_audit_from_frames(
        labels=frozen["labels"],
        eligibility=frozen["eligibility"],
        quality_counts=frozen["quality_counts"],
        prices=None,
    )

    assert with_quarantine.universe["observations"] < without_quarantine.universe["observations"]
    assert "DATA_QUALITY_SYMBOLS_QUARANTINED" in with_quarantine.decision.warnings
    assert "DATA_QUALITY_SYMBOLS_QUARANTINED" not in without_quarantine.decision.warnings


def test_apply_data_quality_quarantine_reports_removed_counts():
    config = Swing20Config(label=LabelConfig(horizon_days=3, target_return=0.20), universe=UniverseConfig())
    good = _clean_price_frame()
    bad = _clean_price_frame().mul(-1)
    prices = price_data_to_frame({"GOOD": good, "BADNEG": bad})

    labels = pd.DataFrame(
        {
            "symbol": ["BADNEG", "BADNEG", "GOOD"],
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-02"]),
            "entry_date": pd.to_datetime(["2024-01-03", "2024-01-04", "2024-01-03"]),
            "window_end_date": pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-05"]),
            "target_20pct_20d": [True, False, True],
        }
    )
    eligibility = pd.DataFrame({"symbol": ["BADNEG", "GOOD"], "date": pd.to_datetime(["2024-01-02", "2024-01-02"])})

    clean_labels, clean_eligibility, _, summary = apply_data_quality_quarantine(
        labels, eligibility, prices, quality_counts={}, config=config
    )

    assert set(clean_labels["symbol"]) == {"GOOD"}
    assert set(clean_eligibility["symbol"]) == {"GOOD"}
    assert summary["observations_removed_by_data_quality"] == 2
    assert summary["positive_labels_removed_by_data_quality"] == 1
    assert summary["events_removed_by_data_quality"] == 1
