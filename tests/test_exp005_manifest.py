"""Tests for EXP-005's Experiment Manifest generator (Revision 5, Section 29,
Stage 9).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from stock_analyzer.sandbox.exp005.config import DEFAULT_CONTROL_SEEDS, Exp005Config
from stock_analyzer.sandbox.exp005.manifest import (
    MissingArtifactHashError,
    build_experiment_manifest,
    sha256_of_file,
)


@pytest.fixture
def feature_file(tmp_path):
    p = tmp_path / "features.parquet"
    p.write_bytes(b"fake feature dataset bytes")
    return p


@pytest.fixture
def ohlcv_file(tmp_path):
    p = tmp_path / "prices.parquet"
    p.write_bytes(b"fake ohlcv bytes")
    return p


def test_sha256_of_file_matches_hashlib_directly(feature_file):
    expected = hashlib.sha256(feature_file.read_bytes()).hexdigest()
    assert sha256_of_file(feature_file) == expected


def test_missing_artifact_raises_not_silently_empty(tmp_path):
    config = Exp005Config()
    with pytest.raises(MissingArtifactHashError):
        build_experiment_manifest(
            config, str(tmp_path / "does-not-exist.parquet"), str(tmp_path / "also-missing.parquet"),
            code_commit_sha="deadbeef",
        )


def test_manifest_assembles_every_frozen_field(feature_file, ohlcv_file):
    config = Exp005Config()
    generated_at = datetime(2026, 7, 19, tzinfo=timezone.utc)

    manifest = build_experiment_manifest(
        config, str(feature_file), str(ohlcv_file), code_commit_sha="abc123", generated_at=generated_at,
    )

    assert manifest.experiment_id == "EXP-005"
    assert manifest.code_commit_sha == "abc123"
    assert manifest.schema_version == 3
    assert manifest.decision_audit_schema_version == config.decision_audit_schema_version
    assert manifest.feature_dataset_hash == sha256_of_file(feature_file)
    assert manifest.ohlcv_hash == sha256_of_file(ohlcv_file)
    assert manifest.portfolio_configuration_hash == config.portfolio_configuration_hash()
    assert manifest.control_seed_list == DEFAULT_CONTROL_SEEDS
    assert len(manifest.control_seed_list) == 50
    assert manifest.feasibility_criteria == config.feasibility_criteria.canonical()
    assert manifest.diagnostic_definitions["horizons"] == config.diagnostic_horizons.canonical()
    assert manifest.spy_benchmark_snapshot_id is None  # not yet pulled -- valid, complete state
    assert manifest.generated_at == generated_at


def test_manifest_is_complete_when_all_fields_populated(feature_file, ohlcv_file):
    config = Exp005Config()
    manifest = build_experiment_manifest(config, str(feature_file), str(ohlcv_file), code_commit_sha="abc123")
    assert manifest.is_complete() is True


def test_manifest_incomplete_when_commit_sha_empty(feature_file, ohlcv_file):
    config = Exp005Config()
    manifest = build_experiment_manifest(config, str(feature_file), str(ohlcv_file), code_commit_sha="abc123")
    empty_sha = manifest.__class__(**{**manifest.__dict__, "code_commit_sha": ""})
    assert empty_sha.is_complete() is False


def test_manifest_incomplete_when_control_seed_list_empty(feature_file, ohlcv_file):
    config = Exp005Config()
    manifest = build_experiment_manifest(config, str(feature_file), str(ohlcv_file), code_commit_sha="abc123")
    empty_seeds = manifest.__class__(**{**manifest.__dict__, "control_seed_list": ()})
    assert empty_seeds.is_complete() is False


def test_spy_benchmark_snapshot_id_none_does_not_block_completeness(feature_file, ohlcv_file):
    """Section 5/29: spy_benchmark_snapshot_id is contextual only and never gates
    the primary comparison -- a manifest with every OTHER field populated is
    complete regardless of whether the SPY pull has happened yet."""

    config = Exp005Config()  # spy_benchmark defaults to all-None
    manifest = build_experiment_manifest(config, str(feature_file), str(ohlcv_file), code_commit_sha="abc123")
    assert manifest.spy_benchmark_snapshot_id is None
    assert manifest.is_complete() is True


def test_canonical_dict_is_json_serializable(feature_file, ohlcv_file):
    import json

    config = Exp005Config()
    manifest = build_experiment_manifest(config, str(feature_file), str(ohlcv_file), code_commit_sha="abc123")
    serialized = json.dumps(manifest.canonical_dict())
    assert "abc123" in serialized


def test_portfolio_configuration_hash_independent_of_variant():
    """portfolio_configuration_hash (Section 29) covers capital/slots/budget/
    commission/slippage/tie-break only -- must be identical regardless of which
    variant/seed the passed-in Exp005Config happens to carry."""

    config_b = Exp005Config(variant_id="B")
    config_d = Exp005Config(variant_id="D", control_seed=7)
    assert config_b.portfolio_configuration_hash() == config_d.portfolio_configuration_hash()
