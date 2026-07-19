"""Typed, frozen configuration for EXP-005 (Revision 5,
docs/09_experiments/EXP-005_Portfolio_Policy_Feasibility_Pilot.md). Every default
value here traces back to a specific frozen decision in that document -- this module
adds typed structure and canonical hashing, never a new policy choice. If a value
here needs to change, the frozen document changes first.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

VARIANT_B = "B"
VARIANT_D = "D"
SUPPORTED_VARIANTS = (VARIANT_B, VARIANT_D)

# Decimal precision used when building the canonical (hashable) representation of
# any float field -- chosen so two configurations that are equal in value, however
# constructed, always serialize byte-identically (Stage 1 requirement). Money and
# rate fields use different precision because a rate (e.g. 0.0005) needs more decimal
# places than a dollar amount to round-trip without loss.
MONEY_DECIMALS = 2
RATE_DECIMALS = 6

# Section 28/29 -- version marker for the four wholly new, additive tables
# (portfolio_admissions, slot_reservations, portfolio_equity_snapshots, executions).
# No migration path is required for it (see schema.py's own docstring once Stage 2
# lands) -- this exists purely so the Experiment Manifest can record which shape of
# those tables a given run used, for future-cycle reference.
DECISION_AUDIT_SCHEMA_VERSION = 1

# Section 3 -- the 50 fixed, pre-registered Variant D control seeds.
DEFAULT_CONTROL_SEEDS: tuple[int, ...] = tuple(range(1, 51))

# Section 24 -- the NO_CAPACITY hypothetical-fill reference-price rule, recorded
# verbatim (as data, not just as this constant) in the Experiment Manifest's
# diagnostic_definitions field.
NO_CAPACITY_REFERENCE_PRICE_RULE = "max_entry_price_at_rejection"

# Section 27 -- censoring reasons. NONE means the horizon was fully observed.
CENSOR_NONE = "NONE"
CENSOR_END_OF_EXPERIMENT = "END_OF_EXPERIMENT"
CENSOR_MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
CENSORING_REASONS = (CENSOR_NONE, CENSOR_END_OF_EXPERIMENT, CENSOR_MISSING_MARKET_DATA)


class UnsupportedVariantError(ValueError):
    """Raised when a variant_id outside {VARIANT_B, VARIANT_D} is requested.
    Revision 5 approves exactly two variants (Section 3) -- fail fast rather than
    silently accept anything else."""


def _round_money(value: float) -> float:
    return round(float(value), MONEY_DECIMALS)


def _round_rate(value: float) -> float:
    return round(float(value), RATE_DECIMALS)


def canonical_json(obj: dict) -> str:
    """The one canonical serialization used everywhere a hash is taken in EXP-005
    (config hashes here, the Experiment Manifest in Stage 9): sorted keys, no
    incidental whitespace, ASCII-only, so the same logical content always produces
    the same bytes regardless of dict construction order or platform."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_of(obj: dict) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PortfolioConfig:
    """Section 4's fixed portfolio assumptions, plus Section 8.3's slot-budget
    definition and Section 9's transaction costs."""

    starting_capital: float = 100_000.0
    max_slots: int = 10
    slot_budget: float = 10_000.0  # total entry cash budget per slot, Section 8.3
    entry_commission: float = 1.0
    exit_commission: float = 1.0
    slippage_rate: float = 0.0005  # 5 bps, both sides, Section 9

    def __post_init__(self) -> None:
        if self.max_slots <= 0:
            raise ValueError("max_slots must be positive")
        if self.slot_budget * self.max_slots > self.starting_capital + 1e-6:
            raise ValueError(
                f"slot_budget * max_slots ({self.slot_budget * self.max_slots}) "
                f"exceeds starting_capital ({self.starting_capital})"
            )

    def canonical(self) -> dict:
        return {
            "starting_capital": _round_money(self.starting_capital),
            "max_slots": self.max_slots,
            "slot_budget": _round_money(self.slot_budget),
            "entry_commission": _round_money(self.entry_commission),
            "exit_commission": _round_money(self.exit_commission),
            "slippage_rate": _round_rate(self.slippage_rate),
        }


@dataclass(frozen=True)
class AdmissionRules:
    """Section 8.4's deterministic ordering rule for scarce-capacity admission."""

    tie_break: str = "symbol_ascending"  # the only value this cycle supports

    def canonical(self) -> dict:
        return {"tie_break": self.tie_break}


@dataclass(frozen=True)
class DiagnosticHorizons:
    """Section 12/Stage 9's frozen horizon lists (Sections 21-24)."""

    post_exit_sessions: tuple[int, ...] = (1, 5, 10, 20)
    entry_timing_sessions: tuple[int, ...] = (1, 5, 10, 20)
    no_capacity_sessions: tuple[int, ...] = (1, 5, 10, 20)
    hold_sessions: tuple[int, ...] = (1, 5, 10)

    def canonical(self) -> dict:
        return {
            "post_exit_sessions": list(self.post_exit_sessions),
            "entry_timing_sessions": list(self.entry_timing_sessions),
            "no_capacity_sessions": list(self.no_capacity_sessions),
            "hold_sessions": list(self.hold_sessions),
        }


@dataclass(frozen=True)
class FeasibilityCriteria:
    """Section 10's exact, frozen feasibility thresholds -- data, not prose."""

    max_drawdown_threshold: float = 0.20
    largest_win_pct_of_net_profit_threshold: float = 0.50
    control_percentile_threshold: float = 80.0
    min_profit_factor: float = 1.0

    def canonical(self) -> dict:
        return {
            "max_drawdown_threshold": _round_rate(self.max_drawdown_threshold),
            "largest_win_pct_of_net_profit_threshold": _round_rate(
                self.largest_win_pct_of_net_profit_threshold
            ),
            "control_percentile_threshold": _round_rate(self.control_percentile_threshold),
            "min_profit_factor": _round_rate(self.min_profit_factor),
        }


@dataclass(frozen=True)
class SpyBenchmarkIdentity:
    """Section 5/29 -- the one-time frozen SPY snapshot's provenance. All fields are
    None until that snapshot is actually pulled; a None identity means "not yet
    available," never a silent default value. Contextual only -- never gates the
    primary Variant B vs. D comparison (Section 5)."""

    snapshot_id: str | None = None
    source: str | None = None
    retrieved_at: str | None = None
    date_range_start: str | None = None
    date_range_end: str | None = None
    raw_file_hash: str | None = None
    normalized_file_hash: str | None = None

    def canonical(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "retrieved_at": self.retrieved_at,
            "date_range_start": self.date_range_start,
            "date_range_end": self.date_range_end,
            "raw_file_hash": self.raw_file_hash,
            "normalized_file_hash": self.normalized_file_hash,
        }


@dataclass(frozen=True)
class Exp005Config:
    """Top-level, frozen EXP-005 run configuration. One instance identifies exactly
    one variant/seed run -- Variant B has control_seed=None; each Variant D run has
    its own fixed seed from DEFAULT_CONTROL_SEEDS."""

    experiment_id: str = "EXP-005"
    variant_id: str = VARIANT_B
    control_seed: int | None = None
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    admission_rules: AdmissionRules = field(default_factory=AdmissionRules)
    diagnostic_horizons: DiagnosticHorizons = field(default_factory=DiagnosticHorizons)
    feasibility_criteria: FeasibilityCriteria = field(default_factory=FeasibilityCriteria)
    decision_audit_schema_version: int = DECISION_AUDIT_SCHEMA_VERSION
    spy_benchmark: SpyBenchmarkIdentity = field(default_factory=SpyBenchmarkIdentity)

    def __post_init__(self) -> None:
        if self.variant_id not in SUPPORTED_VARIANTS:
            raise UnsupportedVariantError(
                f"variant_id={self.variant_id!r} is not supported -- EXP-005 Revision 5 "
                f"approves exactly {SUPPORTED_VARIANTS} (Section 3). No stop-loss, "
                "ADD, or REDUCE variant exists this cycle."
            )
        if self.variant_id == VARIANT_B and self.control_seed is not None:
            raise ValueError("Variant B must not carry a control_seed -- that is Variant D-only.")
        if self.variant_id == VARIANT_D and self.control_seed is None:
            raise ValueError("Variant D requires a control_seed (Section 3's 50 fixed seeds).")

    def canonical_dict(self) -> dict:
        """The full run identity, sorted and precision-stable -- used for
        `config_hash()`. Deliberately excludes `spy_benchmark` (contextual
        provenance, not policy -- see Section 5) so a SPY snapshot arriving later
        does not change the identity of an already-frozen policy configuration."""

        return {
            "experiment_id": self.experiment_id,
            "variant_id": self.variant_id,
            "control_seed": self.control_seed,
            "portfolio": self.portfolio.canonical(),
            "admission_rules": self.admission_rules.canonical(),
            "diagnostic_horizons": self.diagnostic_horizons.canonical(),
            "feasibility_criteria": self.feasibility_criteria.canonical(),
            "decision_audit_schema_version": self.decision_audit_schema_version,
        }

    def config_hash(self) -> str:
        """Identity hash of the FULL run configuration. Two independently
        constructed `Exp005Config` instances with equal field values always produce
        the same hash (tested directly, Stage 1)."""

        return _sha256_of(self.canonical_dict())

    def portfolio_configuration_hash(self) -> str:
        """The narrower hash the Experiment Manifest (Section 29) records as
        `portfolio_configuration_hash`: capital, slot count, per-slot budget,
        commission, slippage rate, and the admission tie-break rule only --
        deliberately excludes `feasibility_criteria`/`diagnostic_horizons`, which
        the manifest records under its own separate `feasibility_criteria`/
        `diagnostic_definitions` fields instead."""

        return _sha256_of(
            {"portfolio": self.portfolio.canonical(), "admission_rules": self.admission_rules.canonical()}
        )
