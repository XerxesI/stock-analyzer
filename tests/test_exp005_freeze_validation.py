"""Tests for EXP-005's Stage 10 freeze-validation gate (Revision 5, Section 13):
the real comparison run must refuse to start unless every Experiment Manifest
field is present and populated -- a missing/empty field raises before any variant
executes.
"""

from __future__ import annotations

import dataclasses

import pytest

from stock_analyzer.sandbox.exp005.config import Exp005Config
from stock_analyzer.sandbox.exp005.freeze_validation import (
    FreezeValidationError,
    missing_manifest_fields,
    validate_freeze,
)
from stock_analyzer.sandbox.exp005.manifest import build_experiment_manifest


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


@pytest.fixture
def complete_manifest(feature_file, ohlcv_file):
    config = Exp005Config()
    return build_experiment_manifest(config, str(feature_file), str(ohlcv_file), code_commit_sha="abc123")


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
        ("feature_dataset_hash", ""),
        ("ohlcv_hash", ""),
        ("portfolio_configuration_hash", ""),
        ("schema_version", 0),
        ("decision_audit_schema_version", 0),
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
    broken = dataclasses.replace(complete_manifest, code_commit_sha="", feature_dataset_hash="")

    missing = missing_manifest_fields(broken)

    assert "code_commit_sha" in missing
    assert "feature_dataset_hash" in missing
    assert len(missing) == 2


def test_validate_freeze_performs_no_side_effects(complete_manifest):
    """A pure inspection -- calling it twice must not mutate the manifest or raise
    differently the second time."""

    validate_freeze(complete_manifest)
    validate_freeze(complete_manifest)
    assert missing_manifest_fields(complete_manifest) == []
