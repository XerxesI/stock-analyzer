"""Tests for EXP-005's typed configuration and canonical hashing (Revision 5,
Stage 1 -- docs/09_experiments/EXP-005_Portfolio_Policy_Feasibility_Pilot.md,
docs/09_experiments/EXP-005_Implementation_Checklist.md).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from stock_analyzer.sandbox.exp005.config import (
    DEFAULT_CONTROL_SEEDS,
    VARIANT_B,
    VARIANT_D,
    AdmissionRules,
    DiagnosticHorizons,
    Exp005Config,
    FeasibilityCriteria,
    PortfolioConfig,
    SpyBenchmarkIdentity,
    UnsupportedVariantError,
    canonical_json,
)


def test_default_control_seeds_are_50_fixed_values():
    assert len(DEFAULT_CONTROL_SEEDS) == 50
    assert DEFAULT_CONTROL_SEEDS == tuple(range(1, 51))


def test_equivalent_configs_produce_byte_identical_canonical_json():
    a = Exp005Config(variant_id=VARIANT_B)
    b = Exp005Config(
        variant_id=VARIANT_B,
        portfolio=PortfolioConfig(),
        admission_rules=AdmissionRules(),
        diagnostic_horizons=DiagnosticHorizons(),
        feasibility_criteria=FeasibilityCriteria(),
    )
    assert canonical_json(a.canonical_dict()) == canonical_json(b.canonical_dict())
    assert a.config_hash() == b.config_hash()
    assert a.portfolio_configuration_hash() == b.portfolio_configuration_hash()


def test_canonical_json_is_independent_of_input_key_order():
    d1 = {"b": 2, "a": 1, "c": {"z": 1, "y": 2}}
    d2 = {"a": 1, "c": {"y": 2, "z": 1}, "b": 2}
    assert canonical_json(d1) == canonical_json(d2)


def test_float_representation_is_stable_at_defined_precision():
    # Values that are numerically equal once rounded to the frozen precision must
    # produce identical canonical output, even if constructed via slightly different
    # float literals.
    a = PortfolioConfig(slippage_rate=0.0005)
    b = PortfolioConfig(slippage_rate=0.00050000001)
    assert a.canonical() == b.canonical()


def test_different_slippage_rate_changes_config_hash():
    a = Exp005Config(variant_id=VARIANT_B)
    b = Exp005Config(variant_id=VARIANT_B, portfolio=replace(PortfolioConfig(), slippage_rate=0.001))
    assert a.config_hash() != b.config_hash()
    assert a.portfolio_configuration_hash() != b.portfolio_configuration_hash()


def test_portfolio_configuration_hash_excludes_feasibility_and_horizons():
    a = Exp005Config(variant_id=VARIANT_B)
    b = Exp005Config(
        variant_id=VARIANT_B,
        feasibility_criteria=FeasibilityCriteria(max_drawdown_threshold=0.5),
        diagnostic_horizons=DiagnosticHorizons(hold_sessions=(1, 2, 3)),
    )
    # The full config hash changes (feasibility/horizons are part of run identity)...
    assert a.config_hash() != b.config_hash()
    # ...but the narrower portfolio-configuration hash (Section 29's manifest field)
    # does not, since neither field belongs to it.
    assert a.portfolio_configuration_hash() == b.portfolio_configuration_hash()


def test_spy_benchmark_identity_excluded_from_config_hash():
    a = Exp005Config(variant_id=VARIANT_B)
    b = Exp005Config(
        variant_id=VARIANT_B,
        spy_benchmark=SpyBenchmarkIdentity(snapshot_id="spy-2026-07-19", raw_file_hash="abc123"),
    )
    assert a.config_hash() == b.config_hash()


def test_unsupported_variant_fails_fast():
    with pytest.raises(UnsupportedVariantError):
        Exp005Config(variant_id="C")
    with pytest.raises(UnsupportedVariantError):
        Exp005Config(variant_id="ADD")


def test_variant_b_must_not_carry_a_control_seed():
    with pytest.raises(ValueError, match="must not carry a control_seed"):
        Exp005Config(variant_id=VARIANT_B, control_seed=1)


def test_variant_d_requires_a_control_seed():
    with pytest.raises(ValueError, match="requires a control_seed"):
        Exp005Config(variant_id=VARIANT_D)


def test_variant_d_with_seed_is_valid_and_distinguishable_by_seed():
    d1 = Exp005Config(variant_id=VARIANT_D, control_seed=1)
    d2 = Exp005Config(variant_id=VARIANT_D, control_seed=2)
    assert d1.config_hash() != d2.config_hash()


def test_portfolio_config_rejects_overallocated_slots():
    with pytest.raises(ValueError, match="exceeds starting_capital"):
        PortfolioConfig(starting_capital=50_000.0, max_slots=10, slot_budget=10_000.0)


def test_portfolio_config_rejects_non_positive_max_slots():
    with pytest.raises(ValueError, match="max_slots must be positive"):
        PortfolioConfig(max_slots=0)


def test_frozen_defaults_match_section_4_and_9():
    config = PortfolioConfig()
    assert config.starting_capital == 100_000.0
    assert config.max_slots == 10
    assert config.slot_budget == 10_000.0
    assert config.entry_commission == 1.0
    assert config.exit_commission == 1.0
    assert config.slippage_rate == 0.0005


def test_frozen_feasibility_criteria_match_section_10():
    criteria = FeasibilityCriteria()
    assert criteria.max_drawdown_threshold == 0.20
    assert criteria.largest_win_pct_of_net_profit_threshold == 0.50
    assert criteria.control_percentile_threshold == 80.0
    assert criteria.min_profit_factor == 1.0


def test_frozen_diagnostic_horizons_match_sections_21_24():
    horizons = DiagnosticHorizons()
    assert horizons.post_exit_sessions == (1, 5, 10, 20)
    assert horizons.entry_timing_sessions == (1, 5, 10, 20)
    assert horizons.no_capacity_sessions == (1, 5, 10, 20)
    assert horizons.hold_sessions == (1, 5, 10)
