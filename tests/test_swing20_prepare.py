from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20.audit import run_audit_from_frames
from stock_analyzer.datasets.swing_20.prepare import (
    _deterministic_sample,
    _resolve_universe,
    load_frozen_dataset,
    verify_frozen_dataset,
    write_frozen_dataset,
)


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


def _universe_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "security_name": "AAA Corp",
                "exchange": "NASDAQ",
                "instrument_type": "COMMON_STOCK",
            }
        ]
    )


def test_write_and_load_frozen_dataset_round_trip(tmp_path):
    manifest = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=_universe_frame(),
        universe_source="test_universe",
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )

    loaded = load_frozen_dataset(manifest["snapshot_dir"])

    assert manifest["storage_format"] == "csv"
    assert manifest["universe_source"] == "test_universe"
    assert manifest["symbol_count_requested"] == 1
    assert manifest["symbol_count_with_prices"] == 1
    assert manifest["symbol_count_failed"] == 0
    assert not loaded["labels"].empty
    assert not loaded["eligibility"].empty
    assert loaded["failures"].empty
    assert loaded["manifest"]["strategy"] == "SWING_20"

    # Snapshot lives under <output_dir>/snapshots/<dataset_version>/, not
    # directly in output_dir.
    snapshot_dir = tmp_path / "snapshots" / manifest["dataset_version"]
    assert snapshot_dir.is_dir()
    assert (snapshot_dir / "manifest.json").is_file()
    assert (snapshot_dir / "failures.csv").is_file()


def test_loaded_frozen_dataset_can_drive_audit(tmp_path):
    manifest = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=_universe_frame(),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )
    loaded = load_frozen_dataset(manifest["snapshot_dir"])

    result = run_audit_from_frames(
        labels=loaded["labels"],
        eligibility=loaded["eligibility"],
        quality_counts=loaded["quality_counts"],
    )

    assert result.strategy == "SWING_20"
    assert result.labels["observations"] > 0
    assert result.baseline["mean_daily_positive_rate"] is not None


def test_repeated_writes_create_distinct_non_overwriting_snapshots(tmp_path):
    first = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=_universe_frame(),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )
    second = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=_universe_frame(),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )

    assert first["snapshot_dir"] != second["snapshot_dir"]
    assert first["dataset_version"] != second["dataset_version"]

    # Both snapshots must still be fully intact and independently loadable —
    # the second run must not have clobbered the first.
    first_loaded = load_frozen_dataset(first["snapshot_dir"])
    second_loaded = load_frozen_dataset(second["snapshot_dir"])
    assert not first_loaded["labels"].empty
    assert not second_loaded["labels"].empty


def test_manifest_includes_hashes_and_provenance(tmp_path):
    manifest = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=_universe_frame(),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )

    hashes = manifest["artifact_hashes"]
    for name in ("universe", "prices", "labels", "eligibility", "failures"):
        assert name in hashes
        assert isinstance(hashes[name], str) and len(hashes[name]) == 64  # sha256 hex digest

    provenance = manifest["provenance"]
    assert "git_commit" in provenance
    assert provenance["pandas_version"] == pd.__version__


def test_verify_frozen_dataset_passes_on_untouched_snapshot(tmp_path):
    manifest = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=_universe_frame(),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )

    verification = verify_frozen_dataset(manifest["snapshot_dir"])
    assert all(verification.values())


def test_verify_frozen_dataset_detects_tampering(tmp_path):
    manifest = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=_universe_frame(),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )

    from pathlib import Path

    labels_path = Path(manifest["snapshot_dir"]) / "labels.csv"
    labels_path.write_text("symbol,date\nAAA,2024-01-01\n", encoding="utf-8")

    verification = verify_frozen_dataset(manifest["snapshot_dir"])
    assert verification["labels"] is False
    assert verification["universe"] is True


def test_failures_are_recorded_not_dropped(tmp_path):
    manifest = write_frozen_dataset(
        price_data={"AAA": _price_frame()},
        universe=pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "security_name": "AAA Corp",
                    "exchange": "NASDAQ",
                    "instrument_type": "COMMON_STOCK",
                },
                {
                    "symbol": "ZZZ",
                    "security_name": "ZZZ Delisted",
                    "exchange": "NASDAQ",
                    "instrument_type": "COMMON_STOCK",
                },
            ]
        ),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
        failures={"ZZZ": "EMPTY_OR_MISSING_DATA"},
    )

    assert manifest["symbol_count_failed"] == 1
    assert "ZZZ" not in manifest["symbols_without_prices"]  # explained by failures, not silently missing

    loaded = load_frozen_dataset(manifest["snapshot_dir"])
    failures = loaded["failures"]
    assert list(failures["symbol"]) == ["ZZZ"]
    assert failures.iloc[0]["reason"] == "EMPTY_OR_MISSING_DATA"


def test_resolve_universe_max_symbols_uses_deterministic_sample_not_head():
    # 26 symbols, sampling 5: odds of a random sample coincidentally matching
    # a head()-style first-N cut are ~1 in 65,780 -- safe for a regression
    # test that this is a seeded sample, not a positional slice.
    letters = [chr(ord("A") + i) * 3 for i in range(26)]
    universe = pd.DataFrame(
        [
            {"symbol": symbol, "security_name": None, "exchange": None, "instrument_type": "COMMON_STOCK"}
            for symbol in letters
        ]
    )

    sampled_once = _deterministic_sample(universe, n=5, seed=7)
    sampled_twice = _deterministic_sample(universe, n=5, seed=7)

    # Same seed -> same subset (reproducible).
    assert sorted(sampled_once["symbol"]) == sorted(sampled_twice["symbol"])
    assert len(sampled_once) == 5
    # A head()-based cut would always return the first five rows in universe
    # order; the seeded sample must not be pinned to that.
    assert sorted(sampled_once["symbol"]) != sorted(letters[:5])


def test_resolve_universe_with_explicit_symbols_and_max_symbols_is_deterministic():
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]

    first = _resolve_universe(symbols=symbols, universe_source="full_us", max_symbols=2, seed=42)
    second = _resolve_universe(symbols=symbols, universe_source="full_us", max_symbols=2, seed=42)

    assert sorted(first["symbol"]) == sorted(second["symbol"])
    assert len(first) == 2
