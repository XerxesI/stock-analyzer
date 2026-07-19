"""The ONE official entry point for a REAL EXP-005 comparison run -- Stage 10
closure (P1 review point 3). A standalone `validate_freeze()` was insufficient:
nothing actually required it to be called, so `build_exp005_replay_services`
could technically be wired and run without a manifest at all, or against an
unrelated one. This module is the single enforced execution boundary: every
check below runs BEFORE any service is constructed or `ReplayService.run` is
called, and a failure of any single check raises before any replay metadata row
is created or any domain row is written.

`verify_real_run_preconditions` is the pure gate -- no side effects, no service
construction, no database access beyond re-reading the frozen artifact files
themselves for hash verification. `run_real_experiment` is the thin orchestrator
that calls it first, then (only if it does not raise) proceeds to build and run
the real replay.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import subprocess
from datetime import date
from pathlib import Path
from typing import Callable

from stock_analyzer.sandbox.application.candidate_service import HistoricalFeatureUniverseProvider
from stock_analyzer.sandbox.application.replay_service import ReplayRunResult
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.replay import ReplayMetadata
from stock_analyzer.sandbox.exp005.application.replay import build_exp005_replay_services
from stock_analyzer.sandbox.exp005.config import VARIANT_B, VARIANT_D, Exp005Config
from stock_analyzer.sandbox.exp005.freeze_validation import FreezeValidationError, validate_freeze
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import VerifiedLineage, verify_frozen_lineage
from stock_analyzer.sandbox.exp005.infrastructure.frozen_market_data_provider import FrozenSwing20MarketDataProvider
from stock_analyzer.sandbox.exp005.manifest import ExperimentManifest, compute_calendar_version, current_code_commit_sha
from stock_analyzer.sandbox.infrastructure.schema import SCHEMA_VERSION


class RealRunGateError(RuntimeError):
    """Raised when any real-run precondition fails -- the manifest is incomplete,
    or does not genuinely describe the artifacts/config/environment about to be
    used. Raised BEFORE any service is constructed, any replay metadata row is
    created, or any domain row is written."""


def _working_tree_is_clean(repo_root: str | Path | None = None) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() == ""


def _configuration_identity(exp005_config: Exp005Config, manifest: ExperimentManifest) -> tuple[str, str]:
    """The replay's own configuration_json/configuration_hash (which
    ReplayService.run uses for ITS OWN independent resume/mismatch protection) are
    constructed HERE, from the verified config + manifest -- never trusted from an
    arbitrary caller-supplied value -- so a persisted replay's configuration
    identity is provably tied to this exact manifest+config, not merely labeled as
    such."""

    payload = {"exp005_config": exp005_config.canonical_dict(), "manifest": manifest.canonical_dict()}
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return canonical_json, hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def verify_real_run_preconditions(
    manifest: ExperimentManifest,
    exp005_config: Exp005Config,
    feature_snapshot_dir: str | Path,
    trading_dates: list[date],
    code_commit_sha: str | None = None,
    working_tree_is_clean_fn: Callable[[], bool] | None = None,
) -> VerifiedLineage:
    """Every check below is independent -- a caller integrating this can rely on
    the FIRST failure's message alone. Returns the freshly re-verified lineage on
    success (proof that verification actually happened, not merely that no
    exception was raised), for the caller to reuse without re-hashing.

    `code_commit_sha`/`working_tree_is_clean_fn` are injectable purely for
    testing -- production callers omit both and get the real git-backed checks.
    """

    try:
        validate_freeze(manifest)
    except FreezeValidationError as e:
        # Re-raised as RealRunGateError so a caller of this boundary only ever
        # needs to catch one exception type -- the underlying cause is chained,
        # not discarded.
        raise RealRunGateError(str(e)) from e

    actual_commit = current_code_commit_sha() if code_commit_sha is None else code_commit_sha
    if actual_commit != manifest.code_commit_sha:
        raise RealRunGateError(
            f"current code commit {actual_commit!r} does not match the manifest's "
            f"code_commit_sha {manifest.code_commit_sha!r} -- a real run must execute at exactly "
            "the commit the manifest was frozen at."
        )

    is_clean = _working_tree_is_clean() if working_tree_is_clean_fn is None else working_tree_is_clean_fn()
    if not is_clean:
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

    if exp005_config.variant_id == VARIANT_B and exp005_config.control_seed is not None:
        raise RealRunGateError("Variant B must not carry a control_seed.")
    if exp005_config.variant_id == VARIANT_D:
        if exp005_config.control_seed not in manifest.control_seed_list:
            raise RealRunGateError(
                f"control_seed {exp005_config.control_seed!r} is not in the manifest's approved "
                f"{len(manifest.control_seed_list)}-seed list."
            )

    if not trading_dates:
        raise RealRunGateError("trading_dates must be non-empty.")
    period_start, period_end = min(trading_dates), max(trading_dates)
    actual_calendar_version = compute_calendar_version(lineage.prices_df, period_start, period_end)
    if actual_calendar_version != manifest.calendar_version:
        raise RealRunGateError(
            f"the supplied trading_dates (period [{period_start.isoformat()}, {period_end.isoformat()}]) "
            f"produce calendar_version {actual_calendar_version!r}, which does not match the manifest's "
            f"recorded calendar_version {manifest.calendar_version!r}."
        )

    return lineage


def run_real_experiment(
    conn: sqlite3.Connection,
    manifest: ExperimentManifest,
    model_adapter,
    universe_provider: HistoricalFeatureUniverseProvider,
    feature_snapshot_dir: str | Path,
    exp005_config: Exp005Config,
    replay_id: str,
    replay_metadata_template: ReplayMetadata,
    trading_dates: list[date],
    sandbox_config: SandboxConfig | None = None,
    code_commit_sha: str | None = None,
    working_tree_is_clean_fn: Callable[[], bool] | None = None,
) -> ReplayRunResult:
    """The only sanctioned way to run a real EXP-005 Variant B or Variant D replay.
    Verifies every precondition (see `verify_real_run_preconditions`) BEFORE
    constructing any service, creating any replay metadata row, or writing any
    domain row -- a failed check raises RealRunGateError with nothing persisted."""

    verify_real_run_preconditions(
        manifest, exp005_config, feature_snapshot_dir, trading_dates, code_commit_sha, working_tree_is_clean_fn,
    )

    configuration_json, configuration_hash = _configuration_identity(exp005_config, manifest)
    replay_metadata = dataclasses.replace(
        replay_metadata_template, configuration_json=configuration_json, configuration_hash=configuration_hash,
    )

    market_data_provider = FrozenSwing20MarketDataProvider(feature_snapshot_dir)
    services = build_exp005_replay_services(
        conn, model_adapter, universe_provider, market_data_provider, exp005_config, replay_id,
        market_data_snapshot_id=manifest.ohlc_hash, sandbox_config=sandbox_config,
    )
    return services.replay_service.run(replay_metadata, trading_dates)
