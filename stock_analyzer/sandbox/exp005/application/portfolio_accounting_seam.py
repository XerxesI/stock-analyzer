"""EXP-005's implementation of `PortfolioAccountingSeam`
(stock_analyzer/sandbox/application/accounting_seam.py) -- Stage 6, the "aligned
dual-accounting" resolution to the Section 8.3/11.3 contradiction.

Core decides WHETHER/WHEN a fill or exit happens, and AT WHAT RAW PRICE (ADR-007,
target/time-exit rules) -- completely unaffected by this class. This class decides
ONLY the position's sizing (the same quantity is then recorded everywhere: core's
`VirtualPosition.quantity`, both BUY/SELL `VirtualTransaction.quantity`, and both
BUY/SELL `Execution.quantity_units`) and appends EXP-005's own cost-adjusted
`executions` audit row plus resolves the candidate's `slot_reservations` row --
inside the SAME atomic transaction EntryService/MonitoringService already hold open
for their own core-table writes.

`size_buy` computes the full `ExecutionAccounting` result ONCE (Stage 5's
`compute_buy_accounting`) and caches it, keyed by `order_id`; `on_filled` retrieves
and reuses that exact cached result -- it is never recomputed, so the quantity
`EntryService` used to build `VirtualPosition`/`VirtualTransaction` and the quantity
this class persists to `executions` can never independently diverge.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.position import VirtualPosition
from stock_analyzer.sandbox.domain.transaction import VirtualTransaction
from stock_analyzer.sandbox.exp005.config import PortfolioConfig
from stock_analyzer.sandbox.exp005.domain.accounting import ExecutionAccounting, compute_buy_accounting, compute_sell_accounting
from stock_analyzer.sandbox.exp005.domain.admission import CONVERTED, RELEASED, SlotReservation
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import (
    money_units_to_float,
    quantity_units_to_float,
    to_money_units,
    to_price_units,
    to_rate_units,
)
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository


class AccountingSeamIntegrityError(RuntimeError):
    """Raised when a fill/expiry/close event has no corresponding, correctly-staged
    slot_reservations/executions row -- by Section 8.1's structural guarantee,
    every PENDING entry_orders row created via AdmissionTransactionService has
    exactly one RESERVED reservation, and every filled position has exactly one BUY
    execution, so a missing one here is a genuine data-integrity failure, never a
    state to route around."""


class Exp005AccountingSeam:
    def __init__(
        self,
        portfolio_repo: PortfolioRepository,
        replay_id: str,
        variant_id: str,
        control_seed: int | None,
        portfolio_config: PortfolioConfig,
        market_data_snapshot_id: str,
    ) -> None:
        self._portfolio_repo = portfolio_repo
        self._replay_id = replay_id
        self._variant_id = variant_id
        self._control_seed = control_seed
        self._portfolio_config = portfolio_config
        self._market_data_snapshot_id = market_data_snapshot_id
        self._pending_buy_accounting: dict[str, tuple[ExecutionAccounting, SlotReservation]] = {}

    def size_buy(self, order: EntryOrder, raw_fill_price: float, fill_date: date) -> float:
        reservation = self._portfolio_repo.get_reservation_for_admission(order.candidate_id)
        if reservation is None:
            raise AccountingSeamIntegrityError(
                f"order {order.order_id} (candidate {order.candidate_id}) has no slot_reservations row -- "
                "every PENDING order created via AdmissionTransactionService must have exactly one."
            )
        slot_budget = money_units_to_float(reservation.reserved_amount_units)
        accounting = compute_buy_accounting(
            raw_fill_price=raw_fill_price,
            slot_budget=slot_budget,
            commission=self._portfolio_config.entry_commission,
            slippage_rate=self._portfolio_config.slippage_rate,
        )
        self._pending_buy_accounting[order.order_id] = (accounting, reservation)
        return quantity_units_to_float(accounting.quantity_units)

    def on_filled(
        self, order: EntryOrder, position: VirtualPosition, transaction: VirtualTransaction, raw_fill_price: float
    ) -> None:
        accounting, reservation = self._pending_buy_accounting.pop(order.order_id)
        now = datetime.now(timezone.utc)

        execution = Execution(
            execution_id=Execution.make_id(order.candidate_id, BUY, position.entry_date),
            replay_id=self._replay_id,
            variant_id=self._variant_id,
            control_seed=self._control_seed,
            order_id=order.order_id,
            candidate_id=order.candidate_id,
            position_id=position.position_id,
            symbol=order.symbol,
            side=BUY,
            decision_date=order.signal_date,
            execution_date=position.entry_date,
            raw_market_fill_price_units=to_price_units(raw_fill_price),
            effective_fill_price_units=accounting.effective_fill_price_units,
            quantity_units=accounting.quantity_units,
            gross_notional_units=accounting.gross_notional_units,
            commission_units=to_money_units(self._portfolio_config.entry_commission),
            slippage_rate_units=to_rate_units(self._portfolio_config.slippage_rate),
            slippage_cost_units=accounting.slippage_cost_units,
            net_cash_flow_units=accounting.net_cash_flow_units,
            fill_reason=transaction.reason,
            market_data_snapshot_id=self._market_data_snapshot_id,
            created_at=now,
        )
        self._portfolio_repo._append_execution_row(execution)
        self._portfolio_repo._update_reservation_status_row(reservation.reservation_id, CONVERTED, resolved_at=now)

    def on_expired(self, order: EntryOrder, as_of_date: date, reason: str) -> None:
        reservation = self._portfolio_repo.get_reservation_for_admission(order.candidate_id)
        if reservation is None:
            raise AccountingSeamIntegrityError(
                f"order {order.order_id} (candidate {order.candidate_id}) has no slot_reservations row to release."
            )
        self._portfolio_repo._update_reservation_status_row(
            reservation.reservation_id, RELEASED, resolved_at=datetime.now(timezone.utc)
        )

    def on_closed(
        self, position: VirtualPosition, transaction: VirtualTransaction, exit_date: date, exit_price: float, exit_reason: str
    ) -> None:
        buy_execution = self._get_buy_execution(position.position_id)
        accounting = compute_sell_accounting(
            raw_fill_price=exit_price,
            quantity=quantity_units_to_float(buy_execution.quantity_units),
            commission=self._portfolio_config.exit_commission,
            slippage_rate=self._portfolio_config.slippage_rate,
        )
        if accounting.quantity_units != buy_execution.quantity_units:
            raise AccountingSeamIntegrityError(
                f"SELL quantity_units ({accounting.quantity_units}) for position {position.position_id} "
                f"does not exactly match its BUY execution's quantity_units ({buy_execution.quantity_units}) -- "
                "SELL must always reuse the original BUY quantity."
            )

        execution = Execution(
            execution_id=Execution.make_id(position.candidate_id, SELL, exit_date),
            replay_id=self._replay_id,
            variant_id=self._variant_id,
            control_seed=self._control_seed,
            order_id=None,
            candidate_id=position.candidate_id,
            position_id=position.position_id,
            symbol=position.symbol,
            side=SELL,
            decision_date=exit_date,
            execution_date=exit_date,
            raw_market_fill_price_units=to_price_units(exit_price),
            effective_fill_price_units=accounting.effective_fill_price_units,
            quantity_units=accounting.quantity_units,
            gross_notional_units=accounting.gross_notional_units,
            commission_units=to_money_units(self._portfolio_config.exit_commission),
            slippage_rate_units=to_rate_units(self._portfolio_config.slippage_rate),
            slippage_cost_units=accounting.slippage_cost_units,
            net_cash_flow_units=accounting.net_cash_flow_units,
            fill_reason=exit_reason,
            market_data_snapshot_id=self._market_data_snapshot_id,
            created_at=datetime.now(timezone.utc),
        )
        self._portfolio_repo._append_execution_row(execution)

    def _get_buy_execution(self, position_id: str) -> Execution:
        for execution in self._portfolio_repo.list_executions_for_position(position_id):
            if execution.side == BUY:
                return execution
        raise AccountingSeamIntegrityError(f"position {position_id} has no BUY execution to close against.")
