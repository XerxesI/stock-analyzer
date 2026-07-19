"""Tests for EXP-005's execution accounting -- exact integer arithmetic, sign
conventions, and reconciliation (Revision 5, Stage 5, corrected in the Stage 2-5
review cycle).

Note on "multiple partial fills": not tested here because the frozen design does not
support them -- ADR-007's fill logic is all-or-nothing per session
(FILLED_AT_OPEN/FILLED_AT_CEILING/NO_FILL, never a partial quantity), and Section 8.3
sizes a BUY's quantity to consume the entire slot budget in one fill.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from stock_analyzer.sandbox.exp005.domain.accounting import (
    InvalidExecutionInputError,
    compute_buy_accounting,
    compute_sell_accounting,
    reconcile_execution,
    reconcile_executions_for_order,
)
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import to_money_units, to_price_units, to_quantity_units, to_rate_units
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db

NOW = datetime.now(timezone.utc)

# Independently reproduced prices required by the review, spanning low/medium/high.
REVIEW_PRICES = [1.01, 1.38, 1.75, 2.12, 2.49, 37.4321, 123.4567]


def _execution_from_accounting(side: str, raw_price: float, accounting, *, commission=1.0, slippage_rate=0.0005, **overrides) -> Execution:
    base = dict(
        execution_id=f"c0:{side}:2026-01-06",
        replay_id="replay-1",
        variant_id="B",
        control_seed=None,
        order_id="c0:order" if side == BUY else None,
        candidate_id="c0",
        position_id="AAA:2026-01-06",
        symbol="AAA",
        side=side,
        decision_date=date(2026, 1, 5),
        execution_date=date(2026, 1, 6),
        raw_market_fill_price_units=to_price_units(raw_price),
        effective_fill_price_units=accounting.effective_fill_price_units,
        quantity_units=accounting.quantity_units,
        gross_notional_units=accounting.gross_notional_units,
        commission_units=to_money_units(commission),
        slippage_rate_units=to_rate_units(slippage_rate),
        slippage_cost_units=accounting.slippage_cost_units,
        net_cash_flow_units=accounting.net_cash_flow_units,
        fill_reason="FILLED_AT_OPEN" if side == BUY else "SELL_TARGET",
        market_data_snapshot_id="snap-1",
        created_at=NOW,
    )
    base.update(overrides)
    return Execution(**base)


# --------------------------------------------------------------------- BUY accounting


def test_buy_zero_fees_zero_slippage():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=0.0, slippage_rate=0.0)
    assert result.effective_fill_price_units == to_price_units(100.0)
    assert result.quantity_units == to_quantity_units(100.0)
    assert result.gross_notional_units == to_money_units(10_000.0)
    assert result.slippage_cost_units == 0
    assert result.net_cash_flow_units == -to_money_units(10_000.0)
    assert result.slot_remainder_units == 0


def test_buy_with_commission_only():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0)
    # security cost + commission + remainder == slot_budget, exactly.
    assert result.gross_notional_units + to_money_units(1.0) + result.slot_remainder_units == to_money_units(10_000.0)
    assert result.net_cash_flow_units == -(result.gross_notional_units + to_money_units(1.0))


def test_buy_with_adverse_slippage_only():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=0.0, slippage_rate=0.01)
    assert result.effective_fill_price_units == to_price_units(101.0)  # 100 * 1.01, worse for the buyer
    assert result.slippage_cost_units > 0


def test_buy_with_combined_commission_and_slippage_never_exceeds_budget():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    assert result.effective_fill_price_units > to_price_units(100.0)  # adverse
    security_cost_plus_commission = result.gross_notional_units + to_money_units(1.0)
    assert security_cost_plus_commission <= to_money_units(10_000.0)
    assert security_cost_plus_commission + result.slot_remainder_units == to_money_units(10_000.0)


def test_buy_rejects_non_positive_raw_price():
    with pytest.raises(InvalidExecutionInputError):
        compute_buy_accounting(raw_fill_price=0.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    with pytest.raises(InvalidExecutionInputError):
        compute_buy_accounting(raw_fill_price=-5.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)


def test_buy_rejects_commission_exceeding_slot_budget():
    with pytest.raises(InvalidExecutionInputError):
        compute_buy_accounting(raw_fill_price=100.0, slot_budget=1.0, commission=1.0, slippage_rate=0.0005)


# -------------------------------------------------------------------- SELL accounting


def test_sell_zero_fees_zero_slippage():
    result = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=0.0, slippage_rate=0.0)
    assert result.effective_fill_price_units == to_price_units(120.0)
    assert result.gross_notional_units == to_money_units(12_000.0)
    assert result.slippage_cost_units == 0
    assert result.net_cash_flow_units == to_money_units(12_000.0)


def test_sell_with_commission_only():
    result = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=1.0, slippage_rate=0.0)
    assert result.net_cash_flow_units == to_money_units(11_999.0)


def test_sell_with_adverse_slippage_only():
    result = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=0.0, slippage_rate=0.01)
    assert result.effective_fill_price_units == to_price_units(118.8)  # 120 * 0.99
    assert result.slippage_cost_units > 0
    assert result.net_cash_flow_units < to_money_units(12_000.0)


def test_sell_with_combined_commission_and_slippage():
    result = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=1.0, slippage_rate=0.0005)
    assert result.effective_fill_price_units < to_price_units(120.0)
    assert result.net_cash_flow_units == result.gross_notional_units - to_money_units(1.0)


def test_sell_rejects_non_positive_quantity():
    with pytest.raises(InvalidExecutionInputError):
        compute_sell_accounting(raw_fill_price=120.0, quantity=0.0, commission=1.0, slippage_rate=0.0005)
    with pytest.raises(InvalidExecutionInputError):
        compute_sell_accounting(raw_fill_price=120.0, quantity=-10.0, commission=1.0, slippage_rate=0.0005)


def test_rejects_out_of_range_slippage_rate():
    with pytest.raises(InvalidExecutionInputError):
        compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=-0.01)
    with pytest.raises(InvalidExecutionInputError):
        compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=1.5)


# ---------------------------------------------------- SELL slippage-rate boundary (closure task 3)
#
# effective_price = raw * (1 - rate) for SELL: a rate of 1.0 or more makes the
# effective price zero or negative, a degenerate fill -- confirmed P2 from the
# second review. SELL's upper bound is strictly < 1, tighter than the general
# [0, 1] bound _validate_common enforces for BUY (where rate=1.0 merely doubles the
# price, still positive).


@pytest.mark.parametrize("slippage_rate", [0.0, 0.0005, 0.9999])
def test_sell_accepts_valid_slippage_rates_up_to_but_excluding_one(slippage_rate: float):
    # commission=0.0 here isolates the slippage_rate boundary itself: at rate=0.9999
    # gross proceeds shrink to $0.01/share, and a nonzero commission would separately
    # trip the net_cash_flow_units>0 check by exceeding those proceeds -- a genuine
    # but different degenerate case, not the one this test targets.
    result = compute_sell_accounting(raw_fill_price=100.0, quantity=100.0, commission=0.0, slippage_rate=slippage_rate)
    assert result.effective_fill_price_units > 0
    assert result.gross_notional_units > 0
    assert result.net_cash_flow_units > 0


def test_sell_rejects_slippage_rate_of_exactly_one():
    with pytest.raises(InvalidExecutionInputError):
        compute_sell_accounting(raw_fill_price=100.0, quantity=100.0, commission=1.0, slippage_rate=1.0)


def test_sell_rejects_slippage_rate_above_one():
    with pytest.raises(InvalidExecutionInputError):
        compute_sell_accounting(raw_fill_price=100.0, quantity=100.0, commission=1.0, slippage_rate=1.5)


def test_sell_rejects_when_commission_consumes_all_proceeds_at_extreme_slippage():
    """A valid slippage_rate (<1) combined with a commission that meets or exceeds
    the shrunken gross proceeds is a separate degenerate case the net_cash_flow_units
    positivity check catches -- found while boundary-testing rate=0.9999 above."""

    with pytest.raises(InvalidExecutionInputError):
        compute_sell_accounting(raw_fill_price=100.0, quantity=100.0, commission=1.0, slippage_rate=0.9999)


def test_sell_rejects_a_rate_that_would_round_effective_price_to_zero():
    """A rate just under 1 applied to a very small raw price can still round the
    effective price down to zero units -- the post-computation positivity checks
    must catch this even though the rate itself is technically < 1."""

    with pytest.raises(InvalidExecutionInputError):
        compute_sell_accounting(raw_fill_price=0.0001, quantity=1.0, commission=0.0, slippage_rate=0.9999)


# ------------------------------------------------------------- exact round-trip / reconciliation


def test_buy_accounting_always_reconciles_exactly():
    """This is the confirmed defect's direct regression test: compute_buy_accounting's
    OWN output must reconcile via the SAME formula reconcile_execution uses."""

    result = compute_buy_accounting(raw_fill_price=37.4321, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(BUY, 37.4321, result)
    assert reconcile_execution(execution) is True


def test_sell_accounting_always_reconciles_exactly():
    result = compute_sell_accounting(raw_fill_price=123.4567, quantity=42.1357, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(SELL, 123.4567, result, quantity_units=result.quantity_units)
    assert reconcile_execution(execution) is True


@pytest.mark.parametrize("raw_price", REVIEW_PRICES)
def test_buy_reconstructs_exactly_across_review_prices(raw_price: float):
    result = compute_buy_accounting(raw_fill_price=raw_price, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(BUY, raw_price, result)

    assert reconcile_execution(execution) is True
    # BUY never exceeds slot budget.
    assert result.gross_notional_units + to_money_units(1.0) <= to_money_units(10_000.0)
    # No unexplained rounding drift: remainder + spent == budget, exactly.
    assert result.gross_notional_units + to_money_units(1.0) + result.slot_remainder_units == to_money_units(10_000.0)


@pytest.mark.parametrize("raw_price", REVIEW_PRICES)
def test_sell_reconstructs_exactly_across_review_prices(raw_price: float):
    result = compute_sell_accounting(raw_fill_price=raw_price, quantity=55.6789, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(SELL, raw_price, result, quantity_units=result.quantity_units)
    assert reconcile_execution(execution) is True


@pytest.mark.parametrize("raw_price", REVIEW_PRICES)
@pytest.mark.parametrize("slot_budget", [500.0, 10_000.0, 987_654.32])
def test_buy_reconstructs_exactly_across_price_and_budget_grid(raw_price: float, slot_budget: float):
    result = compute_buy_accounting(raw_fill_price=raw_price, slot_budget=slot_budget, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(BUY, raw_price, result)
    assert reconcile_execution(execution) is True
    assert result.gross_notional_units + to_money_units(1.0) <= to_money_units(slot_budget)


# ------------------------------------------------------------------------- rounding


def test_rounding_mode_is_documented_round_half_up():
    from stock_analyzer.sandbox.exp005.domain.units import to_money_units

    # 0.005 dollars = half a cent -- round-half-up rounds this UP to 1 cent (unlike
    # Python's default round-half-to-even, which could go either way depending on
    # binary float representation). Verified against the exact Decimal value, not a
    # float literal, to avoid the exact ambiguity this whole corrective cycle exists
    # to eliminate.
    assert to_money_units(Decimal("0.005")) == 1  # rounds up to 1 cent, not down to 0
    assert to_money_units(Decimal("2.005")) == 201  # 200.5 cents -> 201


# --------------------------------------------------------------------- reconciliation


def test_reconcile_execution_accepts_internally_consistent_buy():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(BUY, 100.0, result)
    assert reconcile_execution(execution) is True


def test_reconcile_execution_accepts_internally_consistent_sell():
    result = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(SELL, 120.0, result, quantity_units=result.quantity_units)
    assert reconcile_execution(execution) is True


@pytest.mark.parametrize(
    "field",
    ["effective_fill_price_units", "quantity_units", "gross_notional_units", "commission_units",
     "slippage_rate_units", "slippage_cost_units", "net_cash_flow_units"],
)
def test_reconcile_execution_rejects_each_independently_corrupted_field(field: str):
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(BUY, 100.0, result)
    corrupted = replace(execution, **{field: getattr(execution, field) + 1})
    assert reconcile_execution(corrupted) is False


def test_reconcile_execution_rejects_corrupted_raw_price():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    execution = _execution_from_accounting(BUY, 100.0, result)
    corrupted = replace(execution, raw_market_fill_price_units=execution.raw_market_fill_price_units + 100)
    assert reconcile_execution(corrupted) is False


def test_reconcile_execution_rejects_unrecognized_side():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    execution = replace(_execution_from_accounting(BUY, 100.0, result), side="HOLD")
    with pytest.raises(InvalidExecutionInputError):
        reconcile_execution(execution)


def test_reconcile_executions_for_order_true_only_if_all_reconcile():
    buy_result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    good = _execution_from_accounting(BUY, 100.0, buy_result)
    bad = replace(
        _execution_from_accounting(SELL, 100.0, compute_sell_accounting(100.0, 50.0, 1.0, 0.0005), quantity_units=to_quantity_units(50.0)),
        execution_id="c0:SELL:2026-01-10",
        net_cash_flow_units=1,
    )
    assert reconcile_executions_for_order([good]) is True
    assert reconcile_executions_for_order([good, bad]) is False


# ------------------------------------------------------------------- schema-level FK


def test_execution_linked_to_missing_order_is_rejected():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("run-1", "2026-01-05", "generate-candidates", NOW.isoformat(), None, "COMPLETED", "v1", None, None, "hash", None),
    )
    conn.execute(
        "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
        " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
        " exclusion_reason, adv_quintile, market_regime, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("c0", "run-1", "2026-01-05", "AAA", 1, 5.0, 100.0, 2.0, 101.0, 1, 1, None, "adv_q3", "Bull_Normal", NOW.isoformat()),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO executions (execution_id, replay_id, variant_id, control_seed, order_id, "
            " candidate_id, position_id, symbol, side, decision_date, execution_date, "
            " raw_market_fill_price_units, effective_fill_price_units, quantity_units, gross_notional_units, "
            " commission_units, slippage_rate_units, slippage_cost_units, net_cash_flow_units, fill_reason, "
            " market_data_snapshot_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e1", "replay-1", "B", None, "no-such-order", "c0", None, "AAA", "BUY",
                "2026-01-05", "2026-01-06", 1_000_000, 1_000_500, 100_000, 100_050_000, 100, 5, 50_000, -100_050_100,
                "FILLED_AT_OPEN", "snap-1", NOW.isoformat(),
            ),
        )
