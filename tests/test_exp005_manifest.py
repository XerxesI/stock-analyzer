"""Tests for EXP-005's Experiment Manifest generator (Revision 5, Section 29,
Stage 9, rewritten during Stage 10 closure to use real, physically-verified
frozen-artifact lineage instead of an arbitrary raw-file hash).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.exp005.config import DEFAULT_CONTROL_SEEDS, Exp005Config
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_dataframe, sha256_of_file
from stock_analyzer.sandbox.exp005.manifest import (
    ExperimentManifest,
    build_experiment_manifest,
    compute_calendar_version,
    read_manifest_artifact,
    write_manifest_artifact,
)


def _write_parquet(path, df: pd.DataFrame) -> None:
    df.to_parquet(path)


def _build_fixture_snapshots(tmp_path, prices_df: pd.DataFrame | None = None):
    """Small, physically hash-verifiable SWING_20 snapshot + feature snapshot
    pair -- see tests/test_exp005_frozen_artifacts.py for the same pattern."""

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

    return feature_dir, swing20_dir, artifact_hashes


# --------------------------------------------------------------- calendar_version


def test_compute_calendar_version_is_deterministic_and_period_scoped():
    prices_df = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]), "symbol": ["AAA"] * 4}
    )
    v1 = compute_calendar_version(prices_df, date(2026, 1, 5), date(2026, 1, 7))
    v2 = compute_calendar_version(prices_df, date(2026, 1, 5), date(2026, 1, 7))
    v3 = compute_calendar_version(prices_df, date(2026, 1, 5), date(2026, 1, 8))  # wider period

    assert v1 == v2
    assert v1 != v3


def test_compute_calendar_version_raises_when_no_dates_in_period():
    prices_df = pd.DataFrame({"date": pd.to_datetime(["2026-01-05"]), "symbol": ["AAA"]})
    with pytest.raises(ValueError):
        compute_calendar_version(prices_df, date(2030, 1, 1), date(2030, 1, 2))


# ------------------------------------------------------------------- manifest build


def test_manifest_assembles_every_field_from_verified_lineage(tmp_path):
    feature_dir, swing20_dir, artifact_hashes = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()
    generated_at = datetime(2026, 7, 19, tzinfo=timezone.utc)

    manifest = build_experiment_manifest(
        config, feature_dir, period_start=date(2026, 1, 5), period_end=date(2026, 1, 7),
        code_commit_sha="abc123", generated_at=generated_at,
    )

    assert manifest.experiment_id == "EXP-005"
    assert manifest.code_commit_sha == "abc123"
    assert manifest.schema_version == 3
    assert manifest.decision_audit_schema_version == config.decision_audit_schema_version
    assert manifest.universe_hash == artifact_hashes["universe"]
    assert manifest.ohlc_hash == artifact_hashes["prices"]
    assert manifest.signal_hash == artifact_hashes["labels"]
    assert manifest.eligibility_hash == artifact_hashes["eligibility"]
    assert manifest.feature_hash  # semantic hash, verified against the feature manifest
    assert manifest.feature_snapshot_id == "swing20_features_test"
    assert manifest.swing20_snapshot_id == "swing20_test"
    assert manifest.calendar_version
    assert manifest.portfolio_configuration_hash == config.portfolio_configuration_hash()
    assert manifest.control_seed_list == DEFAULT_CONTROL_SEEDS
    assert manifest.feasibility_criteria == config.feasibility_criteria.canonical()
    assert manifest.diagnostic_definitions["horizons"] == config.diagnostic_horizons.canonical()
    assert manifest.spy_benchmark_snapshot_id is None
    assert manifest.generated_at == generated_at


def test_manifest_build_fails_closed_on_tampered_upstream_artifact(tmp_path):
    feature_dir, swing20_dir, _ = _build_fixture_snapshots(tmp_path)
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]})
    tampered.to_parquet(swing20_dir / "prices.parquet")

    config = Exp005Config()
    from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import FrozenArtifactVerificationError

    with pytest.raises(FrozenArtifactVerificationError):
        build_experiment_manifest(config, feature_dir, period_start=date(2026, 1, 5), period_end=date(2026, 1, 7), code_commit_sha="abc123")


def test_manifest_is_complete_when_all_fields_populated(tmp_path):
    feature_dir, _, _ = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()
    manifest = build_experiment_manifest(
        config, feature_dir, period_start=date(2026, 1, 5), period_end=date(2026, 1, 7), code_commit_sha="abc123",
    )
    assert manifest.is_complete() is True


def test_manifest_incomplete_when_a_required_field_is_empty(tmp_path):
    feature_dir, _, _ = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()
    manifest = build_experiment_manifest(
        config, feature_dir, period_start=date(2026, 1, 5), period_end=date(2026, 1, 7), code_commit_sha="abc123",
    )
    import dataclasses

    broken = dataclasses.replace(manifest, universe_hash="")
    assert broken.is_complete() is False


def test_spy_benchmark_snapshot_id_none_does_not_block_completeness(tmp_path):
    feature_dir, _, _ = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()  # spy_benchmark defaults to all-None
    manifest = build_experiment_manifest(
        config, feature_dir, period_start=date(2026, 1, 5), period_end=date(2026, 1, 7), code_commit_sha="abc123",
    )
    assert manifest.spy_benchmark_snapshot_id is None
    assert manifest.is_complete() is True


def test_portfolio_configuration_hash_independent_of_variant():
    config_b = Exp005Config(variant_id="B")
    config_d = Exp005Config(variant_id="D", control_seed=7)
    assert config_b.portfolio_configuration_hash() == config_d.portfolio_configuration_hash()


# -------------------------------------------------------------- persisted artifact


def test_manifest_round_trips_through_the_persisted_json_artifact(tmp_path):
    feature_dir, _, _ = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()
    manifest = build_experiment_manifest(
        config, feature_dir, period_start=date(2026, 1, 5), period_end=date(2026, 1, 7),
        code_commit_sha="abc123", generated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    artifact_path = tmp_path / "experiment_manifest.json"

    write_manifest_artifact(manifest, artifact_path)
    reloaded = read_manifest_artifact(artifact_path)

    assert reloaded == manifest
    assert reloaded.canonical_dict() == manifest.canonical_dict()


def test_manifest_artifact_file_is_canonical_json(tmp_path):
    feature_dir, _, _ = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()
    manifest = build_experiment_manifest(
        config, feature_dir, period_start=date(2026, 1, 5), period_end=date(2026, 1, 7), code_commit_sha="abc123",
    )
    artifact_path = tmp_path / "experiment_manifest.json"
    write_manifest_artifact(manifest, artifact_path)

    with open(artifact_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw == manifest.canonical_dict()
    text = artifact_path.read_text(encoding="utf-8")
    assert list(json.loads(text).keys()) == sorted(json.loads(text).keys())  # sort_keys=True was honored
