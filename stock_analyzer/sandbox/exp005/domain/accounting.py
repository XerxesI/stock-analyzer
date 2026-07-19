"""Execution accounting: the one, centralized place EXP-005's sign conventions and
reconciliation formulas are computed (Revision 5, Section 18, Stage 5 corrective
cycle). No other module -- execution service, portfolio ledger, or report --
recomputes these independently; they call into this module.

**Exact integer arithmetic throughout** (domain/units.py) -- no float, no tolerance.
The corrective-cycle fix: the prior design computed high-precision Decimal
intermediates and rounded gross_notional/net_cash_flow *independently* from those
intermediates, so `reconcile_execution` (which recomputes from the *persisted,
already-rounded* fields) could disagree with the value `compute_buy_accounting`
itself had produced -- a genuine, confirmed round-trip defect. The fix is a single,
explicit calculation order where EVERY step consumes the PREVIOUS step's
already-rounded, already-persisted value, never a pre-rounding high-precision one:

    1. raw price, in exact price units
    2/3. exact slippage arithmetic -> ROUNDED effective price (persisted)
    4/5. quantity derived from the budget using the PERSISTED effective price ->
         ROUNDED quantity (persisted) -- rounded DOWN for BUY specifically, so
         `security cost + commission` can never exceed the slot budget
    6. gross notional = PERSISTED quantity x PERSISTED effective price -> rounded
    7. net cash flow = PERSISTED gross notional +/- commission
    8. (BUY only) any unspent slot remainder is computed and returned explicitly,
       never silently absorbed

`_compute_from_quantity` is the single shared implementation of steps 2-3 and 6-7,
used by BOTH `compute_sell_accounting` (forward) and `reconcile_execution`
(recomputation from a persisted row) -- so a persisted execution and its
reconciliation can never independently diverge; they are, by construction, the same
code path.

Sign convention: `quantity_units`, `gross_notional_units`, `commission_units`,
`slippage_cost_units` are always non-negative MAGNITUDES. Direction is carried
entirely by `side` and by the signed `net_cash_flow_units` (negative for BUY,
positive for SELL) -- matching the CHECK constraints already enforced at the schema
level (infrastructure/schema.py). Adverse slippage: BUY effective price >= raw
price; SELL effective price <= raw price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN

from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import (
    money_units_to_decimal,
    price_units_to_decimal,
    quantity_units_to_decimal,
    rate_units_to_decimal,
    to_money_units,
    to_price_units,
    to_quantity_units,
    to_rate_units,
)


class InvalidExecutionInputError(ValueError):
    """Raised for a non-positive price/quantity/budget, an out-of-range slippage
    rate, an unrecognized side, or a computed result that would violate the slot-
    budget invariant -- fails fast rather than silently producing a nonsensical or
    over-budget execution."""


@dataclass(frozen=True)
class ExecutionAccounting:
    """The computed, mutually-reconciled fields for one execution, in exact integer
    units -- returned together so a caller can never accidentally persist a subset
    that doesn't reconcile with the rest."""

    effective_fill_price_units: int
    quantity_units: int
    gross_notional_units: int
    slippage_cost_units: int
    net_cash_flow_units: int
    slot_remainder_units: int  # BUY: unspent budget retained as cash. SELL: always 0.


def _validate_common(raw_fill_price: float, commission: float, slippage_rate: float) -> None:
    if raw_fill_price <= 0:
        raise InvalidExecutionInputError(f"raw_fill_price must be positive, got {raw_fill_price!r}")
    if commission < 0:
        raise InvalidExecutionInputError(f"commission must be non-negative, got {commission!r}")
    if not (0 <= slippage_rate <= 1):
        raise InvalidExecutionInputError(f"slippage_rate must be in [0, 1], got {slippage_rate!r}")


def _compute_from_quantity(
    raw_price_units: int, quantity_units: int, commission_units: int, rate_units: int, side: str
) -> tuple[int, int, int, int]:
    """The single shared formula (steps 2-3 and 6-7 of the module docstring's
    calculation order): given raw price/quantity/commission/rate/side, returns
    (effective_price_units, gross_notional_units, slippage_cost_units,
    net_cash_flow_units). Used by BOTH compute_sell_accounting (forward) and
    reconcile_execution (recomputation from a persisted row) -- the one place this
    arithmetic exists."""

    if side not in (BUY, SELL):
        raise InvalidExecutionInputError(f"unrecognized side {side!r}")

    raw = price_units_to_decimal(raw_price_units)
    rate = rate_units_to_decimal(rate_units)
    quantity = quantity_units_to_decimal(quantity_units)

    effective_exact = raw * (1 + rate) if side == BUY else raw * (1 - rate)
    effective_units = to_price_units(effective_exact)
    effective = price_units_to_decimal(effective_units)

    gross_notional_units = to_money_units(quantity * effective)
    gross = money_units_to_decimal(gross_notional_units)

    if side == BUY:
        slippage_cost_units = to_money_units(quantity * (effective - raw))
        net_cash_flow_units = -(gross_notional_units + commission_units)
    else:
        slippage_cost_units = to_money_units(quantity * (raw - effective))
        net_cash_flow_units = gross_notional_units - commission_units

    return effective_units, gross_notional_units, slippage_cost_units, net_cash_flow_units


def compute_buy_accounting(
    raw_fill_price: float, slot_budget: float, commission: float, slippage_rate: float
) -> ExecutionAccounting:
    """Quantity is DERIVED (steps 4-5): sized from the slot budget net of
    commission, using the already-rounded effective price, rounded DOWN to
    quantity-scale so `security cost + commission <= slot budget` always holds. The
    remainder (slot_budget - security_cost - commission), typically a few
    hundredths of a cent, is returned explicitly as `slot_remainder_units` -- never
    silently absorbed into either side of the ledger."""

    _validate_common(raw_fill_price, commission, slippage_rate)
    if slot_budget <= 0:
        raise InvalidExecutionInputError(f"slot_budget must be positive, got {slot_budget!r}")
    if commission >= slot_budget:
        raise InvalidExecutionInputError(f"commission ({commission}) must be less than slot_budget ({slot_budget})")

    raw_units = to_price_units(raw_fill_price)
    slot_budget_units = to_money_units(slot_budget)
    commission_units = to_money_units(commission)
    rate_units = to_rate_units(slippage_rate)

    # Steps 1-3: rounded effective price, persisted immediately.
    raw = price_units_to_decimal(raw_units)
    rate = rate_units_to_decimal(rate_units)
    effective_exact = raw * (1 + rate)
    effective_units = to_price_units(effective_exact)
    effective = price_units_to_decimal(effective_units)

    # Steps 4-5: quantity derived from the PERSISTED effective price, rounded DOWN.
    available_units = slot_budget_units - commission_units
    available = money_units_to_decimal(available_units)
    quantity_exact = available / effective
    quantity_units = to_quantity_units(quantity_exact, rounding=ROUND_DOWN)
    if quantity_units <= 0:
        raise InvalidExecutionInputError(
            f"slot_budget ({slot_budget}) net of commission ({commission}) cannot buy a positive "
            f"quantity at effective price {price_units_to_decimal(effective_units)}"
        )

    # Steps 6-7: gross notional and net cash flow, via the SAME shared formula
    # reconcile_execution uses -- guarantees this function's own output is exactly
    # what independent reconciliation will recompute.
    effective_units_check, gross_notional_units, slippage_cost_units, net_cash_flow_units = _compute_from_quantity(
        raw_units, quantity_units, commission_units, rate_units, BUY
    )
    assert effective_units_check == effective_units  # deterministic given identical inputs

    # Step 8: explicit remainder -- must never be negative (would mean the slot
    # budget was oversubscribed by rounding, which ROUND_DOWN on quantity exists
    # specifically to prevent).
    slot_remainder_units = slot_budget_units - gross_notional_units - commission_units
    if slot_remainder_units < 0:
        raise InvalidExecutionInputError(
            f"internal invariant violated: security cost + commission "
            f"({gross_notional_units + commission_units}) exceeds slot budget ({slot_budget_units})"
        )

    return ExecutionAccounting(
        effective_fill_price_units=effective_units,
        quantity_units=quantity_units,
        gross_notional_units=gross_notional_units,
        slippage_cost_units=slippage_cost_units,
        net_cash_flow_units=net_cash_flow_units,
        slot_remainder_units=slot_remainder_units,
    )


def compute_sell_accounting(
    raw_fill_price: float, quantity: float, commission: float, slippage_rate: float
) -> ExecutionAccounting:
    """Quantity is GIVEN (the position's existing share count, fixed at entry) --
    proceeds return to cash net of commission and adverse slippage. No remainder
    concept applies (nothing is being sized against a budget)."""

    _validate_common(raw_fill_price, commission, slippage_rate)
    if quantity <= 0:
        raise InvalidExecutionInputError(f"quantity must be positive, got {quantity!r}")

    raw_units = to_price_units(raw_fill_price)
    quantity_units = to_quantity_units(quantity)
    commission_units = to_money_units(commission)
    rate_units = to_rate_units(slippage_rate)

    effective_units, gross_notional_units, slippage_cost_units, net_cash_flow_units = _compute_from_quantity(
        raw_units, quantity_units, commission_units, rate_units, SELL
    )

    return ExecutionAccounting(
        effective_fill_price_units=effective_units,
        quantity_units=quantity_units,
        gross_notional_units=gross_notional_units,
        slippage_cost_units=slippage_cost_units,
        net_cash_flow_units=net_cash_flow_units,
        slot_remainder_units=0,
    )


def reconcile_execution(execution: Execution) -> bool:
    """Recomputes effective price, gross notional, slippage cost, and net cash flow
    from the execution's own persisted raw inputs (raw price, quantity, commission,
    slippage rate, side) via `_compute_from_quantity` -- the SAME code path
    `compute_sell_accounting` and `compute_buy_accounting`'s final steps use -- and
    compares each for EXACT integer equality. Never repairs a mismatch; a caller
    that finds `False` must treat it as a data-integrity failure to investigate."""

    effective_units, gross_units, slippage_units, cash_flow_units = _compute_from_quantity(
        execution.raw_market_fill_price_units,
        execution.quantity_units,
        execution.commission_units,
        execution.slippage_rate_units,
        execution.side,
    )
    return (
        effective_units == execution.effective_fill_price_units
        and gross_units == execution.gross_notional_units
        and slippage_units == execution.slippage_cost_units
        and cash_flow_units == execution.net_cash_flow_units
    )


def reconcile_executions_for_order(executions: list[Execution]) -> bool:
    """True only if every execution in the list individually reconciles."""

    return all(reconcile_execution(e) for e in executions)
