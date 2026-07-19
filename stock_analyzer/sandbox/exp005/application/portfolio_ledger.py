"""Portfolio state and daily equity snapshots -- Revision 5, Section 8.5, Stage 6.

Cash, reserved capital, and mark-to-market equity are computed PURELY from
already-persisted facts (currently-RESERVED reservations, all persisted executions,
open positions' `current_close`) -- never an incrementally-maintained running total.
This makes the ledger inherently resume-safe (nothing to lose on interruption) and
guarantees it can never independently drift from what the database actually holds,
the same principle already applied throughout Stages 1-5.

Cash identity (derived, not stored): starting_capital + sum(executions.net_cash_flow)
- sum(RESERVED reservations' reserved_amount). This holds at every point in a
sequential, one-day-at-a-time replay:
  - Admission accept (before any fill): the reservation subtracts its full amount
    from cash immediately (capital tied up in a pending order).
  - Fill: the reservation leaves the RESERVED sum (no longer subtracted) and the
    BUY execution's net_cash_flow (negative: -(gross_notional + commission)) enters
    the sum instead -- cash correctly moves from (X - slot_budget) to
    (X - gross_notional - commission), i.e. increases by exactly
    ExecutionAccounting.slot_remainder_units.
  - Expiry: the reservation leaves the RESERVED sum with no offsetting execution --
    cash correctly reverts by the full slot_budget.
  - SELL fill: the SELL execution's net_cash_flow (positive) enters the sum --
    proceeds return to cash directly.

`PortfolioLedger` implements `CashAvailabilityProvider`
(exp005/application/admission_orchestrator.py) so AdmissionTransactionService's
cash-awareness seam is backed by this real, ledger-derived source.
"""

from __future__ import annotations

from datetime import date, datetime

from stock_analyzer.sandbox.exp005.domain.equity_snapshot import PortfolioEquitySnapshot
from stock_analyzer.sandbox.exp005.domain.units import to_money_units
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


class OpenPositionMissingMarkError(RuntimeError):
    """Raised when an open position has neither a current_close (not yet monitored
    since its fill) nor an entry_price to fall back on -- should be structurally
    impossible (entry_price is always set at fill time), so this indicates a data-
    integrity failure, never a value to silently treat as zero."""


class PortfolioLedger:
    def __init__(
        self,
        portfolio_repo: PortfolioRepository,
        sandbox_repo: SandboxRepository,
        replay_id: str,
        starting_capital_units: int,
    ) -> None:
        self._portfolio_repo = portfolio_repo
        self._sandbox_repo = sandbox_repo
        self._replay_id = replay_id
        self._starting_capital_units = starting_capital_units

    def available_unreserved_cash_units(self) -> int:
        """CashAvailabilityProvider. Reflects all facts persisted so far for this
        replay -- during a live, sequential replay that is always exactly "as of
        right now," i.e. as of whatever day is currently being processed."""

        return self._cash_units()

    def _cash_units(self) -> int:
        net_cash_flow_units = sum(
            e.net_cash_flow_units for e in self._portfolio_repo.list_executions_for_experiment(self._replay_id)
        )
        reserved_units = sum(
            r.reserved_amount_units for r in self._portfolio_repo.list_active_reservations(self._replay_id)
        )
        return self._starting_capital_units + net_cash_flow_units - reserved_units

    def compute_snapshot(self, as_of_date: date, now: datetime) -> PortfolioEquitySnapshot:
        """Pure computation -- does not persist. The caller (the replay day-loop,
        Stage 8) is responsible for calling this exactly once per processed day,
        after that day's full entry/monitoring/candidate/admission sequence
        (Section 8.5), and persisting the result via
        PortfolioRepository.append_equity_snapshot."""

        executions = self._portfolio_repo.list_executions_for_experiment(self._replay_id)
        net_cash_flow_units = sum(e.net_cash_flow_units for e in executions)
        cumulative_commissions_units = sum(e.commission_units for e in executions)
        cumulative_slippage_cost_units = sum(e.slippage_cost_units for e in executions)

        active_reservations = self._portfolio_repo.list_active_reservations(self._replay_id)
        reserved_capital_units = sum(r.reserved_amount_units for r in active_reservations)
        reserved_order_count = len(active_reservations)

        open_positions = self._sandbox_repo.get_open_positions()
        open_position_market_value_units = 0
        for position in open_positions:
            mark_price = position.current_close if position.current_close is not None else position.entry_price
            if mark_price is None:
                raise OpenPositionMissingMarkError(
                    f"open position {position.position_id} has neither current_close nor entry_price -- "
                    "cannot mark to market."
                )
            open_position_market_value_units += to_money_units(position.quantity * mark_price)
        open_position_count = len(open_positions)

        cash_units = self._starting_capital_units + net_cash_flow_units - reserved_capital_units
        total_equity_units = cash_units + reserved_capital_units + open_position_market_value_units

        return PortfolioEquitySnapshot(
            snapshot_id=PortfolioEquitySnapshot.make_id(self._replay_id, as_of_date),
            replay_id=self._replay_id,
            as_of_date=as_of_date,
            cash_units=cash_units,
            reserved_capital_units=reserved_capital_units,
            open_position_market_value_units=open_position_market_value_units,
            total_equity_units=total_equity_units,
            open_position_count=open_position_count,
            reserved_order_count=reserved_order_count,
            cumulative_commissions_units=cumulative_commissions_units,
            cumulative_slippage_cost_units=cumulative_slippage_cost_units,
            created_at=now,
        )
