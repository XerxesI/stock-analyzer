"""Experiment Manifest -- Revision 5, Section 29, Stage 9 (rewritten twice during
Stage 10 closure).

First rewrite fixed a confirmed P1: `feature_dataset_hash` was a raw SHA-256 of an
arbitrary caller-supplied file path, never verified against the upstream SWING_20
snapshot it claimed to come from. Fields now come from
`exp005.infrastructure.frozen_artifacts.verify_frozen_lineage`, which physically
re-verifies that relationship.

Second rewrite fixed a confirmed P1: `calendar_version` was derived from
`min(trading_dates)`/`max(trading_dates)` alone, which let a caller silently omit
an internal trading session while keeping the same endpoints. The manifest now
freezes the exact experiment PERIOD (`signal_start_date`/`signal_end_date`/
`outcome_data_end_date`, matching `ReplayMetadata`'s own three-date model) plus a
session COUNT, and `real_run.py`'s gate independently recomputes the full ordered
date sequence from the re-verified frozen prices artifact and requires the
caller's `trading_dates` to equal it element-for-element -- never merely a hash of
the endpoints.

**Post-freeze clarification on calendar identity** (documented here rather than as
a silent rename): `market_calendar_identity` was previously labeled
"SPY_TRADING_CALENDAR_OUTCOME_ONLY_DATES", implying a separately-sourced SPY
series, while `calendar_version` was actually always computed from the frozen
SWING_20 prices artifact's own observed session dates (the union of every
symbol's trading days in that snapshot). In practice these coincide for the same
period -- any liquid US-equity universe trades on the same sessions the broader
market does -- but the manifest must describe what is ACTUALLY verified and
hashed, not a different, unverified source. The identity is now named
`FROZEN_SWING20_PRICES_SESSION_CALENDAR` to say exactly that.

Every diagnostic-definition value is built by ONE function,
`build_canonical_diagnostic_definitions`, used by both this module's manifest
builder and `real_run.py`'s gate -- so the two can never independently drift and
disagree about what "frozen" means.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from stock_analyzer.sandbox.config import MODEL_VERSION
from stock_analyzer.sandbox.exp005.config import (
    CENSORING_REASONS,
    DEFAULT_CONTROL_SEEDS,
    NO_CAPACITY_REFERENCE_PRICE_RULE,
    Exp005Config,
)
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import verify_frozen_lineage
from stock_analyzer.sandbox.infrastructure.schema import SCHEMA_VERSION

# See module docstring's "Post-freeze clarification on calendar identity."
MARKET_CALENDAR_IDENTITY = "FROZEN_SWING20_PRICES_SESSION_CALENDAR"

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


def build_canonical_diagnostic_definitions(exp005_config: Exp005Config) -> dict:
    """The ONE place diagnostic_definitions is assembled -- both the manifest
    builder (below) and real_run.py's exact-equality gate call this SAME
    function, so they can never independently drift."""

    return {
        "horizons": exp005_config.diagnostic_horizons.canonical(),
        "mfe_mae_window_rule": MFE_MAE_WINDOW_RULE,
        "no_capacity_reference_price_rule": NO_CAPACITY_REFERENCE_PRICE_RULE,
        "censoring_reasons": list(CENSORING_REASONS),
        "market_calendar_identity": MARKET_CALENDAR_IDENTITY,
    }


def current_code_commit_sha(repo_root: str | Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def compute_frozen_calendar(
    prices_df: pd.DataFrame, period_start: date, period_end: date
) -> tuple[tuple[date, ...], str]:
    """Returns (the full sorted, distinct, deduplicated session-date sequence
    actually present in the frozen prices artifact within [period_start,
    period_end], its deterministic version hash). The exact sequence -- not just
    its endpoints or count -- is what a real run's trading_dates must equal,
    element for element (real_run.py); this function is the single place that
    sequence is derived from the frozen artifact, called both when freezing the
    manifest and when the gate re-verifies a real run against it."""

    all_dates = pd.to_datetime(prices_df["date"]).dt.date
    dates_in_period = tuple(sorted({d for d in all_dates.unique() if period_start <= d <= period_end}))
    if not dates_in_period:
        raise ValueError(
            f"no session dates found in the frozen prices artifact within the registered "
            f"experiment period [{period_start.isoformat()}, {period_end.isoformat()}]."
        )
    canonical = ",".join(d.isoformat() for d in dates_in_period)
    version = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return dates_in_period, version


@dataclass(frozen=True)
class ExperimentManifest:
    experiment_id: str
    code_commit_sha: str
    schema_version: int
    decision_audit_schema_version: int
    model_version: str
    universe_hash: str
    ohlc_hash: str
    signal_hash: str
    eligibility_hash: str
    feature_hash: str
    feature_snapshot_id: str
    swing20_snapshot_id: str
    signal_start_date: date
    signal_end_date: date
    outcome_data_end_date: date
    calendar_session_count: int
    calendar_version: str
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
            "model_version": self.model_version,
            "universe_hash": self.universe_hash,
            "ohlc_hash": self.ohlc_hash,
            "signal_hash": self.signal_hash,
            "eligibility_hash": self.eligibility_hash,
            "feature_hash": self.feature_hash,
            "feature_snapshot_id": self.feature_snapshot_id,
            "swing20_snapshot_id": self.swing20_snapshot_id,
            "signal_start_date": self.signal_start_date.isoformat(),
            "signal_end_date": self.signal_end_date.isoformat(),
            "outcome_data_end_date": self.outcome_data_end_date.isoformat(),
            "calendar_session_count": self.calendar_session_count,
            "calendar_version": self.calendar_version,
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
            model_version=data["model_version"],
            universe_hash=data["universe_hash"],
            ohlc_hash=data["ohlc_hash"],
            signal_hash=data["signal_hash"],
            eligibility_hash=data["eligibility_hash"],
            feature_hash=data["feature_hash"],
            feature_snapshot_id=data["feature_snapshot_id"],
            swing20_snapshot_id=data["swing20_snapshot_id"],
            signal_start_date=date.fromisoformat(data["signal_start_date"]),
            signal_end_date=date.fromisoformat(data["signal_end_date"]),
            outcome_data_end_date=date.fromisoformat(data["outcome_data_end_date"]),
            calendar_session_count=data["calendar_session_count"],
            calendar_version=data["calendar_version"],
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
            self.model_version,
            self.universe_hash,
            self.ohlc_hash,
            self.signal_hash,
            self.eligibility_hash,
            self.feature_hash,
            self.feature_snapshot_id,
            self.swing20_snapshot_id,
            self.calendar_version,
            self.portfolio_configuration_hash,
        )
        if any(not value for value in required_non_empty):
            return False
        if self.schema_version <= 0 or self.decision_audit_schema_version <= 0:
            return False
        if self.calendar_session_count <= 0:
            return False
        if not self.control_seed_list:
            return False
        if not self.feasibility_criteria or not self.diagnostic_definitions:
            return False
        return True


def build_experiment_manifest(
    exp005_config: Exp005Config,
    feature_snapshot_dir: str | Path,
    signal_start_date: date,
    signal_end_date: date,
    outcome_data_end_date: date,
    code_commit_sha: str | None = None,
    generated_at: datetime | None = None,
) -> ExperimentManifest:
    """Every hash comes from `verify_frozen_lineage`, which physically re-verifies
    the feature snapshot's claimed relationship to its upstream SWING_20 snapshot
    -- raises `FrozenArtifactVerificationError` (not caught here) if anything
    doesn't match. The frozen calendar spans [signal_start_date,
    outcome_data_end_date] -- the full range a real replay processes, not just
    the signal-generation sub-window."""

    lineage = verify_frozen_lineage(feature_snapshot_dir)
    calendar_dates, calendar_version = compute_frozen_calendar(
        lineage.prices_df, signal_start_date, outcome_data_end_date
    )

    return ExperimentManifest(
        experiment_id=exp005_config.experiment_id,
        code_commit_sha=current_code_commit_sha() if code_commit_sha is None else code_commit_sha,
        schema_version=SCHEMA_VERSION,
        decision_audit_schema_version=exp005_config.decision_audit_schema_version,
        model_version=MODEL_VERSION,
        universe_hash=lineage.artifact_hashes["universe"],
        ohlc_hash=lineage.artifact_hashes["prices"],
        signal_hash=lineage.artifact_hashes["labels"],
        eligibility_hash=lineage.artifact_hashes["eligibility"],
        feature_hash=lineage.feature_dataset_hash,
        feature_snapshot_id=lineage.feature_snapshot_id,
        swing20_snapshot_id=lineage.swing20_snapshot_id,
        signal_start_date=signal_start_date,
        signal_end_date=signal_end_date,
        outcome_data_end_date=outcome_data_end_date,
        calendar_session_count=len(calendar_dates),
        calendar_version=calendar_version,
        portfolio_configuration_hash=exp005_config.portfolio_configuration_hash(),
        control_seed_list=DEFAULT_CONTROL_SEEDS,
        feasibility_criteria=exp005_config.feasibility_criteria.canonical(),
        diagnostic_definitions=build_canonical_diagnostic_definitions(exp005_config),
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
