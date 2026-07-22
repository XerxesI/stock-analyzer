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
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.replay import COMPLETED, DEVELOPMENT_HISTORICAL_REPLAY, FAILED, ReplayMetadata
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.exp005.config import VARIANT_B, VARIANT_D, Exp005Config
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import (
    DiagnosticsProvenanceError,
    load_diagnostics_context,
)
from stock_analyzer.sandbox.exp005.domain.accounting import compute_sell_accounting
from stock_analyzer.sandbox.exp005.domain.execution import SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import to_price_units
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_dataframe, sha256_of_file
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
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


def _valid_replay_metadata(
    manifest: ExperimentManifest, manifest_artifact_hash: str, exp005_config: Exp005Config | None = None, **overrides
) -> ReplayMetadata:
    """`configuration_json`'s shape mirrors `real_run.py`'s own
    `_configuration_identity` payload exactly (`exp005_config`/`manifest`/
    `manifest_artifact_hash`) -- Stage 11-15 third closure: `load_diagnostics_
    context` now derives this replay's variant/seed identity and feasibility
    criteria from the `exp005_config` sub-object here, never from a caller
    argument, so the fixture must actually carry one to exercise the real
    code path."""

    exp005_config = exp005_config or Exp005Config()
    configuration_json = json.dumps(
        {
            "exp005_config": exp005_config.canonical_dict(),
            "manifest": manifest.canonical_dict(),
            "manifest_artifact_hash": manifest_artifact_hash,
        },
        sort_keys=True,
    )
    base = dict(
        replay_id=REPLAY_ID,
        classification=DEVELOPMENT_HISTORICAL_REPLAY,
        signal_start_date=manifest.signal_start_date,
        signal_end_date=manifest.signal_end_date,
        outcome_data_end_date=manifest.outcome_data_end_date,
        configuration_json=configuration_json,
        configuration_hash=hashlib.sha256(configuration_json.encode("utf-8")).hexdigest(),
        started_at=NOW,
        status=COMPLETED,
        code_commit_sha=manifest.code_commit_sha,
        model_version=manifest.model_version,
        feature_snapshot_id=manifest.feature_snapshot_id,
        market_data_snapshot_id=manifest.ohlc_hash,
        completed_at=NOW,
    )
    base.update(overrides)
    # Callers that override `configuration_json` alone (to test some OTHER boundary,
    # e.g. a missing/wrong manifest_artifact_hash inside it) want a self-consistent
    # hash so the new configuration_hash re-verification (finding 6) doesn't fire
    # first and mask the boundary they're actually targeting. Callers testing
    # finding 6 itself override `configuration_hash` explicitly too, which this
    # leaves untouched.
    if "configuration_json" in overrides and "configuration_hash" not in overrides:
        base["configuration_hash"] = hashlib.sha256(base["configuration_json"].encode("utf-8")).hexdigest()
    return ReplayMetadata(**base)


def _build_configuration_json_and_hash(exp005_config_dict: dict, manifest_dict: dict, manifest_artifact_hash: str) -> tuple[str, str]:
    """Mirrors `real_run.py`'s own `_configuration_identity` payload shape and
    hash relationship exactly, but lets a test construct/tamper with each
    sub-object independently and still recompute a genuinely self-consistent
    `configuration_hash` -- proving Stage 11-15 fourth closure's checks catch
    a mismatch that only manifests INSIDE an internally-consistent blob, which
    the second closure's configuration_hash self-check alone cannot see."""

    payload = {
        "exp005_config": exp005_config_dict, "manifest": manifest_dict, "manifest_artifact_hash": manifest_artifact_hash,
    }
    configuration_json = json.dumps(payload, sort_keys=True)
    return configuration_json, hashlib.sha256(configuration_json.encode("utf-8")).hexdigest()


def _setup(tmp_path):
    feature_dir = _build_fixture_snapshots(tmp_path)
    config = Exp005Config()
    manifest = build_experiment_manifest(config, feature_dir, SIGNAL_START, SIGNAL_END, OUTCOME_END, code_commit_sha="abc123")
    manifest_path = tmp_path / "experiment_manifest.json"
    write_manifest_artifact(manifest, manifest_path)
    manifest_artifact_hash = sha256_of_file(manifest_path)
    conn = _make_connection()
    return feature_dir, manifest, manifest_path, manifest_artifact_hash, conn


def _insert_minimal_sell_execution(
    conn: sqlite3.Connection, candidate_id: str, symbol: str, variant_id: str, control_seed: int | None, execution_date: date
) -> None:
    """A minimal, FK/CHECK-satisfying `executions` row -- SELL side needs no
    `entry_orders` row (unlike BUY), so a `ranked_candidates` row is the only
    other table this needs to exist first."""

    sandbox_repo = SandboxRepository(conn)
    portfolio_repo = PortfolioRepository(conn)
    run_id = f"run-{candidate_id}"
    sandbox_repo.create_run(
        SandboxRun(run_id=run_id, as_of_date=execution_date, command="generate-candidates", started_at=NOW, configuration_hash="t")
    )
    sandbox_repo.insert_ranked_candidate(
        RankedCandidate(
            candidate_id=candidate_id, run_id=run_id, as_of_date=execution_date, symbol=symbol, daily_rank=1,
            model_score=0.5, signal_close=10.0, atr14=1.0, max_entry_price=10.1, shadow_top10=True,
            actionable=True, exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
        )
    )
    sell_accounting = compute_sell_accounting(raw_fill_price=10.0, quantity=1.0, commission=0.0, slippage_rate=0.0)
    portfolio_repo.append_execution(
        Execution(
            execution_id=f"{candidate_id}:SELL", replay_id=REPLAY_ID, variant_id=variant_id, control_seed=control_seed,
            order_id=None, candidate_id=candidate_id, position_id=None, symbol=symbol, side=SELL,
            decision_date=execution_date, execution_date=execution_date,
            raw_market_fill_price_units=to_price_units(10.0),
            effective_fill_price_units=sell_accounting.effective_fill_price_units,
            quantity_units=sell_accounting.quantity_units, gross_notional_units=sell_accounting.gross_notional_units,
            commission_units=0, slippage_rate_units=0, slippage_cost_units=sell_accounting.slippage_cost_units,
            net_cash_flow_units=sell_accounting.net_cash_flow_units, fill_reason="SELL_TIME",
            market_data_snapshot_id="snap-1", created_at=NOW,
        )
    )


def test_load_diagnostics_context_succeeds_for_consistent_manifest_and_completed_replay(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    replay_metadata = _valid_replay_metadata(manifest, manifest_artifact_hash)
    SandboxRepository(conn).create_replay_metadata(replay_metadata)

    context = load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)

    assert context.manifest == manifest
    assert context.replay_id == REPLAY_ID
    assert not context.prices_df.empty
    # Stage 11-15 third closure: variant/seed/feasibility identity is derived
    # from the verified configuration_json, never left unpopulated.
    assert context.variant_id == VARIANT_B
    assert context.control_seed is None
    assert context.manifest_artifact_hash == manifest_artifact_hash
    assert context.configuration_hash == replay_metadata.configuration_hash
    assert context.feasibility_criteria == Exp005Config().feasibility_criteria.canonical()
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


# ------------------------------------- configuration_hash self-consistency (finding 6)


def test_configuration_json_tampered_independently_of_hash_rejected(tmp_path):
    """configuration_json was edited after the replay was written, but
    configuration_hash was left as the (now stale) hash of the ORIGINAL json --
    must fail closed before any diagnostic runs, not just before the narrower
    manifest_artifact_hash check."""
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    original_configuration_json = json.dumps({"manifest_artifact_hash": manifest_artifact_hash}, sort_keys=True)
    stale_configuration_hash = hashlib.sha256(original_configuration_json.encode("utf-8")).hexdigest()
    tampered_configuration_json = json.dumps(
        {"manifest_artifact_hash": manifest_artifact_hash, "tampered": True}, sort_keys=True
    )
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(
            manifest,
            manifest_artifact_hash,
            configuration_json=tampered_configuration_json,
            configuration_hash=stale_configuration_hash,
        )
    )

    with pytest.raises(DiagnosticsProvenanceError, match="configuration_hash"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_configuration_hash_tampered_independently_of_json_rejected(tmp_path):
    """configuration_hash was edited after the replay was written, but
    configuration_json itself is untouched -- must fail closed just as surely as
    the json-tampered case above, since either field alone being wrong means the
    persisted pair is no longer provably self-consistent."""
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, manifest_artifact_hash, configuration_hash="0" * 64)
    )

    with pytest.raises(DiagnosticsProvenanceError, match="configuration_hash"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


# --------------------------- verified variant/seed/feasibility identity (third closure)


def test_replay_configuration_json_missing_exp005_config_rejected(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(
            manifest, manifest_artifact_hash,
            # The embedded 'manifest' object is correct here -- this isolates
            # the MISSING exp005_config boundary from the (separately tested)
            # embedded-manifest-mismatch boundary.
            configuration_json=json.dumps(
                {"manifest": manifest.canonical_dict(), "manifest_artifact_hash": manifest_artifact_hash}
            ),
        )
    )

    with pytest.raises(DiagnosticsProvenanceError, match="exp005_config"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


@pytest.mark.parametrize(
    "overrides,expected_match",
    [
        ({"variant_id": "X", "control_seed": None}, "approved variants"),
        ({"variant_id": "B", "control_seed": 5}, "must never carry one"),
        ({"variant_id": "D", "control_seed": None}, "requires one"),
        ({"feasibility_criteria": {}}, "feasibility_criteria"),
    ],
)
def test_replay_configuration_json_malformed_exp005_config_rejected(tmp_path, overrides, expected_match):
    """Each case starts from an otherwise fully valid `exp005_config` payload
    (Stage 11-15 fourth closure added several MORE cross-checks against the
    manifest -- experiment_id, portfolio_configuration_hash -- so a minimal
    hand-built dict missing those would trip the wrong check first) and
    overrides only the ONE field the case actually targets."""

    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    bad_exp005_config = {**Exp005Config().canonical_dict(), **overrides}
    configuration_json = json.dumps(
        {
            "exp005_config": bad_exp005_config,
            "manifest": manifest.canonical_dict(),
            "manifest_artifact_hash": manifest_artifact_hash,
        },
        sort_keys=True,
    )
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, manifest_artifact_hash, configuration_json=configuration_json)
    )

    with pytest.raises(DiagnosticsProvenanceError, match=expected_match):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_zero_transaction_replay_gets_identity_from_configuration_not_executions(tmp_path):
    """No executions exist for this replay at all -- proves the identity comes
    entirely from the verified configuration, never defaulted or left
    undetermined just because there is nothing to cross-check it against
    (Stage 11-15 third closure, finding 1). Uses Variant D specifically so a
    passing assertion cannot be explained away as "just the default"."""

    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    exp005_config = Exp005Config(variant_id=VARIANT_D, control_seed=7)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, manifest_artifact_hash, exp005_config=exp005_config)
    )

    context = load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)

    assert context.variant_id == VARIANT_D
    assert context.control_seed == 7


def test_execution_matching_configuration_identity_succeeds(tmp_path):
    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(_valid_replay_metadata(manifest, manifest_artifact_hash))
    _insert_minimal_sell_execution(
        conn, candidate_id="match-1", symbol="AAA", variant_id=VARIANT_B, control_seed=None, execution_date=SIGNAL_START
    )

    context = load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)

    assert context.variant_id == VARIANT_B
    assert context.control_seed is None


def test_execution_variant_mismatch_with_configuration_fails_closed(tmp_path):
    """A database whose own `executions` rows were written under a DIFFERENT
    variant than its `configuration_json` claims must fail closed -- a Variant
    D run's executions can never masquerade as (or be silently accepted
    alongside) a Variant B configuration (Stage 11-15 third closure,
    finding 1)."""

    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    SandboxRepository(conn).create_replay_metadata(_valid_replay_metadata(manifest, manifest_artifact_hash))  # Variant B
    _insert_minimal_sell_execution(
        conn, candidate_id="mismatch-1", symbol="AAA", variant_id=VARIANT_D, control_seed=3, execution_date=SIGNAL_START
    )

    with pytest.raises(DiagnosticsProvenanceError, match="does not match this replay's own configuration-derived identity"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_execution_control_seed_mismatch_with_configuration_fails_closed(tmp_path):
    """Same class of defect as above, but with the variant matching and only
    the SEED disagreeing -- one Variant D seed's executions can never be
    silently attributed to a different seed's configuration."""

    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    exp005_config = Exp005Config(variant_id=VARIANT_D, control_seed=5)
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(manifest, manifest_artifact_hash, exp005_config=exp005_config)
    )
    _insert_minimal_sell_execution(
        conn, candidate_id="mismatch-2", symbol="AAA", variant_id=VARIANT_D, control_seed=6, execution_date=SIGNAL_START
    )

    with pytest.raises(DiagnosticsProvenanceError, match="does not match this replay's own configuration-derived identity"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


# ------------------------------- feasibility/manifest anchoring (fourth closure)


def test_altered_feasibility_thresholds_with_recomputed_hash_still_rejected(tmp_path):
    """Even a WHOLESALE regenerated configuration_json, with a genuinely
    self-consistent configuration_hash (proving only that this exact blob's
    text was not edited afterward, per the second closure's finding 6), must
    still be rejected if its exp005_config.feasibility_criteria disagrees
    with the manifest's own frozen criteria -- Stage 11-15 fourth closure,
    finding 2. The prior configuration_hash check alone cannot catch this,
    since the tampering happened BEFORE the hash was computed."""

    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    tampered_exp005_config = dict(Exp005Config().canonical_dict())
    tampered_exp005_config["feasibility_criteria"] = {
        "max_drawdown_threshold": "0.99", "largest_win_pct_of_net_profit_threshold": "0.99",
        "control_percentile_threshold": "1.0", "min_profit_factor": "0.01",
    }
    configuration_json, configuration_hash = _build_configuration_json_and_hash(
        tampered_exp005_config, manifest.canonical_dict(), manifest_artifact_hash
    )
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(
            manifest, manifest_artifact_hash, configuration_json=configuration_json, configuration_hash=configuration_hash,
        )
    )

    with pytest.raises(DiagnosticsProvenanceError, match="feasibility_criteria"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_altered_embedded_manifest_with_recomputed_hash_still_rejected(tmp_path):
    """Same class of gap as above, for the embedded manifest snapshot itself:
    a configuration_json whose exp005_config still cites the CORRECT
    manifest_artifact_hash, but whose embedded 'manifest' object was
    independently altered (e.g. a different model_version) before the hash
    was (correctly) recomputed, must still be rejected -- Stage 11-15 fourth
    closure, finding 1."""

    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    tampered_manifest_dict = dict(manifest.canonical_dict())
    tampered_manifest_dict["model_version"] = "a-different-model-version"
    configuration_json, configuration_hash = _build_configuration_json_and_hash(
        Exp005Config().canonical_dict(), tampered_manifest_dict, manifest_artifact_hash
    )
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(
            manifest, manifest_artifact_hash, configuration_json=configuration_json, configuration_hash=configuration_hash,
        )
    )

    with pytest.raises(DiagnosticsProvenanceError, match="embedded 'manifest'"):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


@pytest.mark.parametrize("bad_control_seed", [99_999, "5"], ids=["unknown_seed", "non_integer_seed"])
def test_variant_d_unknown_or_non_integer_seed_with_zero_executions_rejected(tmp_path, bad_control_seed):
    """A Variant D configuration with a seed that is either not one of the
    manifest's approved seeds, or not even an integer, must be rejected
    purely from the configuration itself -- no executions exist for this
    replay at all, so this cannot be "caught later" by the execution
    cross-check; it must fail at load time (Stage 11-15 fourth closure,
    finding 4)."""

    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    bad_exp005_config = dict(Exp005Config().canonical_dict())
    bad_exp005_config["variant_id"] = VARIANT_D
    bad_exp005_config["control_seed"] = bad_control_seed
    configuration_json, configuration_hash = _build_configuration_json_and_hash(
        bad_exp005_config, manifest.canonical_dict(), manifest_artifact_hash
    )
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(
            manifest, manifest_artifact_hash, configuration_json=configuration_json, configuration_hash=configuration_hash,
        )
    )

    with pytest.raises(DiagnosticsProvenanceError):
        load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)


def test_correct_configuration_manifest_pair_passes(tmp_path):
    """A genuinely correct, self-consistent, manifest-anchored
    configuration/manifest pair must NOT be rejected by any of the fourth
    closure's new checks -- the positive case alongside all the negative ones
    above."""

    feature_dir, manifest, manifest_path, manifest_artifact_hash, conn = _setup(tmp_path)
    exp005_config = Exp005Config(variant_id=VARIANT_D, control_seed=12)
    configuration_json, configuration_hash = _build_configuration_json_and_hash(
        exp005_config.canonical_dict(), manifest.canonical_dict(), manifest_artifact_hash
    )
    SandboxRepository(conn).create_replay_metadata(
        _valid_replay_metadata(
            manifest, manifest_artifact_hash, configuration_json=configuration_json, configuration_hash=configuration_hash,
        )
    )

    context = load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)

    assert context.variant_id == VARIANT_D
    assert context.control_seed == 12
    assert context.feasibility_criteria == manifest.feasibility_criteria
