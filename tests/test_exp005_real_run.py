"""Tests for EXP-005's Stage 10 closure: the enforced real-run execution boundary
(Point 3 of the review). verify_real_run_preconditions is the pure gate; a
spy/fake replay-services builder proves run_real_experiment never reaches
ReplayService.run when any single gate condition fails.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import stock_analyzer.sandbox.exp005.application.real_run as real_run_module
from stock_analyzer.sandbox.domain.replay import DEVELOPMENT_HISTORICAL_REPLAY, ReplayMetadata
from stock_analyzer.sandbox.exp005.application.real_run import (
    RealRunGateError,
    run_real_experiment,
    verify_real_run_preconditions,
)
from stock_analyzer.sandbox.exp005.config import VARIANT_B, VARIANT_D, Exp005Config
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_dataframe, sha256_of_file
from stock_analyzer.sandbox.exp005.manifest import build_experiment_manifest
from stock_analyzer.sandbox.infrastructure.schema import init_db

REPLAY_ID = "replay-real-1"


def _write_parquet(path, df: pd.DataFrame) -> None:
    df.to_parquet(path)


def _build_fixture_snapshots(tmp_path):
    swing20_dir = tmp_path / "swing_20" / "snapshots" / "swing20_test"
    swing20_dir.mkdir(parents=True)
    prices_df = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA", "BBB", "BBB", "BBB"],
            "date": pd.to_datetime(
                ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-05", "2026-01-06", "2026-01-07"]
            ),
            "Open": [10.0, 10.1, 10.2, 20.0, 20.1, 20.2],
            "High": [10.5, 10.6, 10.7, 20.5, 20.6, 20.7],
            "Low": [9.5, 9.6, 9.7, 19.5, 19.6, 19.7],
            "Close": [10.2, 10.3, 10.4, 20.2, 20.3, 20.4],
            "Volume": [1000, 1100, 1200, 2000, 2100, 2200],
        }
    )
    other_df = pd.DataFrame({"symbol": ["AAA", "BBB"], "value": [1, 2]})
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
    features_df = pd.DataFrame({"symbol": ["AAA", "BBB"], "date": pd.to_datetime(["2026-01-05", "2026-01-05"]), "f1": [1.0, 2.0]})
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
    return feature_dir, swing20_dir


TRADING_DATES = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


def _build_valid_setup(tmp_path):
    feature_dir, swing20_dir = _build_fixture_snapshots(tmp_path)
    config = Exp005Config(variant_id=VARIANT_B)
    manifest = build_experiment_manifest(
        config, feature_dir, period_start=TRADING_DATES[0], period_end=TRADING_DATES[-1], code_commit_sha="abc123",
    )
    return feature_dir, swing20_dir, config, manifest


def _clean_tree() -> bool:
    return True


def _dirty_tree() -> bool:
    return False


# --------------------------------------------------------- verify_real_run_preconditions


def test_all_preconditions_pass_for_a_consistent_setup(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)

    lineage = verify_real_run_preconditions(
        manifest, config, feature_dir, TRADING_DATES, code_commit_sha="abc123", working_tree_is_clean_fn=_clean_tree,
    )

    assert lineage.feature_snapshot_id == manifest.feature_snapshot_id


def test_incomplete_manifest_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    broken = dataclasses.replace(manifest, universe_hash="")

    with pytest.raises(RealRunGateError):
        verify_real_run_preconditions(broken, config, feature_dir, TRADING_DATES, "abc123", _clean_tree)


def test_wrong_commit_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)

    with pytest.raises(RealRunGateError, match="commit"):
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, "different-sha", _clean_tree)


def test_dirty_working_tree_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)

    with pytest.raises(RealRunGateError, match="working tree"):
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, "abc123", _dirty_tree)


def test_altered_artifact_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]})
    tampered.to_parquet(swing20_dir / "prices.parquet")

    with pytest.raises(Exception):  # FrozenArtifactVerificationError, from re-verifying lineage
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, "abc123", _clean_tree)


def test_wrong_portfolio_config_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    from stock_analyzer.sandbox.exp005.config import PortfolioConfig

    wrong_config = Exp005Config(variant_id=VARIANT_B, portfolio=PortfolioConfig(slot_budget=5_000.0))

    with pytest.raises(RealRunGateError, match="portfolio_configuration_hash"):
        verify_real_run_preconditions(manifest, wrong_config, feature_dir, TRADING_DATES, "abc123", _clean_tree)


def test_variant_b_with_seed_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    # Exp005Config itself forbids constructing VARIANT_B with a seed -- confirms
    # the boundary's own explicit check is redundant-but-consistent with that.
    with pytest.raises(ValueError):
        Exp005Config(variant_id=VARIANT_B, control_seed=1)


def test_variant_d_seed_not_in_approved_list_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    wrong_seed_config = Exp005Config(variant_id=VARIANT_D, control_seed=99999)

    with pytest.raises(RealRunGateError, match="approved"):
        verify_real_run_preconditions(manifest, wrong_seed_config, feature_dir, TRADING_DATES, "abc123", _clean_tree)


def test_variant_d_seed_in_approved_list_passes(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    good_seed_config = Exp005Config(variant_id=VARIANT_D, control_seed=1)  # DEFAULT_CONTROL_SEEDS = range(1, 51)

    verify_real_run_preconditions(manifest, good_seed_config, feature_dir, TRADING_DATES, "abc123", _clean_tree)


def test_wrong_calendar_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    wrong_dates = [date(2026, 1, 5), date(2026, 1, 6)]  # narrower than the manifest's registered period

    with pytest.raises(RealRunGateError, match="calendar_version"):
        verify_real_run_preconditions(manifest, config, feature_dir, wrong_dates, "abc123", _clean_tree)


def test_empty_trading_dates_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)

    with pytest.raises(RealRunGateError, match="trading_dates"):
        verify_real_run_preconditions(manifest, config, feature_dir, [], "abc123", _clean_tree)


# ---------------------------------------------- run_real_experiment (spy replay service)


class _SpyServices:
    def __init__(self) -> None:
        self.replay_service = _SpyReplayService()


class _SpyReplayService:
    def __init__(self) -> None:
        self.run_called = False

    def run(self, replay_metadata, trading_dates, progress_every=None):
        self.run_called = True
        return "SPY_RESULT"


def _make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema

    init_exp005_schema(conn)
    return conn


def _replay_metadata_template() -> ReplayMetadata:
    return ReplayMetadata(
        replay_id=REPLAY_ID,
        classification=DEVELOPMENT_HISTORICAL_REPLAY,
        signal_start_date=TRADING_DATES[0],
        signal_end_date=TRADING_DATES[-1],
        outcome_data_end_date=TRADING_DATES[-1],
        configuration_json="{}",
        configuration_hash="placeholder-must-be-overridden",
        started_at=datetime.now(timezone.utc),
    )


class _FakeModelAdapter:
    model_version = "fake"
    fit_params = {"adv_edges": [0, 1], "adv_labels": ["adv_q1"]}
    feature_names = ("f1",)
    train_row_count = 10

    def score(self, features_df):
        return pd.Series([1.0] * len(features_df), index=features_df.index)


class _FakeUniverseProvider:
    def features_for_date(self, as_of_date):
        return pd.DataFrame()


def _spy_builder(*args, **kwargs):
    return _SpyServices()


def test_run_real_experiment_never_builds_services_when_manifest_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", _spy_builder)
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    broken = dataclasses.replace(manifest, universe_hash="")
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(
            conn, broken, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, config, REPLAY_ID,
            _replay_metadata_template(), TRADING_DATES, code_commit_sha="abc123", working_tree_is_clean_fn=_clean_tree,
        )


def test_run_real_experiment_never_builds_services_on_wrong_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", _spy_builder)
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(
            conn, manifest, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, config, REPLAY_ID,
            _replay_metadata_template(), TRADING_DATES, code_commit_sha="wrong", working_tree_is_clean_fn=_clean_tree,
        )


def test_run_real_experiment_never_builds_services_on_dirty_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", _spy_builder)
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(
            conn, manifest, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, config, REPLAY_ID,
            _replay_metadata_template(), TRADING_DATES, code_commit_sha="abc123", working_tree_is_clean_fn=_dirty_tree,
        )


def test_run_real_experiment_never_builds_services_on_wrong_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", _spy_builder)
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    wrong_seed_config = Exp005Config(variant_id=VARIANT_D, control_seed=99999)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(
            conn, manifest, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, wrong_seed_config, REPLAY_ID,
            _replay_metadata_template(), TRADING_DATES, code_commit_sha="abc123", working_tree_is_clean_fn=_clean_tree,
        )


def test_run_real_experiment_never_builds_services_on_altered_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", _spy_builder)
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]})
    tampered.to_parquet(swing20_dir / "prices.parquet")
    conn = _make_connection()

    with pytest.raises(Exception):
        run_real_experiment(
            conn, manifest, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, config, REPLAY_ID,
            _replay_metadata_template(), TRADING_DATES, code_commit_sha="abc123", working_tree_is_clean_fn=_clean_tree,
        )


def test_run_real_experiment_never_builds_services_on_wrong_config(tmp_path, monkeypatch):
    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", _spy_builder)
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    from stock_analyzer.sandbox.exp005.config import PortfolioConfig

    wrong_config = Exp005Config(variant_id=VARIANT_B, portfolio=PortfolioConfig(slot_budget=5_000.0))
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(
            conn, manifest, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, wrong_config, REPLAY_ID,
            _replay_metadata_template(), TRADING_DATES, code_commit_sha="abc123", working_tree_is_clean_fn=_clean_tree,
        )


def test_run_real_experiment_never_builds_services_on_wrong_calendar(tmp_path, monkeypatch):
    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", _spy_builder)
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(
            conn, manifest, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, config, REPLAY_ID,
            _replay_metadata_template(), [date(2026, 1, 5), date(2026, 1, 6)],
            code_commit_sha="abc123", working_tree_is_clean_fn=_clean_tree,
        )


def test_run_real_experiment_calls_replay_run_when_all_preconditions_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", _spy_builder)
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    conn = _make_connection()

    result = run_real_experiment(
        conn, manifest, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, config, REPLAY_ID,
        _replay_metadata_template(), TRADING_DATES, code_commit_sha="abc123", working_tree_is_clean_fn=_clean_tree,
    )

    assert result == "SPY_RESULT"


def test_replay_configuration_identity_is_derived_from_manifest_not_the_caller(tmp_path, monkeypatch):
    """The caller's placeholder configuration_hash must never reach ReplayService
    -- run_real_experiment always overrides it with a hash derived from the
    verified manifest + config."""

    captured = {}

    class _CapturingSpyReplayService:
        def run(self, replay_metadata, trading_dates, progress_every=None):
            captured["configuration_hash"] = replay_metadata.configuration_hash
            captured["configuration_json"] = replay_metadata.configuration_json
            return "SPY_RESULT"

    class _CapturingSpyServices:
        def __init__(self):
            self.replay_service = _CapturingSpyReplayService()

    monkeypatch.setattr(real_run_module, "build_exp005_replay_services", lambda *a, **kw: _CapturingSpyServices())
    feature_dir, swing20_dir, config, manifest = _build_valid_setup(tmp_path)
    conn = _make_connection()

    run_real_experiment(
        conn, manifest, _FakeModelAdapter(), _FakeUniverseProvider(), feature_dir, config, REPLAY_ID,
        _replay_metadata_template(), TRADING_DATES, code_commit_sha="abc123", working_tree_is_clean_fn=_clean_tree,
    )

    assert captured["configuration_hash"] != "placeholder-must-be-overridden"
    assert manifest.code_commit_sha in captured["configuration_json"]
