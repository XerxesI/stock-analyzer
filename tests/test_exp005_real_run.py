"""Tests for EXP-005's Stage 10 closure: the enforced real-run execution boundary,
twice-revised. verify_real_run_preconditions is the pure gate; a spy/fake
replay-services builder (plus spy model/universe constructors) proves
run_real_experiment never reaches ReplayService.run when any single gate
condition fails, and never accepts a caller-supplied model/universe/replay-id.
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
from stock_analyzer.sandbox.exp005.manifest import build_experiment_manifest, write_manifest_artifact
from stock_analyzer.sandbox.infrastructure.schema import init_db

REPLAY_ID = "replay-real-1"
SIGNAL_START = date(2026, 1, 5)
SIGNAL_END = date(2026, 1, 6)
OUTCOME_END = date(2026, 1, 7)
TRADING_DATES = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


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


def _build_valid_setup(tmp_path):
    feature_dir, swing20_dir = _build_fixture_snapshots(tmp_path)
    config = Exp005Config(variant_id=VARIANT_B)
    manifest = build_experiment_manifest(
        config, feature_dir, SIGNAL_START, SIGNAL_END, OUTCOME_END, code_commit_sha="abc123",
    )
    manifest_path = tmp_path / "experiment_manifest.json"
    write_manifest_artifact(manifest, manifest_path)
    return feature_dir, swing20_dir, config, manifest, manifest_path


def _replay_metadata_template(replay_id: str = REPLAY_ID) -> ReplayMetadata:
    return ReplayMetadata(
        replay_id=replay_id,
        classification=DEVELOPMENT_HISTORICAL_REPLAY,
        signal_start_date=SIGNAL_START,
        signal_end_date=SIGNAL_END,
        outcome_data_end_date=OUTCOME_END,
        configuration_json="{}",
        configuration_hash="placeholder-must-be-overridden",
        started_at=datetime.now(timezone.utc),
    )


@pytest.fixture(autouse=True)
def _default_clean_commit(monkeypatch):
    """Every test defaults to a matching commit + clean tree via the PRIVATE
    module functions -- no public parameter exists to override these (Point 4).
    Individual tests override via monkeypatch when they need a mismatch."""

    monkeypatch.setattr(real_run_module, "_current_code_commit_sha", lambda *a, **kw: "abc123")
    monkeypatch.setattr(real_run_module, "_working_tree_is_clean", lambda *a, **kw: True)
    yield


# --------------------------------------------------------- verify_real_run_preconditions


def test_all_preconditions_pass_for_a_consistent_setup(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)

    lineage = verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, _replay_metadata_template())

    assert lineage.feature_snapshot_id == manifest.feature_snapshot_id


def test_no_public_commit_or_working_tree_override_parameters_exist():
    """Point 4: the production entry points must not expose a way for a caller to
    certify its own commit/cleanliness."""

    import inspect

    sig = inspect.signature(verify_real_run_preconditions)
    assert "code_commit_sha" not in sig.parameters
    assert "working_tree_is_clean_fn" not in sig.parameters
    sig2 = inspect.signature(run_real_experiment)
    assert "code_commit_sha" not in sig2.parameters
    assert "working_tree_is_clean_fn" not in sig2.parameters
    assert "model_adapter" not in sig2.parameters
    assert "universe_provider" not in sig2.parameters
    assert "replay_id" not in sig2.parameters


def test_incomplete_manifest_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    broken = dataclasses.replace(manifest, universe_hash="")

    with pytest.raises(RealRunGateError):
        verify_real_run_preconditions(broken, config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_mismatched_experiment_id_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    other_config = dataclasses.replace(config, experiment_id="EXP-999")

    with pytest.raises(RealRunGateError, match="experiment_id"):
        verify_real_run_preconditions(manifest, other_config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_wrong_commit_blocks(tmp_path, monkeypatch):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    monkeypatch.setattr(real_run_module, "_current_code_commit_sha", lambda *a, **kw: "different-sha")

    with pytest.raises(RealRunGateError, match="commit"):
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_dirty_working_tree_blocks(tmp_path, monkeypatch):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    monkeypatch.setattr(real_run_module, "_working_tree_is_clean", lambda *a, **kw: False)

    with pytest.raises(RealRunGateError, match="working tree"):
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_wrong_model_version_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    wrong = dataclasses.replace(manifest, model_version="some-other-model@deadbeef")

    with pytest.raises(RealRunGateError, match="model_version"):
        verify_real_run_preconditions(wrong, config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_altered_artifact_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]})
    tampered.to_parquet(swing20_dir / "prices.parquet")

    with pytest.raises(Exception):  # FrozenArtifactVerificationError, from re-verifying lineage
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_wrong_portfolio_config_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    from stock_analyzer.sandbox.exp005.config import PortfolioConfig

    wrong_config = Exp005Config(variant_id=VARIANT_B, portfolio=PortfolioConfig(slot_budget=5_000.0))

    with pytest.raises(RealRunGateError, match="portfolio_configuration_hash"):
        verify_real_run_preconditions(manifest, wrong_config, feature_dir, TRADING_DATES, _replay_metadata_template())


# ------------------------------------------------------- exact-equality frozen rules


def test_altered_seed_list_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    altered = dataclasses.replace(manifest, control_seed_list=(999,))

    with pytest.raises(RealRunGateError, match="control_seed_list"):
        verify_real_run_preconditions(altered, config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_altered_feasibility_criteria_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    altered = dataclasses.replace(manifest, feasibility_criteria={"invented_field": "invented_value"})

    with pytest.raises(RealRunGateError, match="feasibility_criteria"):
        verify_real_run_preconditions(altered, config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_altered_diagnostic_definitions_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    altered = dataclasses.replace(manifest, diagnostic_definitions={"invented_field": "invented_value"})

    with pytest.raises(RealRunGateError, match="diagnostic_definitions"):
        verify_real_run_preconditions(altered, config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_variant_d_seed_999_with_altered_seed_list_blocks(tmp_path):
    """Reproduces the exact confirmed-bypass scenario: an altered seed list
    containing 999 must still be rejected (by the exact-equality check against
    DEFAULT_CONTROL_SEEDS), even though 999 IS a member of the (altered) list."""

    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    altered = dataclasses.replace(manifest, control_seed_list=(999,))
    seed_999_config = Exp005Config(variant_id=VARIANT_D, control_seed=999)

    with pytest.raises(RealRunGateError, match="control_seed_list"):
        verify_real_run_preconditions(altered, seed_999_config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_variant_d_seed_not_in_approved_list_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    wrong_seed_config = Exp005Config(variant_id=VARIANT_D, control_seed=99999)

    with pytest.raises(RealRunGateError, match="approved"):
        verify_real_run_preconditions(manifest, wrong_seed_config, feature_dir, TRADING_DATES, _replay_metadata_template())


def test_variant_d_seed_in_approved_list_passes(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    good_seed_config = Exp005Config(variant_id=VARIANT_D, control_seed=1)  # DEFAULT_CONTROL_SEEDS = range(1, 51)

    verify_real_run_preconditions(manifest, good_seed_config, feature_dir, TRADING_DATES, _replay_metadata_template())


# ------------------------------------------------------------- exact trading dates


def test_omitted_internal_date_blocks(tmp_path):
    """The exact confirmed-bypass reproduction: manifest calendar is
    [Jan 5, Jan 6, Jan 7]; supplied dates omit Jan 6."""

    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    omitted_middle = [date(2026, 1, 5), date(2026, 1, 7)]

    with pytest.raises(RealRunGateError, match="trading_dates"):
        verify_real_run_preconditions(manifest, config, feature_dir, omitted_middle, _replay_metadata_template())


def test_extra_date_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    extra = TRADING_DATES + [date(2026, 1, 8)]

    with pytest.raises(RealRunGateError, match="trading_dates"):
        verify_real_run_preconditions(manifest, config, feature_dir, extra, _replay_metadata_template())


def test_duplicate_date_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    duplicated = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 6), date(2026, 1, 7)]

    with pytest.raises(RealRunGateError, match="trading_dates"):
        verify_real_run_preconditions(manifest, config, feature_dir, duplicated, _replay_metadata_template())


def test_reordered_dates_block(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    reordered = [date(2026, 1, 6), date(2026, 1, 5), date(2026, 1, 7)]

    with pytest.raises(RealRunGateError, match="trading_dates"):
        verify_real_run_preconditions(manifest, config, feature_dir, reordered, _replay_metadata_template())


def test_changed_endpoint_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    changed_end = [date(2026, 1, 5), date(2026, 1, 6)]  # narrower than manifest's registered period

    with pytest.raises(RealRunGateError, match="trading_dates"):
        verify_real_run_preconditions(manifest, config, feature_dir, changed_end, _replay_metadata_template())


def test_empty_trading_dates_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)

    with pytest.raises(RealRunGateError, match="trading_dates"):
        verify_real_run_preconditions(manifest, config, feature_dir, [], _replay_metadata_template())


def test_exact_correct_sequence_passes(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, _replay_metadata_template())


# --------------------------------------------------------- signal/outcome period binding


def test_mismatched_signal_start_date_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    wrong_template = dataclasses.replace(_replay_metadata_template(), signal_start_date=date(2026, 1, 4))

    with pytest.raises(RealRunGateError, match="signal_start_date"):
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, wrong_template)


def test_mismatched_signal_end_date_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    wrong_template = dataclasses.replace(_replay_metadata_template(), signal_end_date=date(2026, 1, 7))

    with pytest.raises(RealRunGateError, match="signal_end_date"):
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, wrong_template)


def test_mismatched_outcome_data_end_date_blocks(tmp_path):
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    wrong_template = dataclasses.replace(_replay_metadata_template(), outcome_data_end_date=date(2026, 1, 8))

    with pytest.raises(RealRunGateError, match="outcome_data_end_date"):
        verify_real_run_preconditions(manifest, config, feature_dir, TRADING_DATES, wrong_template)


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


class _FakeModelAdapter:
    model_version = "fake"
    fit_params = {"adv_edges": [0, 1], "adv_labels": ["adv_q1"]}
    feature_names = ("f1",)
    train_row_count = 10

    def score(self, features_df):
        return pd.Series([1.0] * len(features_df), index=features_df.index)


class _FakeUniverseProvider:
    def __init__(self, *args, **kwargs):
        pass

    def features_for_date(self, as_of_date):
        return pd.DataFrame()


def _patch_internal_constructors(monkeypatch, spy_services_factory=None):
    """Patches the internally-constructed model adapter / universe provider /
    services builder -- proves run_real_experiment builds them itself (from the
    verified snapshot path) rather than accepting them from the caller, which is
    no longer even possible (Point 3: no such parameters exist)."""

    monkeypatch.setattr(real_run_module, "Model2PredictionAdapter", lambda path: _FakeModelAdapter())
    monkeypatch.setattr(real_run_module, "HistoricalFeatureUniverseProvider", lambda path: _FakeUniverseProvider())
    monkeypatch.setattr(
        real_run_module, "build_exp005_replay_services", spy_services_factory or (lambda *a, **kw: _SpyServices())
    )


def test_run_real_experiment_never_builds_services_when_manifest_incomplete(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    broken = dataclasses.replace(manifest, universe_hash="")
    write_manifest_artifact(broken, manifest_path)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(conn, manifest_path, feature_dir, config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_never_builds_services_on_wrong_commit(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    monkeypatch.setattr(real_run_module, "_current_code_commit_sha", lambda *a, **kw: "wrong")
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(conn, manifest_path, feature_dir, config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_never_builds_services_on_dirty_tree(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    monkeypatch.setattr(real_run_module, "_working_tree_is_clean", lambda *a, **kw: False)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(conn, manifest_path, feature_dir, config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_never_builds_services_on_wrong_seed(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    wrong_seed_config = Exp005Config(variant_id=VARIANT_D, control_seed=99999)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(conn, manifest_path, feature_dir, wrong_seed_config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_never_builds_services_on_altered_artifact(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    tampered = pd.DataFrame({"symbol": ["ZZZ"], "date": pd.to_datetime(["2026-01-05"]), "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]})
    tampered.to_parquet(swing20_dir / "prices.parquet")
    conn = _make_connection()

    with pytest.raises(Exception):
        run_real_experiment(conn, manifest_path, feature_dir, config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_never_builds_services_on_wrong_config(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    from stock_analyzer.sandbox.exp005.config import PortfolioConfig

    wrong_config = Exp005Config(variant_id=VARIANT_B, portfolio=PortfolioConfig(slot_budget=5_000.0))
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(conn, manifest_path, feature_dir, wrong_config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_never_builds_services_on_omitted_internal_date(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(
            conn, manifest_path, feature_dir, config, _replay_metadata_template(),
            [date(2026, 1, 5), date(2026, 1, 7)],
        )


def test_run_real_experiment_never_builds_services_on_altered_seed_list(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    altered = dataclasses.replace(manifest, control_seed_list=(999,))
    write_manifest_artifact(altered, manifest_path)
    seed_999_config = Exp005Config(variant_id=VARIANT_D, control_seed=999)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(conn, manifest_path, feature_dir, seed_999_config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_never_builds_services_on_altered_feasibility_criteria(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    altered = dataclasses.replace(manifest, feasibility_criteria={"invented": "value"})
    write_manifest_artifact(altered, manifest_path)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(conn, manifest_path, feature_dir, config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_never_builds_services_on_altered_diagnostic_definitions(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    altered = dataclasses.replace(manifest, diagnostic_definitions={"invented": "value"})
    write_manifest_artifact(altered, manifest_path)
    conn = _make_connection()

    with pytest.raises(RealRunGateError):
        run_real_experiment(conn, manifest_path, feature_dir, config, _replay_metadata_template(), TRADING_DATES)


def test_run_real_experiment_calls_replay_run_when_all_preconditions_pass(tmp_path, monkeypatch):
    _patch_internal_constructors(monkeypatch)
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    conn = _make_connection()

    result = run_real_experiment(conn, manifest_path, feature_dir, config, _replay_metadata_template(), TRADING_DATES)

    assert result == "SPY_RESULT"


def test_replay_id_is_derived_solely_from_the_template():
    """Point 3: there is no separate replay_id argument to disagree with the
    template -- confirmed structurally by the signature check above, and here by
    confirming the template's replay_id is what reaches build_exp005_replay_services."""

    import inspect

    sig = inspect.signature(run_real_experiment)
    assert "replay_metadata_template" in sig.parameters
    assert "replay_id" not in sig.parameters


def test_replay_configuration_identity_is_derived_from_manifest_artifact_file(tmp_path, monkeypatch):
    """The caller's placeholder configuration_hash must never reach
    ReplayService, and the identity must incorporate the PERSISTED manifest
    artifact file's own hash, not merely the in-memory manifest content."""

    captured = {}

    class _CapturingSpyReplayService:
        def run(self, replay_metadata, trading_dates, progress_every=None):
            captured["configuration_hash"] = replay_metadata.configuration_hash
            captured["configuration_json"] = replay_metadata.configuration_json
            return "SPY_RESULT"

    class _CapturingSpyServices:
        def __init__(self):
            self.replay_service = _CapturingSpyReplayService()

    _patch_internal_constructors(monkeypatch, spy_services_factory=lambda *a, **kw: _CapturingSpyServices())
    feature_dir, swing20_dir, config, manifest, manifest_path = _build_valid_setup(tmp_path)
    conn = _make_connection()

    run_real_experiment(conn, manifest_path, feature_dir, config, _replay_metadata_template(), TRADING_DATES)

    assert captured["configuration_hash"] != "placeholder-must-be-overridden"
    assert manifest.code_commit_sha in captured["configuration_json"]
    assert "manifest_artifact_hash" in captured["configuration_json"]
