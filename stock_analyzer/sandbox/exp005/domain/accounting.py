"""Execution accounting: the one, centralized place EXP-005's sign conventions and
reconciliation formulas are computed (Revision 5, Section 18, Stage 5). No other
module -- execution service, portfolio ledger, or report -- recomputes these
independently; they call into this module.

Sign convention (frozen here, referenced everywhere else): `quantity`,
`gross_notional`, `commission`, `slippage_cost` are always non-negative MAGNITUDES.
Direction is carried entirely by `side` and by the signed `net_cash_flow` (negative
for BUY -- cash leaves the portfolio; positive for SELL -- cash enters it). This
matches the CHECK constraints already enforced at the schema level
(infrastructure/schema.py).

Both `raw_market_fill_price` (ADR-007's unadjusted simulated price -- what decides
whether an order is fillable at all, Section 6) and `effective_fill_price`
(slippage-adjusted -- what cash accounting actually uses) are always computed;
neither is derived from or overwrites the other after the fact. Adverse slippage:
BUY effective price >= raw price; SELL effective price <= raw price.

Rounding: all arithmetic is performed in `Decimal`, each input converted via
`Decimal(str(x))` (Stage 1's lesson -- this recovers the exact intended decimal value
of a float literal, not an approximation) to avoid binary-float error accumulating
across multiple computation steps. Results are rounded ONLY at the final monetary
boundary, reusing this project's existing `round_price` (4 decimals)/`round_money`
(2 decimals) helpers (`stock_analyzer.sandbox.config`) -- Python's round-half-to-even,
the same convention every other sandbox module already uses, not a new one
introduced here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from stock_analyzer.sandbox.config import round_money, round_price
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution


class InvalidExecutionInputError(ValueError):
    """Raised for a non-positive price/quantity/budget, an out-of-range slippage
    rate, or an unrecognized side -- fails fast rather than silently producing a
    nonsensical execution."""


@dataclass(frozen=True)
class ExecutionAccounting:
    """The computed, mutually-reconciled fields for one execution -- returned
    together so a caller can never accidentally persist a subset that doesn't
    reconcile with the rest."""

    effective_fill_price: float
    quantity: float
    gross_notional: float
    slippage_cost: float
    net_cash_flow: float


def _validate_common(raw_fill_price: float, commission: float, slippage_rate: float) -> None:
    if raw_fill_price <= 0:
        raise InvalidExecutionInputError(f"raw_fill_price must be positive, got {raw_fill_price!r}")
    if commission < 0:
        raise InvalidExecutionInputError(f"commission must be non-negative, got {commission!r}")
    if not (0 <= slippage_rate <= 1):
        raise InvalidExecutionInputError(f"slippage_rate must be in [0, 1], got {slippage_rate!r}")


def compute_buy_accounting(
    raw_fill_price: float, slot_budget: float, commission: float, slippage_rate: float
) -> ExecutionAccounting:
    """Quantity is sized to spend the ENTIRE slot budget (net of commission) at the
    slippage-adjusted effective price: `security cost (quantity * effective price) +
    commission == slot_budget`, exactly (Revision 5, Section 8.3's quantity
    formula) -- fractional shares make exact budget consumption possible, no
    leftover reservation dust."""

    _validate_common(raw_fill_price, commission, slippage_rate)
    if slot_budget <= 0:
        raise InvalidExecutionInputError(f"slot_budget must be positive, got {slot_budget!r}")
    if commission >= slot_budget:
        raise InvalidExecutionInputError(f"commission ({commission}) must be less than slot_budget ({slot_budget})")

    raw = Decimal(str(raw_fill_price))
    budget = Decimal(str(slot_budget))
    comm = Decimal(str(commission))
    rate = Decimal(str(slippage_rate))

    effective = raw * (1 + rate)  # adverse for the buyer: effective >= raw
    quantity = (budget - comm) / effective
    gross_notional = quantity * effective
    slippage_cost = quantity * (effective - raw)
    net_cash_flow = -(gross_notional + comm)

    return ExecutionAccounting(
        effective_fill_price=round_price(float(effective)),
        quantity=round_price(float(quantity)),
        gross_notional=round_money(float(gross_notional)),
        slippage_cost=round_money(float(slippage_cost)),
        net_cash_flow=round_money(float(net_cash_flow)),
    )


def compute_sell_accounting(
    raw_fill_price: float, quantity: float, commission: float, slippage_rate: float
) -> ExecutionAccounting:
    """Quantity is fixed (the position's existing share count, set at entry) --
    proceeds return to cash net of commission and adverse slippage."""

    _validate_common(raw_fill_price, commission, slippage_rate)
    if quantity <= 0:
        raise InvalidExecutionInputError(f"quantity must be positive, got {quantity!r}")

    raw = Decimal(str(raw_fill_price))
    qty = Decimal(str(quantity))
    comm = Decimal(str(commission))
    rate = Decimal(str(slippage_rate))

    effective = raw * (1 - rate)  # adverse for the seller: effective <= raw
    gross_notional = qty * effective
    slippage_cost = qty * (raw - effective)
    net_cash_flow = gross_notional - comm

    return ExecutionAccounting(
        effective_fill_price=round_price(float(effective)),
        quantity=round_price(float(qty)),
        gross_notional=round_money(float(gross_notional)),
        slippage_cost=round_money(float(slippage_cost)),
        net_cash_flow=round_money(float(net_cash_flow)),
    )


def reconcile_execution(execution: Execution) -> bool:
    """Recomputes every derived field from the execution's own raw inputs
    (raw_market_fill_price, quantity, commission, slippage_rate, side) using exact
    Decimal arithmetic and compares each against the persisted value for EXACT
    equality -- valid specifically because both sides go through the identical
    rounding boundary (round_price/round_money). Checks, in order: effective price
    vs. raw price + slippage + side; gross notional vs. quantity * effective price;
    net cash flow vs. gross notional + commission + side.

    Never "fixes" an inconsistent execution -- a caller that finds a mismatch must
    treat it as a data-integrity failure to investigate, not something to silently
    repair on read (Stage 5 review)."""

    raw = Decimal(str(execution.raw_market_fill_price))
    rate = Decimal(str(execution.slippage_rate))
    quantity = Decimal(str(execution.quantity))
    comm = Decimal(str(execution.commission))

    if execution.side == BUY:
        expected_effective = raw * (1 + rate)
    elif execution.side == SELL:
        expected_effective = raw * (1 - rate)
    else:
        raise InvalidExecutionInputError(f"unrecognized side {execution.side!r}")

    if round_price(float(expected_effective)) != execution.effective_fill_price:
        return False

    effective = Decimal(str(execution.effective_fill_price))
    expected_gross = quantity * effective
    if round_money(float(expected_gross)) != execution.gross_notional:
        return False

    gross = Decimal(str(execution.gross_notional))
    expected_cash_flow = -(gross + comm) if execution.side == BUY else gross - comm
    return round_money(float(expected_cash_flow)) == execution.net_cash_flow


def reconcile_executions_for_order(executions: list[Execution]) -> bool:
    """True only if every execution in the list individually reconciles."""

    return all(reconcile_execution(e) for e in executions)
