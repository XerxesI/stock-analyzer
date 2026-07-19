"""Tests for EXP-005's Stage 10 freeze-validation gate (Revision 5, Section 13):
the real comparison run must refuse to start unless every Experiment Manifest
field is present and populated -- a missing/empty field raises before any variant
executes.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date

import pandas as pd
import pytest

from stock_analyzer.sandbox.exp005.config import Exp005Config
from stock_analyzer.sandbox.exp005.freeze_validation import (
    FreezeValidationError,
    missing_manifest_fields,
    validate_freeze,
)
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_dataframe, sha256_of_file
from stock_analyzer.sandbox.exp005.manifest import build_experiment_manifest

SIGNAL_START = date(2026, 1, 5)
SIGNAL_END = date(2026, 1, 6)
OUTCOME_END = date(2026, 1, 7)


def _write_parquet(path, df: pd.DataFrame) -> None:
    df.to_parquet(path)


def _build_fixture_snapshots(tmp_path):
    swing20_dir = tmp_path / "swing_20" / "snapshots" / "swing20_test"
    swing20_dir.mkdir(parents=True)
    prices_df = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA"],
            "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "Open": [10.0, 10.1, 10.2], "High": [10.5, 10.6, 10.7], "Low": [9.5, 9.6, 9.7],
            "Close": [10.2, 10.3, 10.4], "Volume": [1000, 1100, 1200],
        }
    )
    other_df = pd.DataFrame({"symbol": ["AAA"], "value": [1]})
    artifact_dfs = {"universe": other_df, "prices": prices_df, "labels": other_df, "eligibility": other_df, "failures": other_df}
    artifact_hashes, artifacts_paths = {}, {}
    for name, df in artifact_dfs.items():
        path = swing20_dir / f"{name}.parquet"
        _write_parquet(path, df)
        artifact_hashes[name] = sha256_of_file(path)
        artifacts_paths[name] = str(path)
    with open(swing20_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump({"dataset_version": "swing20_test", "artifacts": artifacts_paths, "artifact_hashes": artifact_hashes}, f)

    feature_dir = tmp_path / "swing_20_features" / "snapshots" / "swing20_features_test"
    feature_dir.mkdir(parents=True)
    features_df = pd.DataFrame({"symbol": ["AAA"], "date": pd.to_datetime(["2026-01-05"]), "f1": [1.0]})
    _write_parquet(feature_dir / "features.parquet", features_df)
    with open(feature_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "dataset_version": "swing20_features_test",
                "source_swing20_snapshot_id": "swing20_test",
                "source_swing20_snapshot_dir": str(swing20_dir),
                "source_swing20_artifact_hashes": artifact_hashes,
                "feature_dataset_hash": sha256_of_dataframe(features_df),
            },
            f,
        )
    return feature_dir


@pytest.fixture
def complete_manifest(tmp_path):
    feature_dir = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()
    return build_experiment_manifest(
        config, feature_dir, SIGNAL_START, SIGNAL_END, OUTCOME_END, code_commit_sha="abc123",
    )


def test_complete_manifest_passes_validation(complete_manifest):
    assert missing_manifest_fields(complete_manifest) == []
    validate_freeze(complete_manifest)  # must not raise


def test_spy_benchmark_none_does_not_block_freeze(complete_manifest):
    assert complete_manifest.spy_benchmark_snapshot_id is None
    validate_freeze(complete_manifest)  # must not raise -- contextual only


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("experiment_id", ""),
        ("code_commit_sha", ""),
        ("model_version", ""),
        ("universe_hash", ""),
        ("ohlc_hash", ""),
        ("signal_hash", ""),
        ("eligibility_hash", ""),
        ("feature_hash", ""),
        ("calendar_version", ""),
        ("feature_snapshot_id", ""),
        ("swing20_snapshot_id", ""),
        ("portfolio_configuration_hash", ""),
        ("schema_version", 0),
        ("decision_audit_schema_version", 0),
        ("calendar_session_count", 0),
        ("control_seed_list", ()),
        ("feasibility_criteria", {}),
        ("diagnostic_definitions", {}),
    ],
)
def test_each_missing_field_individually_blocks_freeze(complete_manifest, field, bad_value):
    broken = dataclasses.replace(complete_manifest, **{field: bad_value})

    assert field in missing_manifest_fields(broken)
    with pytest.raises(FreezeValidationError, match=field):
        validate_freeze(broken)


def test_multiple_missing_fields_are_all_named(complete_manifest):
    broken = dataclasses.replace(complete_manifest, code_commit_sha="", universe_hash="")

    missing = missing_manifest_fields(broken)

    assert "code_commit_sha" in missing
    assert "universe_hash" in missing
    assert len(missing) == 2


def test_validate_freeze_performs_no_side_effects(complete_manifest):
    """A pure inspection -- calling it twice must not mutate the manifest or raise
    differently the second time."""

    validate_freeze(complete_manifest)
    validate_freeze(complete_manifest)
    assert missing_manifest_fields(complete_manifest) == []
