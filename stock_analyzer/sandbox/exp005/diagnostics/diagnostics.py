"""Mediated loading boundary for EXP-005's post-hoc decision-quality diagnostics
-- Revision 5, Sections 18-27 and Section 30, Stage 11.

Section 30's formalized contract: the entire diagnostics layer is a pure function

    Result = f(SQLite replay database, prices.parquet, Experiment Manifest)

and nothing else -- no live network access, no hidden global state, no dependency
on wall-clock time or call order, no access to any database or artifact outside
these three named inputs. This module is the ONE place those three inputs are
loaded and verified; every individual diagnostic function (Stages 12-13) receives
an already-built `DiagnosticsContext` from here, never a raw connection, file
path, or manifest object directly -- so "pure function of exactly these three
inputs" is enforced by what's structurally available to call, not merely
documented.

**Import-isolation invariant (Section 26), enforced mechanically, not just by
convention:** this package -- `stock_analyzer.sandbox.exp005.diagnostics` -- must
never be imported, directly or transitively, by `CandidateService`,
`AdmissionTransactionService`/`CapacityAdmissionOrchestrator`, `EntryService`,
`MonitoringService`, `ReplayService`, or any of the modules that wire them
together (`exp005/application/replay.py`, `real_run.py`,
`portfolio_accounting_seam.py`, `portfolio_ledger.py`). Those modules make
decisions; this package only ever reads what already happened, strictly after a
replay has completed. `tests/test_exp005_diagnostics_import_boundary.py` verifies
this by statically walking the import graph, not by code-review discipline alone.

Read-only against an ALREADY-COMPLETED replay database: nothing in this package,
or anything built on top of `DiagnosticsContext`, ever writes to
`portfolio_admissions`/`slot_reservations`/`executions`/`portfolio_equity_snapshots`
or any core sandbox table.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stock_analyzer.sandbox.domain.replay import COMPLETED
from stock_analyzer.sandbox.exp005.config import SUPPORTED_VARIANTS, VARIANT_B, VARIANT_D
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_file, verify_frozen_lineage
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.manifest import ExperimentManifest, read_manifest_artifact
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


class DiagnosticsProvenanceError(RuntimeError):
    """Raised when the supplied database/artifacts do not provably belong to the
    completed replay this diagnostics pass claims to analyze -- diagnostics must
    be computed against the EXACT same frozen data and the EXACT same completed
    run the manifest describes, never a different, unverified, or still-running
    one (Stage 11-15 closure, finding 5)."""


@dataclass(frozen=True)
class DiagnosticsContext:
    """Everything a diagnostic function needs, already loaded and physically
    verified against the manifest -- Section 30's three named inputs, and nothing
    else. `portfolio_repo`/`sandbox_repo` wrap the completed replay's own SQLite
    connection for READ-ONLY access (nothing in the diagnostics package ever
    calls an insert/update method on either).

    `variant_id`/`control_seed`/`feasibility_criteria`/`manifest_artifact_hash`/
    `configuration_hash` are NEVER caller-supplied (Stage 11-15 third closure) --
    they are derived exclusively from this replay's own verified
    `configuration_json` (the same hash-verified payload `real_run.py`'s
    `_configuration_identity` wrote), so a downstream report built from this
    context cannot be mislabeled as a different variant/seed than the one that
    actually produced it, nor scored against thresholds other than the ones
    frozen at run time."""

    manifest: ExperimentManifest
    replay_id: str
    variant_id: str
    control_seed: int | None
    manifest_artifact_hash: str
    configuration_hash: str
    feasibility_criteria: dict
    prices_df: pd.DataFrame
    portfolio_repo: PortfolioRepository
    sandbox_repo: SandboxRepository


def load_diagnostics_context(
    conn: sqlite3.Connection,
    manifest_artifact_path: str | Path,
    feature_snapshot_dir: str | Path,
    replay_id: str,
) -> DiagnosticsContext:
    """Loads and re-verifies the frozen prices artifact against
    `manifest.ohlc_hash`/`manifest.feature_snapshot_id` -- never trusts a
    caller-supplied path without re-hashing it -- AND verifies the supplied
    connection actually holds a COMPLETED replay whose own persisted provenance
    matches that same manifest (Stage 11-15 closure, finding 5). Mirrors
    `real_run.py`'s own execution boundary: the manifest is loaded fresh from its
    PERSISTED artifact file, never accepted as an in-memory object a caller could
    construct without ever freezing it, and the replay's own configuration
    identity must incorporate that artifact file's own hash. Call this ONCE per
    diagnostics pass; every individual diagnostic function takes the returned
    `DiagnosticsContext`, never `conn`/a file path directly.

    A database whose `replay_metadata` row for `replay_id` is missing, still
    `RUNNING`/`FAILED`, or whose provenance disagrees with the manifest is
    rejected -- since core sandbox tables carry no `replay_id` column (each
    replay is its own isolated database, per EXP-004's convention), this
    replay_metadata match is the strongest available proof that the supplied
    connection is genuinely the isolated database for this replay, not a
    different or mismatched one accidentally pointed at."""

    manifest = read_manifest_artifact(manifest_artifact_path)
    manifest_artifact_hash = sha256_of_file(manifest_artifact_path)

    lineage = verify_frozen_lineage(feature_snapshot_dir)
    if lineage.artifact_hashes["prices"] != manifest.ohlc_hash:
        raise DiagnosticsProvenanceError(
            f"the supplied feature snapshot's re-verified prices hash "
            f"({lineage.artifact_hashes['prices']!r}) does not match the manifest's recorded "
            f"ohlc_hash ({manifest.ohlc_hash!r})."
        )
    if lineage.feature_snapshot_id != manifest.feature_snapshot_id:
        raise DiagnosticsProvenanceError(
            f"the supplied feature snapshot ({lineage.feature_snapshot_id!r}) does not match the "
            f"manifest's recorded feature_snapshot_id ({manifest.feature_snapshot_id!r})."
        )

    replay = SandboxRepository(conn).get_replay_metadata(replay_id)
    if replay is None:
        raise DiagnosticsProvenanceError(
            f"no replay_metadata row exists for replay_id={replay_id!r} in the supplied database -- "
            "diagnostics can only be computed against an actually-completed replay."
        )
    if replay.status != COMPLETED:
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r} has status {replay.status!r}, not {COMPLETED!r} -- diagnostics "
            "can only be computed once a replay has genuinely finished."
        )

    _provenance_checks = (
        ("code_commit_sha", replay.code_commit_sha, manifest.code_commit_sha),
        ("model_version", replay.model_version, manifest.model_version),
        ("feature_snapshot_id", replay.feature_snapshot_id, manifest.feature_snapshot_id),
        ("market_data_snapshot_id", replay.market_data_snapshot_id, manifest.ohlc_hash),
        ("signal_start_date", replay.signal_start_date, manifest.signal_start_date),
        ("signal_end_date", replay.signal_end_date, manifest.signal_end_date),
        ("outcome_data_end_date", replay.outcome_data_end_date, manifest.outcome_data_end_date),
    )
    for field_name, actual, expected in _provenance_checks:
        if actual != expected:
            raise DiagnosticsProvenanceError(
                f"replay {replay_id!r}'s persisted {field_name} ({actual!r}) does not match the "
                f"manifest's recorded {field_name} ({expected!r})."
            )

    try:
        configuration = json.loads(replay.configuration_json)
    except (TypeError, ValueError) as e:
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r}'s persisted configuration_json is not valid JSON: {e}."
        ) from e

    # Self-consistency (Stage 11-15 second closure, finding 6): configuration_hash
    # is `real_run.py`'s own `_configuration_identity` -- sha256 of the EXACT
    # persisted configuration_json string, verbatim, using the SAME canonical
    # serialization already baked into that string (never re-serialized here,
    # which could silently accept a semantically-identical but differently-
    # formatted tampering of the JSON text). Recomputing and requiring equality
    # catches EITHER field being edited independently of the other after the
    # fact -- not just the narrower manifest_artifact_hash check below.
    recomputed_configuration_hash = hashlib.sha256(replay.configuration_json.encode("utf-8")).hexdigest()
    if recomputed_configuration_hash != replay.configuration_hash:
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r}'s persisted configuration_hash ({replay.configuration_hash!r}) does "
            f"not match sha256(configuration_json) recomputed just now "
            f"({recomputed_configuration_hash!r}) -- configuration_json and/or configuration_hash was "
            "modified independently after the replay was written."
        )

    actual_manifest_artifact_hash = configuration.get("manifest_artifact_hash")
    if actual_manifest_artifact_hash != manifest_artifact_hash:
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r}'s persisted configuration_json records manifest_artifact_hash "
            f"{actual_manifest_artifact_hash!r}, which does not match the SUPPLIED manifest "
            f"artifact file's actual hash ({manifest_artifact_hash!r}) -- this replay was not "
            "provably run against this exact manifest artifact."
        )

    # Variant/seed/feasibility identity (Stage 11-15 third closure): derived
    # EXCLUSIVELY from this already hash-verified configuration_json, never
    # accepted as a parameter downstream -- so a Variant D run's report can
    # never be relabeled Variant B (or assigned a different seed) by a caller
    # after the fact, and the feasibility thresholds a report is later judged
    # against can never be substituted for a different dict post-hoc.
    exp005_config_payload = configuration.get("exp005_config")
    if not isinstance(exp005_config_payload, dict):
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r}'s persisted configuration_json has no 'exp005_config' object -- "
            "this replay's variant/seed identity and feasibility criteria can only be trusted from "
            "the verified configuration, never supplied separately by a caller."
        )
    variant_id = exp005_config_payload.get("variant_id")
    control_seed = exp005_config_payload.get("control_seed")
    if variant_id not in SUPPORTED_VARIANTS:
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r}'s persisted configuration_json records variant_id {variant_id!r}, "
            f"which is not one of the approved variants {SUPPORTED_VARIANTS!r}."
        )
    if variant_id == VARIANT_B and control_seed is not None:
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r}'s persisted configuration_json records variant_id={VARIANT_B!r} "
            f"together with a control_seed ({control_seed!r}) -- Variant B must never carry one."
        )
    if variant_id == VARIANT_D and control_seed is None:
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r}'s persisted configuration_json records variant_id={VARIANT_D!r} "
            "with no control_seed -- every Variant D run requires one."
        )
    feasibility_criteria = exp005_config_payload.get("feasibility_criteria")
    if not isinstance(feasibility_criteria, dict) or not feasibility_criteria:
        raise DiagnosticsProvenanceError(
            f"replay {replay_id!r}'s persisted configuration_json has no usable feasibility_criteria."
        )

    # Cross-check every execution this replay actually produced against that
    # same config-derived identity -- a disagreement (e.g. a database whose
    # own executions were written under a different variant/seed than its
    # configuration claims) fails closed rather than silently trusting
    # whichever source a caller happened to read first. A replay with ZERO
    # executions is not a special case: its identity still comes from the
    # verified configuration above, never from (necessarily absent) execution
    # rows -- there is simply nothing to cross-check in that case.
    portfolio_repo = PortfolioRepository(conn)
    for execution in portfolio_repo.list_executions_for_experiment(replay_id):
        if execution.variant_id != variant_id or execution.control_seed != control_seed:
            raise DiagnosticsProvenanceError(
                f"execution {execution.execution_id!r} in replay {replay_id!r} carries "
                f"variant_id={execution.variant_id!r}/control_seed={execution.control_seed!r}, which "
                f"does not match this replay's own configuration-derived identity "
                f"(variant_id={variant_id!r}, control_seed={control_seed!r}) -- the database's "
                "execution rows disagree with its own persisted configuration."
            )

    return DiagnosticsContext(
        manifest=manifest,
        replay_id=replay_id,
        variant_id=variant_id,
        control_seed=control_seed,
        manifest_artifact_hash=manifest_artifact_hash,
        configuration_hash=replay.configuration_hash,
        feasibility_criteria=feasibility_criteria,
        prices_df=lineage.prices_df,
        portfolio_repo=portfolio_repo,
        sandbox_repo=SandboxRepository(conn),
    )
