"""Tests for EXP-005's diagnostics mediated loading boundary (Stage 11, corrected
in the Stage 11-15 closure cycle, finding 5): load_diagnostics_context re-verifies
the frozen prices artifact against a manifest loaded fresh from its PERSISTED
artifact file, AND verifies the supplied database actually holds a COMPLETED
replay whose own persisted provenance matches that manifest -- Section 30's
pure-function contract, and Section 26's decision/observation isolation, enforced
at the loading step, not just documented.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.replay import COMPLETED, DEVELOPMENT_HISTORICAL_REPLAY, FAILED, ReplayMetadata
from stock_analyzer.sandbox.exp005.config import Exp005Config
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import (
    DiagnosticsProvenanceError,
    load_diagnostics_context,
)
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_dataframe, sha256_of_file
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.exp005.manifest import ExperimentManifest, build_experiment_manifest, write_manifest_artifact
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

SIGNAL_START = date(2026, 1, 5)
SIGNAL_END = date(2026, 1, 6)
OUTCOME_END = date(2026, 1, 7)
REPLAY_ID = "replay-diag-1"
NOW = datetime.now(timezone.utc)


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


def _make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    return conn


def _valid_replay_metadata(manifest: ExperimentManifest, manifest_artifact_hash: str, **overrides) -> ReplayMetadata:
    configuration_json = json.dumps({"manifest_artifact_hash": manifest_artifact_hash}, sort_keys=True)
    base = dict(
        replay_id=REPLAY_ID,
        classification=DEVELOPMENT_HISTORICAL_REPLAY,
        signal_start_date=manifest.signal_start_date,
        signal_end_date=manifest.signal_end_date,
        outcome_data_end_date=manifest.outcome_data_end_date,
        configuration_json=configuration_json,
        configuration_hash="irrelevant-to-this-boundary",
        started_at=NOW,
        status=COMPLETED,
        code_commit_sha=manifest.code_commit_sha,
        model_version=manifest.model_version,
        feature_snapshot_id=manifest.feature_snapshot_id,
        market_data_snapshot_id=manifest.ohlc_hash,
        completed_at=NOW,
    )
    base.update(overrides)
    return ReplayMetadata(**base)


def _setup(tmp_path):
    feature_dir = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()
    manifest = build_experiment_manifest(config, feature_dir, SIGNAL_START, SIGNAL_END, OUTCOME_END, code_commit_sha="abc123")
    manifest_path = tmp_path / "experiment_manifest.json"
    write_manifest_artifact(manifest, manifest_path)
    manifest_artifact_hash = sha256_of_file(manifest_path)
    conn = _make_connection()
    return feature_dir, manifest, manifest_path, manifest_artifact_hash, conn


def test_load_diagnostics_context_succeeds_for_consistent_manifest_and_completed_replay(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(_valid_replay_metadata(manifest, manifest_artifact_hash))

    context = load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)

    assert context.manifest == manifest
    assert context.replay_id == REPLAY_ID
    assert not context.prices_df.empty
    assert context.portfolio_repo is not None
    assert context.sandbox_repo is not None


def test_load_diagnostics_context_rejects_mismatched_ohlc_hash(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    wrong_manifest = dataclasses.replace(manifest, ohlc_hash="0" * 64)
    write_manifest_artifact(wrong_manifest, manifest_path)
    SandboxRepository(conn).create_replay_metadata(_valid_replay_metadata(wrong_manifest, sha256_of_file(manifest_path)))

    with pytest.raises(Exception, match="ohlc_hash|prices hash"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_load_diagnostics_context_rejects_mismatched_feature_snapshot_id(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    wrong_manifest = dataclasses.replace(manifest, feature_snapshot_id="some-other-snapshot")
    write_manifest_artifact(wrong_manifest, manifest_path)
    SandboxRepository(conn).create_replay_metadata(_valid_replay_metadata(wrong_manifest, sha256_of_file(manifest_path)))

    with pytest.raises(DiagnosticsProvenanceError, match="feature_snapshot_id"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_load_diagnostics_context_rejects_tampered_prices_file(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(_valid_replay_metadata(manifest, manifest_artifact_hash))

    swing20_dir = tmp_path / "swing_20" / "snapshots" / "swing20_test"
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]})
    tampered.to_parquet(swing20_dir / "prices.parquet")

    with pytest.raises(Exception):  # FrozenArtifactVerificationError, from re-verifying lineage
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


# --------------------------------------------- replay-provenance boundary (finding 5)


def test_missing_replay_metadata_row_rejected(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    # No replay_metadata row inserted at all.

    with pytest.raises(DiagnosticsProvenanceError, match="no replay_metadata row"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_non_completed_replay_status_rejected(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, manifest_artifact_hash, status=FAILED, completed_at=None)
    )

    with pytest.raises(DiagnosticsProvenanceError, match="COMPLETED"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


@pytest.mark.parametrize(
    "field_name,override_value",
    [
        ("code_commit_sha", "WRONG-COMMIT"),
        ("model_version", "WRONG-MODEL-VERSION"),
        ("feature_snapshot_id", "WRONG-SNAPSHOT-ID"),
        ("market_data_snapshot_id", "WRONG-MARKET-DATA-ID"),
        ("signal_start_date", date(2020, 1, 1)),
        ("signal_end_date", date(2020, 1, 2)),
        ("outcome_data_end_date", date(2020, 1, 3)),
    ],
)
def test_replay_provenance_mismatch_rejected(tmp_path, field_name, override_value):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, manifest_artifact_hash, **{field_name: override_value})
    )

    with pytest.raises(DiagnosticsProvenanceError, match=field_name):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_replay_configuration_json_missing_manifest_artifact_hash_rejected(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, manifest_artifact_hash, configuration_json=json.dumps({}))
    )

    with pytest.raises(DiagnosticsProvenanceError, match="manifest_artifact_hash"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_replay_configuration_json_wrong_manifest_artifact_hash_rejected(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, "0" * 64)  # a DIFFERENT manifest artifact's hash
    )

    with pytest.raises(DiagnosticsProvenanceError, match="manifest_artifact_hash"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_replay_malformed_configuration_json_rejected(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, manifest_artifact_hash, configuration_json="not valid json{")
    )

    with pytest.raises(DiagnosticsProvenanceError, match="not valid JSON"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)
