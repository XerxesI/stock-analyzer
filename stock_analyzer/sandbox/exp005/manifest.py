"""Experiment Manifest -- Revision 5, Section 29, Stage 9 (rewritten during Stage
10 closure to fix a confirmed P1: the manifest previously computed
`feature_dataset_hash` as a raw SHA-256 of an arbitrary caller-supplied file path,
never verified against the upstream SWING_20 snapshot it claimed to come from, and
was missing `universe_hash`/`signal_hash`/`eligibility_hash`/`calendar_version`
entirely).

The single, consolidated, explicitly-named artifact recording every
reproducibility hash/identity a later reviewer needs to reconstruct, bit-for-bit,
why any single EXP-005 decision turned out the way it did. Generated ONCE per
experiment (not per variant/seed run -- Section 29's field list has no
variant_id/control_seed field; `control_seed_list` covers all 50 approved Variant D
seeds), before Stage 10's freeze-validation gate. Never hand-edited or regenerated
with different values after real Variant B results become visible (Section 12 item
9's final sentence).

Every hash field is now sourced from
`exp005.infrastructure.frozen_artifacts.verify_frozen_lineage`, which physically
re-verifies the feature snapshot's claimed relationship to its upstream SWING_20
snapshot (never trusts either manifest's own claim) -- the SAME verification
`FrozenSwing20MarketDataProvider` performs, so the manifest and the actual data a
real run reads can never silently diverge.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from stock_analyzer.sandbox.exp005.config import (
    CENSORING_REASONS,
    DEFAULT_CONTROL_SEEDS,
    NO_CAPACITY_REFERENCE_PRICE_RULE,
    Exp005Config,
)
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import verify_frozen_lineage
from stock_analyzer.sandbox.infrastructure.schema import SCHEMA_VERSION

# Section 5's market-calendar identity: the same SPY-based _outcome_only_dates
# convention already used to build EXP-004's date list -- every post-hoc horizon
# (Sections 20-24) counts sessions from THIS calendar, never raw calendar days.
MARKET_CALENDAR_IDENTITY = "SPY_TRADING_CALENDAR_OUTCOME_ONLY_DATES"

# Section 20's entry/exit-session ambiguity rule, recorded as data (not just prose)
# so the manifest is a complete, standalone specification of the rule actually
# applied to every MFE/MAE computation.
MFE_MAE_WINDOW_RULE = {
    "price_basis": "effective_entry_price",
    "entry_session_inclusion": {
        "FILLED_AT_OPEN": "included",
        "FILLED_AT_CEILING": "excluded_starts_next_session",
    },
    "exit_session_inclusion": {
        "SELL_TIME": "included",
        "SELL_TARGET_AT_OPEN": "included",
        "SELL_TARGET_INTRADAY": "excluded_beyond_realized_exit_price",
    },
}


def current_code_commit_sha(repo_root: str | Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def compute_calendar_version(prices_df: pd.DataFrame, period_start: date, period_end: date) -> str:
    """Deterministic identifier derived from the sorted, distinct session dates
    actually present in the verified frozen prices artifact, restricted to the
    registered experiment period [period_start, period_end] -- never from a
    third-party calendar library that could silently drift from what the frozen
    artifact itself contains."""

    all_dates = pd.to_datetime(prices_df["date"]).dt.date
    dates_in_period = sorted({d for d in all_dates.unique() if period_start <= d <= period_end})
    if not dates_in_period:
        raise ValueError(
            f"no session dates found in the frozen prices artifact within the registered "
            f"experiment period [{period_start.isoformat()}, {period_end.isoformat()}]."
        )
    canonical = ",".join(d.isoformat() for d in dates_in_period)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExperimentManifest:
    experiment_id: str
    code_commit_sha: str
    schema_version: int
    decision_audit_schema_version: int
    universe_hash: str
    ohlc_hash: str
    signal_hash: str
    eligibility_hash: str
    feature_hash: str
    calendar_version: str
    feature_snapshot_id: str
    swing20_snapshot_id: str
    portfolio_configuration_hash: str
    control_seed_list: tuple[int, ...]
    feasibility_criteria: dict
    diagnostic_definitions: dict
    spy_benchmark_snapshot_id: str | None
    generated_at: datetime

    def canonical_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "code_commit_sha": self.code_commit_sha,
            "schema_version": self.schema_version,
            "decision_audit_schema_version": self.decision_audit_schema_version,
            "universe_hash": self.universe_hash,
            "ohlc_hash": self.ohlc_hash,
            "signal_hash": self.signal_hash,
            "eligibility_hash": self.eligibility_hash,
            "feature_hash": self.feature_hash,
            "calendar_version": self.calendar_version,
            "feature_snapshot_id": self.feature_snapshot_id,
            "swing20_snapshot_id": self.swing20_snapshot_id,
            "portfolio_configuration_hash": self.portfolio_configuration_hash,
            "control_seed_list": list(self.control_seed_list),
            "feasibility_criteria": self.feasibility_criteria,
            "diagnostic_definitions": self.diagnostic_definitions,
            "spy_benchmark_snapshot_id": self.spy_benchmark_snapshot_id,
            "generated_at": self.generated_at.isoformat(),
        }

    @staticmethod
    def from_canonical_dict(data: dict) -> "ExperimentManifest":
        return ExperimentManifest(
            experiment_id=data["experiment_id"],
            code_commit_sha=data["code_commit_sha"],
            schema_version=data["schema_version"],
            decision_audit_schema_version=data["decision_audit_schema_version"],
            universe_hash=data["universe_hash"],
            ohlc_hash=data["ohlc_hash"],
            signal_hash=data["signal_hash"],
            eligibility_hash=data["eligibility_hash"],
            feature_hash=data["feature_hash"],
            calendar_version=data["calendar_version"],
            feature_snapshot_id=data["feature_snapshot_id"],
            swing20_snapshot_id=data["swing20_snapshot_id"],
            portfolio_configuration_hash=data["portfolio_configuration_hash"],
            control_seed_list=tuple(data["control_seed_list"]),
            feasibility_criteria=data["feasibility_criteria"],
            diagnostic_definitions=data["diagnostic_definitions"],
            spy_benchmark_snapshot_id=data["spy_benchmark_snapshot_id"],
            generated_at=datetime.fromisoformat(data["generated_at"]),
        )

    def is_complete(self) -> bool:
        """Section 13's required gate, as a pure predicate: every field must be
        present and populated before any variant executes. `spy_benchmark_
        snapshot_id` is explicitly contextual-only (Section 5/29) -- None is a
        valid, complete state for it, never treated as a missing field."""

        required_non_empty = (
            self.experiment_id,
            self.code_commit_sha,
            self.universe_hash,
            self.ohlc_hash,
            self.signal_hash,
            self.eligibility_hash,
            self.feature_hash,
            self.calendar_version,
            self.feature_snapshot_id,
            self.swing20_snapshot_id,
            self.portfolio_configuration_hash,
        )
        if any(not value for value in required_non_empty):
            return False
        if self.schema_version <= 0 or self.decision_audit_schema_version <= 0:
            return False
        if not self.control_seed_list:
            return False
        if not self.feasibility_criteria or not self.diagnostic_definitions:
            return False
        return True


def build_experiment_manifest(
    exp005_config: Exp005Config,
    feature_snapshot_dir: str | Path,
    period_start: date,
    period_end: date,
    code_commit_sha: str | None = None,
    generated_at: datetime | None = None,
) -> ExperimentManifest:
    """Every hash comes from `verify_frozen_lineage`, which physically re-verifies
    the feature snapshot's claimed relationship to its upstream SWING_20 snapshot
    -- raises `FrozenArtifactVerificationError` (not caught here) if anything
    doesn't match, rather than ever recording an unverified value."""

    lineage = verify_frozen_lineage(feature_snapshot_dir)

    diagnostic_definitions = {
        "horizons": exp005_config.diagnostic_horizons.canonical(),
        "mfe_mae_window_rule": MFE_MAE_WINDOW_RULE,
        "no_capacity_reference_price_rule": NO_CAPACITY_REFERENCE_PRICE_RULE,
        "censoring_reasons": list(CENSORING_REASONS),
        "market_calendar_identity": MARKET_CALENDAR_IDENTITY,
    }

    return ExperimentManifest(
        experiment_id=exp005_config.experiment_id,
        code_commit_sha=current_code_commit_sha() if code_commit_sha is None else code_commit_sha,
        schema_version=SCHEMA_VERSION,
        decision_audit_schema_version=exp005_config.decision_audit_schema_version,
        universe_hash=lineage.artifact_hashes["universe"],
        ohlc_hash=lineage.artifact_hashes["prices"],
        signal_hash=lineage.artifact_hashes["labels"],
        eligibility_hash=lineage.artifact_hashes["eligibility"],
        feature_hash=lineage.feature_dataset_hash,
        calendar_version=compute_calendar_version(lineage.prices_df, period_start, period_end),
        feature_snapshot_id=lineage.feature_snapshot_id,
        swing20_snapshot_id=lineage.swing20_snapshot_id,
        portfolio_configuration_hash=exp005_config.portfolio_configuration_hash(),
        control_seed_list=DEFAULT_CONTROL_SEEDS,
        feasibility_criteria=exp005_config.feasibility_criteria.canonical(),
        diagnostic_definitions=diagnostic_definitions,
        spy_benchmark_snapshot_id=exp005_config.spy_benchmark.snapshot_id,
        generated_at=datetime.now(timezone.utc) if generated_at is None else generated_at,
    )


def write_manifest_artifact(manifest: ExperimentManifest, output_path: str | Path) -> None:
    """Persists the manifest as one canonical JSON artifact -- sorted keys, fixed
    indent, so a byte-diff between two manifest files is meaningful. Never
    hand-edit the resulting file; regenerate via build_experiment_manifest
    instead."""

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest.canonical_dict(), f, sort_keys=True, indent=2)
        f.write("\n")


def read_manifest_artifact(path: str | Path) -> ExperimentManifest:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ExperimentManifest.from_canonical_dict(data)
