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


def test_canonical_serialization_is_exact_not_rounded():
    # A prior implementation pre-rounded floats to a fixed number of decimal places
    # before hashing -- lossy, and unsound in principle even though these specific
    # values happened not to collide under that scheme. The canonical form must be
    # an exact decimal string, so two values this close together remain
    # distinguishable, however small the difference.
    a = PortfolioConfig(slippage_rate=0.0005)
    b = PortfolioConfig(slippage_rate=0.00050000001)
    assert a.canonical()["slippage_rate"] != b.canonical()["slippage_rate"]
    assert a.canonical() != b.canonical()


def test_materially_different_rates_serialize_differently():
    a = PortfolioConfig(slippage_rate=0.001)
    b = PortfolioConfig(slippage_rate=0.0014)
    assert a.canonical()["slippage_rate"] != b.canonical()["slippage_rate"]


def test_semantically_identical_values_serialize_identically_regardless_of_literal():
    # 0.0005 and 5e-4 are the exact same float value, written two different ways in
    # source code -- their canonical form must be byte-identical.
    a = PortfolioConfig(slippage_rate=0.0005)
    b = PortfolioConfig(slippage_rate=5e-4)
    assert a.canonical() == b.canonical()
    assert a.canonical()["slippage_rate"] == "0.0005"


def test_canonical_decimal_strings_are_exact_not_rounded_json_numbers():
    config = PortfolioConfig()
    canonical = config.canonical()
    # Every float-derived field must serialize as a decimal STRING (never a bare
    # JSON number, which would route back through float re-parsing ambiguity).
    for key in ("starting_capital", "slot_budget", "entry_commission", "exit_commission", "slippage_rate"):
        assert isinstance(canonical[key], str)


def test_nan_and_infinity_are_rejected():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            PortfolioConfig(slippage_rate=bad)
        with pytest.raises(ValueError, match="finite"):
            FeasibilityCriteria(max_drawdown_threshold=bad)


def test_negative_zero_does_not_create_a_separate_identity():
    a = PortfolioConfig(slippage_rate=0.0)
    b = PortfolioConfig(slippage_rate=-0.0)
    assert a.canonical() == b.canonical()
    assert a.canonical()["slippage_rate"] == "0.0" == b.canonical()["slippage_rate"]


def test_locale_does_not_affect_serialization():
    import locale

    config = PortfolioConfig(slippage_rate=0.0005, starting_capital=100_000.0)
    baseline = canonical_json(config.canonical())
    original = locale.setlocale(locale.LC_ALL)
    try:
        # Some locales (e.g. de_DE) use ',' as the decimal separator in %f-style
        # formatting -- str(float)/Decimal(str(...)) must not be affected by this at
        # all, since neither consults locale. Skip gracefully if the locale isn't
        # installed on this machine rather than failing on an environment gap.
        for candidate in ("de_DE.UTF-8", "de_DE", "German_Germany.1252"):
            try:
                locale.setlocale(locale.LC_ALL, candidate)
                break
            except locale.Error:
                continue
        else:
            pytest.skip("no alternate decimal-comma locale available on this machine")
        assert canonical_json(config.canonical()) == baseline
    finally:
        locale.setlocale(locale.LC_ALL, original)


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


def test_variant_b_and_variant_d_have_different_config_identity():
    b = Exp005Config(variant_id=VARIANT_B)
    d = Exp005Config(variant_id=VARIANT_D, control_seed=1)
    assert b.config_hash() != d.config_hash()
    # Both use the same default portfolio mechanics -- Section 3: D differs from B
    # ONLY in ranking, never in portfolio/admission/execution config.
    assert b.portfolio_configuration_hash() == d.portfolio_configuration_hash()


def test_control_seed_changes_config_hash_but_not_portfolio_configuration_hash():
    d1 = Exp005Config(variant_id=VARIANT_D, control_seed=1)
    d2 = Exp005Config(variant_id=VARIANT_D, control_seed=2)
    assert d1.config_hash() != d2.config_hash()
    assert d1.portfolio_configuration_hash() == d2.portfolio_configuration_hash()


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
