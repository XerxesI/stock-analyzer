"""Narrow repository for EXP-005's four new tables (Revision 5, Stage 3, corrected
in the Stage 2-5 review cycle).

Exposes only explicit, single-purpose operations -- no generic save(table, dict) or
update_anything() API. Every multi-row read has an explicit ORDER BY; nothing here
depends on SQLite's unspecified row-return order or insertion order.

All numeric comparisons are EXACT integer equality (domain/units.py) -- no float, no
tolerance. `insert_admission`/`insert_reservation` are deliberately NON-COMMITTING:
they participate in the caller-owned atomic transaction in
application/admission_orchestrator.py (Section 8.2). `append_execution` REJECTS a
non-reconciling execution before writing it (Stage 5 review) -- reconciliation is
checked at the write boundary, not left to be silently discovered later.
`update_reservation_status` is conflict-safe: an identical repeat to an
already-applied target is a no-op; a conflicting second transition (e.g.
CONVERTED -> RELEASED) raises; a missing reservation raises; a zero-row UPDATE is
never silently treated as success (Stage 4 review).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from stock_analyzer.sandbox.exp005.domain.accounting import reconcile_execution
from stock_analyzer.sandbox.exp005.domain.admission import CONVERTED, RELEASED, RESERVED, PortfolioAdmission, SlotReservation
from stock_analyzer.sandbox.exp005.domain.equity_snapshot import PortfolioEquitySnapshot
from stock_analyzer.sandbox.exp005.domain.execution import Execution

TRANSITIONED = "TRANSITIONED"
ALREADY_IN_TARGET_STATE = "ALREADY_IN_TARGET_STATE"


class AdmissionConflictError(RuntimeError):
    """Raised when a row already exists with DIFFERENT content than what is being
    written now, for any of the four EXP-005 tables' idempotent-insert methods --
    never silently overwritten. An identical repeat (the safe resume case) is a
    no-op instead. Mirrors the pattern already established for
    stock_analyzer.sandbox.infrastructure.sqlite_repository.RankedCandidateConflictError."""


class NonReconcilingExecutionError(RuntimeError):
    """Raised by append_execution when the execution's own persisted fields do not
    reconcile against each other (domain.accounting.reconcile_execution) --
    rejected BEFORE writing, never accepted and repaired on read."""


class ReservationNotFoundError(RuntimeError):
    """Raised by update_reservation_status when no slot_reservations row exists for
    the given reservation_id."""


class ReservationTransitionConflictError(RuntimeError):
    """Raised by update_reservation_status when the reservation is already resolved
    to a DIFFERENT status than the one requested -- e.g. attempting RELEASED after
    it was already CONVERTED. A rowcount of zero from the underlying UPDATE is never
    silently treated as success; this error (or ALREADY_IN_TARGET_STATE) is always
    the explicit reason."""


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _admissions_match(existing: PortfolioAdmission, new: PortfolioAdmission) -> bool:
    return (
        existing.replay_id == new.replay_id
        and existing.candidate_id == new.candidate_id
        and existing.symbol == new.symbol
        and existing.as_of_date == new.as_of_date
        and existing.decision == new.decision
        and existing.rank_at_admission == new.rank_at_admission
        and existing.slot_budget_units == new.slot_budget_units
        and existing.reason == new.reason
    )


def _reservations_match(existing: SlotReservation, new: SlotReservation) -> bool:
    return (
        existing.replay_id == new.replay_id
        and existing.candidate_id == new.candidate_id
        and existing.symbol == new.symbol
        and existing.reserved_amount_units == new.reserved_amount_units
        and existing.status == new.status
    )


def _executions_match(existing: Execution, new: Execution) -> bool:
    return (
        existing.replay_id == new.replay_id
        and existing.variant_id == new.variant_id
        and existing.control_seed == new.control_seed
        and existing.order_id == new.order_id
        and existing.candidate_id == new.candidate_id
        and existing.position_id == new.position_id
        and existing.symbol == new.symbol
        and existing.side == new.side
        and existing.decision_date == new.decision_date
        and existing.execution_date == new.execution_date
        and existing.raw_market_fill_price_units == new.raw_market_fill_price_units
        and existing.effective_fill_price_units == new.effective_fill_price_units
        and existing.quantity_units == new.quantity_units
        and existing.gross_notional_units == new.gross_notional_units
        and existing.commission_units == new.commission_units
        and existing.slippage_rate_units == new.slippage_rate_units
        and existing.slippage_cost_units == new.slippage_cost_units
        and existing.net_cash_flow_units == new.net_cash_flow_units
        and existing.fill_reason == new.fill_reason
        and existing.market_data_snapshot_id == new.market_data_snapshot_id
    )


def _snapshots_match(existing: PortfolioEquitySnapshot, new: PortfolioEquitySnapshot) -> bool:
    return (
        existing.replay_id == new.replay_id
        and existing.cash_units == new.cash_units
        and existing.reserved_capital_units == new.reserved_capital_units
        and existing.open_position_market_value_units == new.open_position_market_value_units
        and existing.total_equity_units == new.total_equity_units
        and existing.open_position_count == new.open_position_count
        and existing.reserved_order_count == new.reserved_order_count
        and existing.cumulative_commissions_units == new.cumulative_commissions_units
        and existing.cumulative_slippage_cost_units == new.cumulative_slippage_cost_units
    )


class PortfolioRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------------------------------------------------------------- admissions
    def insert_admission(self, admission: PortfolioAdmission) -> bool:
        """Non-committing -- see module docstring on transaction ownership."""

        existing = self.get_admission(admission.admission_id)
        if existing is not None:
            if _admissions_match(existing, admission):
                return False
            raise AdmissionConflictError(
                f"portfolio_admissions row for {admission.admission_id} already exists with "
                f"different content -- existing={existing!r}, new={admission!r}."
            )
        self._conn.execute(
            "INSERT INTO portfolio_admissions (admission_id, replay_id, candidate_id, symbol, as_of_date, "
            " decision, rank_at_admission, slot_budget_units, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                admission.admission_id,
                admission.replay_id,
                admission.candidate_id,
                admission.symbol,
                admission.as_of_date.isoformat(),
                admission.decision,
                admission.rank_at_admission,
                admission.slot_budget_units,
                admission.reason,
                admission.created_at.isoformat(),
            ),
        )
        return True

    def get_admission(self, admission_id: str) -> PortfolioAdmission | None:
        row = self._conn.execute(
            "SELECT * FROM portfolio_admissions WHERE admission_id = ?", (admission_id,)
        ).fetchone()
        return self._row_to_admission(row) if row else None

    def list_admissions_for_experiment(self, replay_id: str) -> list[PortfolioAdmission]:
        """Every admission decision ever made for this replay, across all dates --
        used by post-hoc report generation (Section 25), unlike
        list_admissions_for_session's single-day scope."""

        rows = self._conn.execute(
            "SELECT * FROM portfolio_admissions WHERE replay_id = ? ORDER BY as_of_date ASC, rank_at_admission ASC",
            (replay_id,),
        ).fetchall()
        return [self._row_to_admission(r) for r in rows]

    def list_admissions_for_session(self, replay_id: str, as_of_date: date) -> list[PortfolioAdmission]:
        rows = self._conn.execute(
            "SELECT * FROM portfolio_admissions WHERE replay_id = ? AND as_of_date = ? "
            "ORDER BY rank_at_admission ASC, symbol ASC",
            (replay_id, as_of_date.isoformat()),
        ).fetchall()
        return [self._row_to_admission(r) for r in rows]

    @staticmethod
    def _row_to_admission(row: sqlite3.Row) -> PortfolioAdmission:
        return PortfolioAdmission(
            admission_id=row["admission_id"],
            replay_id=row["replay_id"],
            candidate_id=row["candidate_id"],
            symbol=row["symbol"],
            as_of_date=_d(row["as_of_date"]),
            decision=row["decision"],
            rank_at_admission=row["rank_at_admission"],
            slot_budget_units=row["slot_budget_units"],
            reason=row["reason"],
            created_at=_dt(row["created_at"]),
        )

    # -------------------------------------------------------------- reservations
    def insert_reservation(self, reservation: SlotReservation) -> bool:
        """Non-committing -- see module docstring on transaction ownership."""

        existing = self.get_reservation_for_admission(reservation.admission_id)
        if existing is not None:
            if _reservations_match(existing, reservation):
                return False
            raise AdmissionConflictError(
                f"slot_reservations row for admission {reservation.admission_id} already exists "
                f"with different content -- existing={existing!r}, new={reservation!r}."
            )
        self._conn.execute(
            "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
            " reserved_amount_units, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                reservation.reservation_id,
                reservation.replay_id,
                reservation.admission_id,
                reservation.candidate_id,
                reservation.symbol,
                reservation.reserved_amount_units,
                reservation.status,
                reservation.created_at.isoformat(),
                reservation.resolved_at.isoformat() if reservation.resolved_at else None,
            ),
        )
        return True

    def get_reservation_for_admission(self, admission_id: str) -> SlotReservation | None:
        row = self._conn.execute(
            "SELECT * FROM slot_reservations WHERE admission_id = ?", (admission_id,)
        ).fetchone()
        return self._row_to_reservation(row) if row else None

    def list_active_reservations(self, replay_id: str) -> list[SlotReservation]:
        rows = self._conn.execute(
            "SELECT * FROM slot_reservations WHERE replay_id = ? AND status = 'RESERVED' "
            "ORDER BY created_at ASC, reservation_id ASC",
            (replay_id,),
        ).fetchall()
        return [self._row_to_reservation(r) for r in rows]

    def list_reservations_for_experiment(self, replay_id: str) -> list[SlotReservation]:
        """Every reservation ever created for this replay, regardless of its
        current (mutable) status -- unlike list_active_reservations. Used by
        post-hoc diagnostics (Section 24) to reconstruct which reservations
        occupied a slot on a PAST date, via each row's own created_at/resolved_at
        timestamps rather than its current status (which only reflects the final
        outcome, since slot_reservations is a mutable current-state table -- see
        the module docstring)."""

        rows = self._conn.execute(
            "SELECT * FROM slot_reservations WHERE replay_id = ? ORDER BY created_at ASC, reservation_id ASC",
            (replay_id,),
        ).fetchall()
        return [self._row_to_reservation(r) for r in rows]

    def _update_reservation_status_row(self, reservation_id: str, status: str, resolved_at: datetime) -> str:
        """Non-committing -- see update_reservation_status."""

        if status not in (CONVERTED, RELEASED):
            raise ValueError(f"update_reservation_status only transitions to CONVERTED or RELEASED, got {status!r}")

        row = self._conn.execute(
            "SELECT status FROM slot_reservations WHERE reservation_id = ?", (reservation_id,)
        ).fetchone()
        if row is None:
            raise ReservationNotFoundError(f"no slot_reservations row for reservation_id={reservation_id!r}")

        current_status = row["status"]
        if current_status == status:
            return ALREADY_IN_TARGET_STATE
        if current_status != RESERVED:
            raise ReservationTransitionConflictError(
                f"reservation {reservation_id} is already {current_status!r}; cannot transition to {status!r}"
            )

        self._conn.execute(
            "UPDATE slot_reservations SET status = ?, resolved_at = ? WHERE reservation_id = ? AND status = 'RESERVED'",
            (status, resolved_at.isoformat(), reservation_id),
        )
        return TRANSITIONED

    def update_reservation_status(self, reservation_id: str, status: str, resolved_at: datetime) -> str:
        """The one, explicit, narrow status transition Section 8.3 specifies:
        RESERVED -> CONVERTED (fill) or RESERVED -> RELEASED (expiry).

        Returns ALREADY_IN_TARGET_STATE if the reservation is already at `status`
        (a safe idempotent retry -- no write performed). Returns TRANSITIONED if the
        RESERVED -> status transition was just applied. Raises
        ReservationNotFoundError if no such reservation exists.  Raises
        ReservationTransitionConflictError if the reservation is already resolved to
        a DIFFERENT status than requested (e.g. CONVERTED, but RELEASED was
        requested) -- a genuine conflict, never silently treated as success."""

        result = self._update_reservation_status_row(reservation_id, status, resolved_at)
        self._conn.commit()
        return result

    @staticmethod
    def _row_to_reservation(row: sqlite3.Row) -> SlotReservation:
        return SlotReservation(
            reservation_id=row["reservation_id"],
            replay_id=row["replay_id"],
            admission_id=row["admission_id"],
            candidate_id=row["candidate_id"],
            symbol=row["symbol"],
            reserved_amount_units=row["reserved_amount_units"],
            status=row["status"],
            created_at=_dt(row["created_at"]),
            resolved_at=_dt(row["resolved_at"]),
        )

    # ---------------------------------------------------------------- executions
    def _append_execution_row(self, execution: Execution) -> bool:
        """Non-committing -- see append_execution. Still rejects a non-reconciling
        execution and still enforces idempotent-insert-or-conflict, so a caller
        composing this into a larger atomic transaction (EntryService's fill event,
        MonitoringService's close event) gets the exact same safety guarantees, not
        a stripped-down variant."""

        if not reconcile_execution(execution):
            raise NonReconcilingExecutionError(
                f"executions row for {execution.execution_id} does not reconcile against its own "
                f"raw inputs (raw price, quantity, commission, slippage rate) -- refusing to persist "
                f"an internally inconsistent execution: {execution!r}."
            )

        existing = self.get_execution(execution.execution_id)
        if existing is not None:
            if _executions_match(existing, execution):
                return False
            raise AdmissionConflictError(
                f"executions row for {execution.execution_id} already exists with different "
                f"content -- existing={existing!r}, new={execution!r}."
            )
        self._conn.execute(
            "INSERT INTO executions (execution_id, replay_id, variant_id, control_seed, order_id, "
            " candidate_id, position_id, symbol, side, decision_date, execution_date, "
            " raw_market_fill_price_units, effective_fill_price_units, quantity_units, gross_notional_units, "
            " commission_units, slippage_rate_units, slippage_cost_units, net_cash_flow_units, fill_reason, "
            " market_data_snapshot_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                execution.execution_id,
                execution.replay_id,
                execution.variant_id,
                execution.control_seed,
                execution.order_id,
                execution.candidate_id,
                execution.position_id,
                execution.symbol,
                execution.side,
                execution.decision_date.isoformat(),
                execution.execution_date.isoformat(),
                execution.raw_market_fill_price_units,
                execution.effective_fill_price_units,
                execution.quantity_units,
                execution.gross_notional_units,
                execution.commission_units,
                execution.slippage_rate_units,
                execution.slippage_cost_units,
                execution.net_cash_flow_units,
                execution.fill_reason,
                execution.market_data_snapshot_id,
                execution.created_at.isoformat(),
            ),
        )
        return True

    def append_execution(self, execution: Execution) -> bool:
        """Rejects a non-reconciling execution BEFORE writing it -- reconciliation
        is a write-time gate, not something discovered later by a separate audit
        pass."""

        created = self._append_execution_row(execution)
        self._conn.commit()
        return created

    def get_execution(self, execution_id: str) -> Execution | None:
        row = self._conn.execute("SELECT * FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
        return self._row_to_execution(row) if row else None

    def list_executions_for_order(self, order_id: str) -> list[Execution]:
        rows = self._conn.execute(
            "SELECT * FROM executions WHERE order_id = ? ORDER BY execution_date ASC, execution_id ASC",
            (order_id,),
        ).fetchall()
        return [self._row_to_execution(r) for r in rows]

    def list_executions_for_position(self, position_id: str) -> list[Execution]:
        rows = self._conn.execute(
            "SELECT * FROM executions WHERE position_id = ? ORDER BY execution_date ASC, execution_id ASC",
            (position_id,),
        ).fetchall()
        return [self._row_to_execution(r) for r in rows]

    def list_executions_for_experiment(self, replay_id: str) -> list[Execution]:
        rows = self._conn.execute(
            "SELECT * FROM executions WHERE replay_id = ? ORDER BY execution_date ASC, execution_id ASC",
            (replay_id,),
        ).fetchall()
        return [self._row_to_execution(r) for r in rows]

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> Execution:
        return Execution(
            execution_id=row["execution_id"],
            replay_id=row["replay_id"],
            variant_id=row["variant_id"],
            control_seed=row["control_seed"],
            order_id=row["order_id"],
            candidate_id=row["candidate_id"],
            position_id=row["position_id"],
            symbol=row["symbol"],
            side=row["side"],
            decision_date=_d(row["decision_date"]),
            execution_date=_d(row["execution_date"]),
            raw_market_fill_price_units=row["raw_market_fill_price_units"],
            effective_fill_price_units=row["effective_fill_price_units"],
            quantity_units=row["quantity_units"],
            gross_notional_units=row["gross_notional_units"],
            commission_units=row["commission_units"],
            slippage_rate_units=row["slippage_rate_units"],
            slippage_cost_units=row["slippage_cost_units"],
            net_cash_flow_units=row["net_cash_flow_units"],
            fill_reason=row["fill_reason"],
            market_data_snapshot_id=row["market_data_snapshot_id"],
            created_at=_dt(row["created_at"]),
        )

    # ----------------------------------------------------------- equity snapshots
    def append_equity_snapshot(self, snapshot: PortfolioEquitySnapshot) -> bool:
        existing = self.get_equity_snapshot(snapshot.replay_id, snapshot.as_of_date)
        if existing is not None:
            if _snapshots_match(existing, snapshot):
                return False
            raise AdmissionConflictError(
                f"portfolio_equity_snapshots row for {snapshot.replay_id}/{snapshot.as_of_date} already "
                f"exists with different content -- existing={existing!r}, new={snapshot!r}."
            )
        self._conn.execute(
            "INSERT INTO portfolio_equity_snapshots (snapshot_id, replay_id, as_of_date, cash_units, "
            " reserved_capital_units, open_position_market_value_units, total_equity_units, "
            " open_position_count, reserved_order_count, cumulative_commissions_units, "
            " cumulative_slippage_cost_units, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot.snapshot_id,
                snapshot.replay_id,
                snapshot.as_of_date.isoformat(),
                snapshot.cash_units,
                snapshot.reserved_capital_units,
                snapshot.open_position_market_value_units,
                snapshot.total_equity_units,
                snapshot.open_position_count,
                snapshot.reserved_order_count,
                snapshot.cumulative_commissions_units,
                snapshot.cumulative_slippage_cost_units,
                snapshot.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return True

    def get_equity_snapshot(self, replay_id: str, as_of_date: date) -> PortfolioEquitySnapshot | None:
        row = self._conn.execute(
            "SELECT * FROM portfolio_equity_snapshots WHERE replay_id = ? AND as_of_date = ?",
            (replay_id, as_of_date.isoformat()),
        ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def list_equity_snapshots(self, replay_id: str) -> list[PortfolioEquitySnapshot]:
        rows = self._conn.execute(
            "SELECT * FROM portfolio_equity_snapshots WHERE replay_id = ? ORDER BY as_of_date ASC",
            (replay_id,),
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> PortfolioEquitySnapshot:
        return PortfolioEquitySnapshot(
            snapshot_id=row["snapshot_id"],
            replay_id=row["replay_id"],
            as_of_date=_d(row["as_of_date"]),
            cash_units=row["cash_units"],
            reserved_capital_units=row["reserved_capital_units"],
            open_position_market_value_units=row["open_position_market_value_units"],
            total_equity_units=row["total_equity_units"],
            open_position_count=row["open_position_count"],
            reserved_order_count=row["reserved_order_count"],
            cumulative_commissions_units=row["cumulative_commissions_units"],
            cumulative_slippage_cost_units=row["cumulative_slippage_cost_units"],
            created_at=_dt(row["created_at"]),
        )
