"""Tests for EXP-005's frozen-artifact lineage verification and frozen market-data
provider (Stage 10 closure P1 review): every OHLCV bar an EXP-005 real run uses
must come from a hash-verified, non-live snapshot -- never a live Yahoo fetch.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import (
    FrozenArtifactVerificationError,
    sha256_of_dataframe,
    sha256_of_file,
    verify_frozen_lineage,
)
from stock_analyzer.sandbox.exp005.infrastructure.frozen_market_data_provider import (
    FrozenDataIntegrityError,
    FrozenDataRangeError,
    FrozenSwing20MarketDataProvider,
)

_SWING20_ARTIFACTS = ("universe", "prices", "labels", "eligibility", "failures")


def _write_parquet(path, df: pd.DataFrame) -> None:
    df.to_parquet(path)


def _build_fixture_snapshots(tmp_path, prices_df: pd.DataFrame | None = None):
    """Builds a small, real (hash-verifiable) SWING_20 snapshot + feature snapshot
    pair under tmp_path, matching the actual project's manifest.json structure --
    just with tiny synthetic data instead of the real multi-GB artifact."""

    swing20_dir = tmp_path / "swing_20" / "snapshots" / "swing20_test"
    swing20_dir.mkdir(parents=True)

    if prices_df is None:
        prices_df = pd.DataFrame(
            {
                "symbol": ["AAA", "AAA", "AAA", "BBB", "BBB"],
                "date": pd.to_datetime(
                    ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-05", "2026-01-06"]
                ),
                "Open": [10.0, 10.1, 10.2, 20.0, 20.1],
                "High": [10.5, 10.6, 10.7, 20.5, 20.6],
                "Low": [9.5, 9.6, 9.7, 19.5, 19.6],
                "Close": [10.2, 10.3, 10.4, 20.2, 20.3],
                "Volume": [1000, 1100, 1200, 2000, 2100],
            }
        )
    other_df = pd.DataFrame({"symbol": ["AAA", "BBB"], "value": [1, 2]})

    artifact_dfs = {"universe": other_df, "prices": prices_df, "labels": other_df, "eligibility": other_df, "failures": other_df}
    artifact_hashes = {}
    artifacts_paths = {}
    for name, df in artifact_dfs.items():
        path = swing20_dir / f"{name}.parquet"
        _write_parquet(path, df)
        artifact_hashes[name] = sha256_of_file(path)
        artifacts_paths[name] = str(path)

    swing20_manifest = {
        "dataset_version": "swing20_test",
        "artifacts": artifacts_paths,
        "artifact_hashes": artifact_hashes,
    }
    with open(swing20_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(swing20_manifest, f)

    feature_dir = tmp_path / "swing_20_features" / "snapshots" / "swing20_features_test"
    feature_dir.mkdir(parents=True)
    features_df = pd.DataFrame({"symbol": ["AAA", "BBB"], "date": pd.to_datetime(["2026-01-05", "2026-01-05"]), "f1": [1.0, 2.0]})
    _write_parquet(feature_dir / "features.parquet", features_df)
    feature_manifest = {
        "dataset_version": "swing20_features_test",
        "source_swing20_snapshot_id": "swing20_test",
        "source_swing20_snapshot_dir": str(swing20_dir),
        "source_swing20_artifact_hashes": artifact_hashes,
        "feature_dataset_hash": sha256_of_dataframe(features_df),
    }
    with open(feature_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(feature_manifest, f)

    return feature_dir, swing20_dir


# ------------------------------------------------------------- lineage verification


def test_verify_frozen_lineage_succeeds_on_consistent_snapshots(tmp_path):
    feature_dir, swing20_dir = _build_fixture_snapshots(tmp_path)

    lineage = verify_frozen_lineage(feature_dir)

    assert lineage.feature_snapshot_id == "swing20_features_test"
    assert lineage.swing20_snapshot_id == "swing20_test"
    assert set(lineage.artifact_hashes) == set(_SWING20_ARTIFACTS)
    assert not lineage.prices_df.empty


def test_verify_frozen_lineage_rejects_snapshot_id_mismatch(tmp_path):
    feature_dir, swing20_dir = _build_fixture_snapshots(tmp_path)
    manifest_path = feature_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_swing20_snapshot_id"] = "some-other-snapshot"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(FrozenArtifactVerificationError, match="source_swing20_snapshot_id"):
        verify_frozen_lineage(feature_dir)


def test_verify_frozen_lineage_rejects_artifact_hash_mismatch_between_manifests(tmp_path):
    feature_dir, swing20_dir = _build_fixture_snapshots(tmp_path)
    manifest_path = feature_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_swing20_artifact_hashes"]["prices"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(FrozenArtifactVerificationError, match="artifact_hashes"):
        verify_frozen_lineage(feature_dir)


def test_verify_frozen_lineage_rejects_tampered_swing20_file(tmp_path):
    feature_dir, swing20_dir = _build_fixture_snapshots(tmp_path)
    # Tamper with the actual prices.parquet file AFTER its hash was recorded.
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]})
    _write_parquet(swing20_dir / "prices.parquet", tampered)

    with pytest.raises(FrozenArtifactVerificationError, match="does not match its manifest-recorded hash"):
        verify_frozen_lineage(feature_dir)


def test_verify_frozen_lineage_rejects_tampered_feature_dataset(tmp_path):
    feature_dir, swing20_dir = _build_fixture_snapshots(tmp_path)
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "f1": [999.0]})
    _write_parquet(feature_dir / "features.parquet", tampered)

    with pytest.raises(FrozenArtifactVerificationError, match="feature_dataset_hash"):
        verify_frozen_lineage(feature_dir)


# ---------------------------------------------------------- frozen market data provider


def test_provider_fetch_as_of_returns_bars_up_to_and_including_as_of_date(tmp_path):
    feature_dir, _ = _build_fixture_snapshots(tmp_path)
    provider = FrozenSwing20MarketDataProvider(feature_dir)

    result = provider.fetch_as_of("AAA", date(2026, 1, 6))

    assert list(result.index.date) == [date(2026, 1, 5), date(2026, 1, 6)]
    assert list(result["Close"]) == [10.2, 10.3]


def test_provider_never_returns_a_future_bar(tmp_path):
    feature_dir, _ = _build_fixture_snapshots(tmp_path)
    provider = FrozenSwing20MarketDataProvider(feature_dir)

    result = provider.fetch_as_of("AAA", date(2026, 1, 5))

    assert list(result.index.date) == [date(2026, 1, 5)]


def test_provider_unknown_symbol_returns_empty_frame(tmp_path):
    feature_dir, _ = _build_fixture_snapshots(tmp_path)
    provider = FrozenSwing20MarketDataProvider(feature_dir)

    result = provider.fetch_as_of("NOPE", date(2026, 1, 6))

    assert result.empty


def test_provider_rejects_date_outside_frozen_range(tmp_path):
    feature_dir, _ = _build_fixture_snapshots(tmp_path)
    provider = FrozenSwing20MarketDataProvider(feature_dir)

    with pytest.raises(FrozenDataRangeError):
        provider.fetch_as_of("AAA", date(2020, 1, 1))
    with pytest.raises(FrozenDataRangeError):
        provider.fetch_as_of("AAA", date(2030, 1, 1))


def test_provider_construction_rejects_duplicate_symbol_date_rows(tmp_path):
    duplicated = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "date": pd.to_datetime(["2026-01-05", "2026-01-05"]),
            "Open": [10.0, 10.0], "High": [10.5, 10.5], "Low": [9.5, 9.5], "Close": [10.2, 10.2], "Volume": [1000, 1000],
        }
    )
    feature_dir, _ = _build_fixture_snapshots(tmp_path, prices_df=duplicated)

    with pytest.raises(FrozenDataIntegrityError, match="duplicate"):
        FrozenSwing20MarketDataProvider(feature_dir)


def test_provider_construction_rejects_null_symbol(tmp_path):
    malformed = pd.DataFrame(
        {
            "symbol": [None],
            "date": pd.to_datetime(["2026-01-05"]),
            "Open": [10.0], "High": [10.5], "Low": [9.5], "Close": [10.2], "Volume": [1000],
        }
    )
    feature_dir, _ = _build_fixture_snapshots(tmp_path, prices_df=malformed)

    with pytest.raises(FrozenDataIntegrityError, match="symbol"):
        FrozenSwing20MarketDataProvider(feature_dir)


def test_provider_construction_verifies_lineage_and_rejects_tampered_data(tmp_path):
    feature_dir, swing20_dir = _build_fixture_snapshots(tmp_path)
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]})
    _write_parquet(swing20_dir / "prices.parquet", tampered)

    with pytest.raises(FrozenArtifactVerificationError):
        FrozenSwing20MarketDataProvider(feature_dir)


def test_provider_exposes_verified_snapshot_identity(tmp_path):
    feature_dir, _ = _build_fixture_snapshots(tmp_path)
    provider = FrozenSwing20MarketDataProvider(feature_dir)

    assert provider.feature_snapshot_id == "swing20_features_test"
    assert provider.swing20_snapshot_id == "swing20_test"


def test_provider_never_calls_live_data_fetcher(tmp_path, monkeypatch):
    """No live/network fallback of any kind -- patch the underlying live fetcher
    to explode, and confirm the frozen provider never touches it."""

    def exploding_get_stock_data(*args, **kwargs):
        raise AssertionError("FrozenSwing20MarketDataProvider must never call the live data fetcher")

    import stock_analyzer.data.data_fetcher as data_fetcher_module

    monkeypatch.setattr(data_fetcher_module, "get_stock_data", exploding_get_stock_data)

    feature_dir, _ = _build_fixture_snapshots(tmp_path)
    provider = FrozenSwing20MarketDataProvider(feature_dir)
    result = provider.fetch_as_of("AAA", date(2026, 1, 6))
    assert not result.empty
