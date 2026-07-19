"""The ONE official entry point for a REAL EXP-005 comparison run -- Stage 10
closure (P1 review, twice-revised). Every check below runs BEFORE any service is
constructed or `ReplayService.run` is called; a failure of any single check
raises before any replay metadata row is created or any domain row is written.

Second closure round fixed three further confirmed P1s:

1. **Exact trading-date binding.** The gate previously derived `calendar_version`
   from `min(trading_dates)`/`max(trading_dates)` alone, which let a caller
   silently omit an internal session while keeping the same endpoints. The gate
   now recomputes the FULL ordered session-date sequence from the re-verified
   frozen prices artifact (`exp005.manifest.compute_frozen_calendar`, spanning
   the manifest's own frozen `signal_start_date`..`outcome_data_end_date`) and
   requires the caller's `trading_dates` to equal it element-for-element -- any
   missing, added, reordered, or duplicated date is rejected.
2. **Exact-equality frozen-rule checks.** The gate previously only checked that
   `control_seed_list`/`feasibility_criteria`/`diagnostic_definitions` were
   non-empty -- a manifest with invented (but non-empty) values for any of them
   passed. It now compares each against the frozen source of truth by exact
   equality (`DEFAULT_CONTROL_SEEDS`, `exp005_config.feasibility_criteria.
   canonical()`, `manifest.build_canonical_diagnostic_definitions` -- the SAME
   function the manifest builder itself uses, so the two can never drift).
3. **No caller-supplied model/universe/replay-id.** The boundary previously took
   an arbitrary `model_adapter`/`universe_provider` from the caller -- even a
   fake one passed every check, since nothing tied it to the manifest. Both are
   now constructed INTERNALLY from the verified feature snapshot's own
   `features.parquet`, and `manifest.model_version` is checked against the
   running code's `MODEL_VERSION` constant before that construction happens.
   `replay_id` is derived solely from `replay_metadata_template.replay_id` (the
   separate, potentially-inconsistent argument was removed). The manifest is now
   loaded from its PERSISTED artifact file (never an in-memory object the caller
   could construct without ever freezing it), and the replay's own configuration
   identity incorporates that artifact file's own hash.

`code_commit_sha`/working-tree-clean overrides are NOT public parameters (a prior
revision exposed them, which let a caller certify its own commit/cleanliness) --
the production path always reads the real `git` state; tests monkeypatch the
private `_current_code_commit_sha`/`_working_tree_is_clean` module functions
directly instead.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

from stock_analyzer.sandbox.application.candidate_service import HistoricalFeatureUniverseProvider
from stock_analyzer.sandbox.application.replay_service import ReplayRunResult
from stock_analyzer.sandbox.config import MODEL_VERSION, SandboxConfig
from stock_analyzer.sandbox.domain.replay import ReplayMetadata
from stock_analyzer.sandbox.exp005.application.replay import build_exp005_replay_services
from stock_analyzer.sandbox.exp005.config import DEFAULT_CONTROL_SEEDS, VARIANT_B, VARIANT_D, Exp005Config
from stock_analyzer.sandbox.exp005.freeze_validation import FreezeValidationError, validate_freeze
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import (
    VerifiedLineage,
    sha256_of_file,
    verify_frozen_lineage,
)
from stock_analyzer.sandbox.exp005.infrastructure.frozen_market_data_provider import FrozenSwing20MarketDataProvider
from stock_analyzer.sandbox.exp005.manifest import (
    ExperimentManifest,
    build_canonical_diagnostic_definitions,
    compute_frozen_calendar,
    read_manifest_artifact,
)
from stock_analyzer.sandbox.infrastructure.model2_prediction_adapter import Model2PredictionAdapter
from stock_analyzer.sandbox.infrastructure.schema import SCHEMA_VERSION


class RealRunGateError(RuntimeError):
    """Raised when any real-run precondition fails -- the manifest is incomplete,
    or does not genuinely describe the artifacts/config/environment/trading-date
    sequence/replay identity about to be used. Raised BEFORE any service is
    constructed, any replay metadata row is created, or any domain row is
    written."""


def _current_code_commit_sha(repo_root: str | Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _working_tree_is_clean(repo_root: str | Path | None = None) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() == ""


def _configuration_identity(
    exp005_config: Exp005Config, manifest: ExperimentManifest, manifest_artifact_hash: str
) -> tuple[str, str]:
    """The replay's own configuration_json/configuration_hash (which
    ReplayService.run uses for ITS OWN independent resume/mismatch protection)
    incorporate the PERSISTED manifest artifact FILE's own hash, not merely the
    in-memory manifest object's content -- so the replay's identity is provably
    tied to a manifest that was actually frozen to disk, not one constructed
    fresh in memory each time without ever being written down."""

    payload = {
        "exp005_config": exp005_config.canonical_dict(),
        "manifest": manifest.canonical_dict(),
        "manifest_artifact_hash": manifest_artifact_hash,
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return canonical_json, hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def verify_real_run_preconditions(
    manifest: ExperimentManifest,
    exp005_config: Exp005Config,
    feature_snapshot_dir: str | Path,
    trading_dates: list[date],
    replay_metadata_template: ReplayMetadata,
) -> VerifiedLineage:
    """Every check below is independent -- a caller integrating this can rely on
    the FIRST failure's message alone. Returns the freshly re-verified lineage on
    success (proof that verification actually happened), for the caller to reuse
    without re-hashing."""

    try:
        validate_freeze(manifest)
    except FreezeValidationError as e:
        raise RealRunGateError(str(e)) from e

    if exp005_config.experiment_id != manifest.experiment_id:
        raise RealRunGateError(
            f"exp005_config.experiment_id ({exp005_config.experiment_id!r}) does not match the "
            f"manifest's recorded experiment_id ({manifest.experiment_id!r})."
        )

    actual_commit = _current_code_commit_sha()
    if actual_commit != manifest.code_commit_sha:
        raise RealRunGateError(
            f"current code commit {actual_commit!r} does not match the manifest's "
            f"code_commit_sha {manifest.code_commit_sha!r} -- a real run must execute at exactly "
            "the commit the manifest was frozen at."
        )
    if not _working_tree_is_clean():
        raise RealRunGateError(
            "the git working tree is not clean -- a real EXP-005 run requires a clean working "
            "tree so the run is provably tied to a committed state, not uncommitted local changes."
        )

    if SCHEMA_VERSION != manifest.schema_version:
        raise RealRunGateError(
            f"the running code's core schema_version ({SCHEMA_VERSION}) does not match the "
            f"manifest's recorded schema_version ({manifest.schema_version})."
        )
    if exp005_config.decision_audit_schema_version != manifest.decision_audit_schema_version:
        raise RealRunGateError(
            f"exp005_config.decision_audit_schema_version ({exp005_config.decision_audit_schema_version}) "
            f"does not match the manifest's recorded value ({manifest.decision_audit_schema_version})."
        )
    if MODEL_VERSION != manifest.model_version:
        raise RealRunGateError(
            f"the running code's MODEL_VERSION ({MODEL_VERSION!r}) does not match the manifest's "
            f"recorded model_version ({manifest.model_version!r})."
        )

    lineage = verify_frozen_lineage(feature_snapshot_dir)
    if lineage.feature_snapshot_id != manifest.feature_snapshot_id:
        raise RealRunGateError(
            f"the supplied feature snapshot ({lineage.feature_snapshot_id!r}) does not match the "
            f"manifest's recorded feature_snapshot_id ({manifest.feature_snapshot_id!r})."
        )
    if lineage.swing20_snapshot_id != manifest.swing20_snapshot_id:
        raise RealRunGateError(
            f"the supplied SWING_20 snapshot ({lineage.swing20_snapshot_id!r}) does not match the "
            f"manifest's recorded swing20_snapshot_id ({manifest.swing20_snapshot_id!r})."
        )
    _artifact_checks = (
        ("universe", manifest.universe_hash),
        ("prices", manifest.ohlc_hash),
        ("labels", manifest.signal_hash),
        ("eligibility", manifest.eligibility_hash),
    )
    for artifact_name, expected_hash in _artifact_checks:
        actual_hash = lineage.artifact_hashes[artifact_name]
        if actual_hash != expected_hash:
            raise RealRunGateError(
                f"the supplied {artifact_name} artifact's re-verified hash ({actual_hash!r}) does "
                f"not match the manifest's recorded value ({expected_hash!r})."
            )
    if lineage.feature_dataset_hash != manifest.feature_hash:
        raise RealRunGateError(
            f"the supplied feature dataset's re-verified semantic hash ({lineage.feature_dataset_hash!r}) "
            f"does not match the manifest's recorded feature_hash ({manifest.feature_hash!r})."
        )

    actual_portfolio_hash = exp005_config.portfolio_configuration_hash()
    if actual_portfolio_hash != manifest.portfolio_configuration_hash:
        raise RealRunGateError(
            f"exp005_config's portfolio_configuration_hash ({actual_portfolio_hash!r}) does not "
            f"match the manifest's recorded value ({manifest.portfolio_configuration_hash!r})."
        )

    if manifest.control_seed_list != DEFAULT_CONTROL_SEEDS:
        raise RealRunGateError(
            f"the manifest's control_seed_list has been altered from the frozen approved seed "
            f"list -- manifest has {manifest.control_seed_list!r}, frozen is {DEFAULT_CONTROL_SEEDS!r}."
        )
    expected_feasibility = exp005_config.feasibility_criteria.canonical()
    if manifest.feasibility_criteria != expected_feasibility:
        raise RealRunGateError(
            f"the manifest's feasibility_criteria does not exactly match exp005_config's frozen "
            f"feasibility_criteria -- manifest={manifest.feasibility_criteria!r}, "
            f"expected={expected_feasibility!r}."
        )
    expected_diagnostics = build_canonical_diagnostic_definitions(exp005_config)
    if manifest.diagnostic_definitions != expected_diagnostics:
        raise RealRunGateError(
            f"the manifest's diagnostic_definitions does not exactly match the canonical frozen "
            f"definitions -- manifest={manifest.diagnostic_definitions!r}, "
            f"expected={expected_diagnostics!r}."
        )

    if exp005_config.variant_id == VARIANT_B and exp005_config.control_seed is not None:
        raise RealRunGateError("Variant B must not carry a control_seed.")
    if exp005_config.variant_id == VARIANT_D:
        if exp005_config.control_seed not in manifest.control_seed_list:
            raise RealRunGateError(
                f"control_seed {exp005_config.control_seed!r} is not in the manifest's approved "
                f"{len(manifest.control_seed_list)}-seed list."
            )

    if replay_metadata_template.signal_start_date != manifest.signal_start_date:
        raise RealRunGateError(
            f"replay_metadata_template.signal_start_date ({replay_metadata_template.signal_start_date}) "
            f"does not match the manifest's frozen signal_start_date ({manifest.signal_start_date})."
        )
    if replay_metadata_template.signal_end_date != manifest.signal_end_date:
        raise RealRunGateError(
            f"replay_metadata_template.signal_end_date ({replay_metadata_template.signal_end_date}) "
            f"does not match the manifest's frozen signal_end_date ({manifest.signal_end_date})."
        )
    if replay_metadata_template.outcome_data_end_date != manifest.outcome_data_end_date:
        raise RealRunGateError(
            f"replay_metadata_template.outcome_data_end_date "
            f"({replay_metadata_template.outcome_data_end_date}) does not match the manifest's frozen "
            f"outcome_data_end_date ({manifest.outcome_data_end_date})."
        )

    if not trading_dates:
        raise RealRunGateError("trading_dates must be non-empty.")
    expected_dates, expected_calendar_version = compute_frozen_calendar(
        lineage.prices_df, manifest.signal_start_date, manifest.outcome_data_end_date
    )
    if expected_calendar_version != manifest.calendar_version:
        raise RealRunGateError(
            "internal inconsistency: recomputing the calendar from the re-verified frozen prices "
            f"artifact over the manifest's own frozen period yields calendar_version "
            f"{expected_calendar_version!r}, which does not match the manifest's own recorded "
            f"calendar_version {manifest.calendar_version!r} -- the frozen prices artifact may have "
            "changed since the manifest was built."
        )
    if len(expected_dates) != manifest.calendar_session_count:
        raise RealRunGateError(
            f"the manifest's calendar_session_count ({manifest.calendar_session_count}) does not "
            f"match the re-derived session count ({len(expected_dates)})."
        )
    if tuple(trading_dates) != expected_dates:
        raise RealRunGateError(
            "the supplied trading_dates do not exactly equal the manifest's frozen calendar "
            f"sequence ({len(expected_dates)} sessions) -- a missing, added, reordered, or "
            "duplicated date is rejected, not just a changed endpoint."
        )

    return lineage


def run_real_experiment(
    conn: sqlite3.Connection,
    manifest_artifact_path: str | Path,
    feature_snapshot_dir: str | Path,
    exp005_config: Exp005Config,
    replay_metadata_template: ReplayMetadata,
    trading_dates: list[date],
    sandbox_config: SandboxConfig | None = None,
) -> ReplayRunResult:
    """The only sanctioned way to run a real EXP-005 Variant B or Variant D
    replay. `manifest_artifact_path` must point at a manifest already persisted
    via `exp005.manifest.write_manifest_artifact` -- loaded fresh here, never
    accepted as an in-memory object, so a real run is provably tied to a manifest
    that was actually frozen to disk. `replay_id` is taken solely from
    `replay_metadata_template.replay_id`; the model adapter and feature-universe
    provider are constructed internally from the verified feature snapshot, never
    supplied by the caller."""

    manifest = read_manifest_artifact(manifest_artifact_path)
    manifest_artifact_hash = sha256_of_file(manifest_artifact_path)

    lineage = verify_real_run_preconditions(
        manifest, exp005_config, feature_snapshot_dir, trading_dates, replay_metadata_template,
    )

    configuration_json, configuration_hash = _configuration_identity(exp005_config, manifest, manifest_artifact_hash)
    replay_metadata = dataclasses.replace(
        replay_metadata_template, configuration_json=configuration_json, configuration_hash=configuration_hash,
    )

    features_path = str(lineage.feature_snapshot_dir / "features.parquet")
    model_adapter = Model2PredictionAdapter(features_path)
    universe_provider = HistoricalFeatureUniverseProvider(features_path)
    market_data_provider = FrozenSwing20MarketDataProvider(feature_snapshot_dir)

    services = build_exp005_replay_services(
        conn, model_adapter, universe_provider, market_data_provider, exp005_config,
        replay_metadata_template.replay_id, market_data_snapshot_id=manifest.ohlc_hash,
        sandbox_config=sandbox_config,
    )
    return services.replay_service.run(replay_metadata, trading_dates)
