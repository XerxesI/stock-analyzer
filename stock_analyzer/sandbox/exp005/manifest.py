"""Experiment Manifest -- Revision 5, Section 29, Stage 9.

The single, consolidated, explicitly-named artifact recording every
reproducibility hash/identity a later reviewer needs to reconstruct, bit-for-bit,
why any single EXP-005 decision turned out the way it did -- rather than
reassembling that provenance from several scattered sections. Generated ONCE per
experiment (not per variant/seed run -- Section 29's field list has no
variant_id/control_seed field; `control_seed_list` covers all 50 approved Variant D
seeds), before Stage 10's freeze-validation gate. Never hand-edited or regenerated
with different values after real Variant B results become visible (Section 12 item
9's final sentence).

Every value here either already exists as a frozen decision in `exp005/config.py`
(Stage 1) or is computed once from a named frozen-artifact file path / the current
git commit -- this module never independently re-derives or re-interprets a
threshold/definition; it only assembles what is already frozen elsewhere.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from stock_analyzer.sandbox.exp005.config import (
    CENSORING_REASONS,
    DEFAULT_CONTROL_SEEDS,
    NO_CAPACITY_REFERENCE_PRICE_RULE,
    Exp005Config,
)
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


class MissingArtifactHashError(RuntimeError):
    """Raised when a required frozen-artifact file cannot be found/hashed at
    manifest-generation time -- never silently recorded as an empty or placeholder
    hash."""


def sha256_of_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_or_raise(path: str, label: str) -> str:
    try:
        return sha256_of_file(path)
    except OSError as e:
        raise MissingArtifactHashError(f"cannot hash {label} at {path!r}: {e}") from e


def current_code_commit_sha(repo_root: str | Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class ExperimentManifest:
    experiment_id: str
    code_commit_sha: str
    schema_version: int
    decision_audit_schema_version: int
    feature_dataset_hash: str
    ohlcv_hash: str
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
            "feature_dataset_hash": self.feature_dataset_hash,
            "ohlcv_hash": self.ohlcv_hash,
            "portfolio_configuration_hash": self.portfolio_configuration_hash,
            "control_seed_list": list(self.control_seed_list),
            "feasibility_criteria": self.feasibility_criteria,
            "diagnostic_definitions": self.diagnostic_definitions,
            "spy_benchmark_snapshot_id": self.spy_benchmark_snapshot_id,
            "generated_at": self.generated_at.isoformat(),
        }

    def is_complete(self) -> bool:
        """Section 13's required gate, as a pure predicate: every field must be
        present and populated before any variant executes. `spy_benchmark_
        snapshot_id` is explicitly contextual-only (Section 5/29) -- None is a
        valid, complete state for it, never treated as a missing field. Stage 10's
        freeze_validation.py is what actually raises on an incomplete manifest;
        this method only reports the fact."""

        required_non_empty = (
            self.experiment_id,
            self.code_commit_sha,
            self.feature_dataset_hash,
            self.ohlcv_hash,
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
    feature_dataset_path: str,
    ohlcv_path: str,
    code_commit_sha: str | None = None,
    generated_at: datetime | None = None,
) -> ExperimentManifest:
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
        feature_dataset_hash=_hash_or_raise(feature_dataset_path, "feature_dataset"),
        ohlcv_hash=_hash_or_raise(ohlcv_path, "ohlcv"),
        portfolio_configuration_hash=exp005_config.portfolio_configuration_hash(),
        control_seed_list=DEFAULT_CONTROL_SEEDS,
        feasibility_criteria=exp005_config.feasibility_criteria.canonical(),
        diagnostic_definitions=diagnostic_definitions,
        spy_benchmark_snapshot_id=exp005_config.spy_benchmark.snapshot_id,
        generated_at=datetime.now(timezone.utc) if generated_at is None else generated_at,
    )
