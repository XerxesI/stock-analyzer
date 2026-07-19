"""Tests for EXP-005's execution accounting -- sign conventions, reconciliation,
and rounding (Revision 5, Stage 5).

Note on "multiple partial fills": not tested here because the frozen design does not
support them -- ADR-007's fill logic is all-or-nothing per session
(FILLED_AT_OPEN/FILLED_AT_CEILING/NO_FILL, never a partial quantity), and Section 8.3
sizes a BUY's quantity to consume the entire slot budget in one fill. There is no
partial-fill scenario to cover under the currently frozen mechanics.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.sandbox.exp005.domain.accounting import (
    InvalidExecutionInputError,
    compute_buy_accounting,
    compute_sell_accounting,
    reconcile_execution,
    reconcile_executions_for_order,
)
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db

NOW = datetime.now(timezone.utc)


def _execution(side: str, **overrides) -> Execution:
    base = dict(
        execution_id="c0:X:2026-01-06",
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
        raw_market_fill_price=100.0,
        effective_fill_price=100.05 if side == BUY else 99.95,
        quantity=99.0,
        gross_notional=9904.95,
        commission=1.0,
        slippage_rate=0.0005,
        slippage_cost=4.95,
        net_cash_flow=-9905.95 if side == BUY else 9903.95,
        fill_reason="FILLED_AT_OPEN" if side == BUY else "SELL_TARGET",
        market_data_snapshot_id="snap-1",
        created_at=NOW,
    )
    base.update(overrides)
    return Execution(**base)


# --------------------------------------------------------------------- BUY accounting


def test_buy_zero_fees_zero_slippage():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=0.0, slippage_rate=0.0)
    assert result.effective_fill_price == 100.0
    assert result.quantity == 100.0
    assert result.gross_notional == 10_000.0
    assert result.slippage_cost == 0.0
    assert result.net_cash_flow == -10_000.0


def test_buy_with_commission_only():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0)
    # quantity * price + commission == slot_budget, exactly.
    assert result.gross_notional + 1.0 == pytest.approx(10_000.0, abs=0.01)
    assert result.net_cash_flow == -10_000.0
    assert result.gross_notional == pytest.approx(9999.0, abs=0.01)


def test_buy_with_adverse_slippage_only():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=0.0, slippage_rate=0.01)
    assert result.effective_fill_price == 101.0  # 100 * 1.01, worse for the buyer
    assert result.slippage_cost > 0
    assert result.net_cash_flow == -10_000.0


def test_buy_with_combined_commission_and_slippage_spends_exactly_the_budget():
    result = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    assert result.effective_fill_price > 100.0  # adverse
    # security cost + commission == slot_budget, exactly (Section 8.3).
    assert result.gross_notional + 1.0 == pytest.approx(10_000.0, abs=0.01)
    assert result.net_cash_flow == pytest.approx(-10_000.0, abs=0.01)


def test_buy_exact_full_fill_consumes_entire_slot_budget():
    result = compute_buy_accounting(raw_fill_price=37.4321, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    assert abs(result.net_cash_flow) == pytest.approx(10_000.0, abs=0.01)


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
    assert result.effective_fill_price == 120.0
    assert result.gross_notional == 12_000.0
    assert result.slippage_cost == 0.0
    assert result.net_cash_flow == 12_000.0


def test_sell_with_commission_only():
    result = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=1.0, slippage_rate=0.0)
    assert result.net_cash_flow == 11_999.0


def test_sell_with_adverse_slippage_only():
    result = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=0.0, slippage_rate=0.01)
    assert result.effective_fill_price == 118.8  # 120 * 0.99, worse for the seller
    assert result.slippage_cost > 0
    assert result.net_cash_flow < 12_000.0


def test_sell_with_combined_commission_and_slippage():
    result = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=1.0, slippage_rate=0.0005)
    assert result.effective_fill_price < 120.0
    assert result.net_cash_flow == pytest.approx(result.gross_notional - 1.0, abs=1e-9)


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


# ------------------------------------------------------------------------- rounding


def test_rounding_uses_project_convention_price_and_money_decimals():
    result = compute_buy_accounting(raw_fill_price=33.33335, slot_budget=1000.0, commission=1.0, slippage_rate=0.0)
    # round_price -> 4 decimals, round_money -> 2 decimals (stock_analyzer.sandbox.config).
    assert round(result.effective_fill_price, 4) == result.effective_fill_price
    assert round(result.gross_notional, 2) == result.gross_notional
    assert round(result.net_cash_flow, 2) == result.net_cash_flow


def test_rounding_half_increment_behavior_is_round_half_to_even():
    # Python's round() (which round_money/round_price both use) rounds halves to the
    # nearest even digit -- documented and tested explicitly, not assumed.
    from stock_analyzer.sandbox.config import round_money

    assert round_money(2.005) == round(2.005, 2)  # binary float representation of
    # 2.005 is actually slightly below 2.005, so this equals 2.0 -- the point of this
    # test is that our rounding is IDENTICAL to Python's round(), not a different
    # rounding mode, whatever that value turns out to be.
    assert round_money(0.125) == round(0.125, 2)


# --------------------------------------------------------------------- reconciliation


def test_reconcile_execution_accepts_internally_consistent_buy():
    accounting = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    execution = _execution(
        BUY,
        raw_market_fill_price=100.0,
        effective_fill_price=accounting.effective_fill_price,
        quantity=accounting.quantity,
        gross_notional=accounting.gross_notional,
        commission=1.0,
        slippage_rate=0.0005,
        slippage_cost=accounting.slippage_cost,
        net_cash_flow=accounting.net_cash_flow,
    )
    assert reconcile_execution(execution) is True


def test_reconcile_execution_accepts_internally_consistent_sell():
    accounting = compute_sell_accounting(raw_fill_price=120.0, quantity=100.0, commission=1.0, slippage_rate=0.0005)
    execution = _execution(
        SELL,
        raw_market_fill_price=120.0,
        effective_fill_price=accounting.effective_fill_price,
        quantity=accounting.quantity,
        gross_notional=accounting.gross_notional,
        commission=1.0,
        slippage_rate=0.0005,
        slippage_cost=accounting.slippage_cost,
        net_cash_flow=accounting.net_cash_flow,
    )
    assert reconcile_execution(execution) is True


def test_reconcile_execution_rejects_inconsistent_effective_price():
    execution = _execution(BUY, effective_fill_price=999.0)  # raw=100, slippage=0.0005 -> should be ~100.05
    assert reconcile_execution(execution) is False


def test_reconcile_execution_rejects_inconsistent_gross_notional():
    execution = _execution(BUY, gross_notional=1.0)  # doesn't match quantity * effective_fill_price
    assert reconcile_execution(execution) is False


def test_reconcile_execution_rejects_inconsistent_net_cash_flow():
    execution = _execution(BUY, net_cash_flow=-1.0)  # doesn't match -(gross_notional + commission)
    assert reconcile_execution(execution) is False


def test_reconcile_execution_rejects_unrecognized_side():
    execution = replace(_execution(BUY), side="HOLD")
    with pytest.raises(InvalidExecutionInputError):
        reconcile_execution(execution)


def test_reconcile_executions_for_order_true_only_if_all_reconcile():
    buy_accounting = compute_buy_accounting(raw_fill_price=100.0, slot_budget=10_000.0, commission=1.0, slippage_rate=0.0005)
    good = _execution(
        BUY, raw_market_fill_price=100.0, effective_fill_price=buy_accounting.effective_fill_price,
        quantity=buy_accounting.quantity, gross_notional=buy_accounting.gross_notional,
        slippage_cost=buy_accounting.slippage_cost, net_cash_flow=buy_accounting.net_cash_flow,
    )
    bad = _execution(SELL, execution_id="c0:SELL:2026-01-10", net_cash_flow=1.0)
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
            " raw_market_fill_price, effective_fill_price, quantity, gross_notional, commission, "
            " slippage_rate, slippage_cost, net_cash_flow, fill_reason, market_data_snapshot_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e1", "replay-1", "B", None, "no-such-order", "c0", None, "AAA", "BUY",
                "2026-01-05", "2026-01-06", 100.0, 100.05, 10.0, 1000.5, 1.0, 0.0005, 0.5, -1001.5,
                "FILLED_AT_OPEN", "snap-1", NOW.isoformat(),
            ),
        )
