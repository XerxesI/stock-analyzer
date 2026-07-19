"""Narrow repository for EXP-005's four new tables (Revision 5, Stage 3).

Exposes only explicit, single-purpose operations -- no generic save(table, dict) or
update_anything() API that could hide which fact is actually being written or
undermine append-only semantics. Every multi-row read has an explicit ORDER BY;
nothing here depends on SQLite's unspecified row-return order or insertion order.

`insert_admission`/`insert_reservation` are deliberately NON-COMMITTING: they
participate in the caller-owned atomic transaction implemented in
application/admission_orchestrator.py (Stage 4), the single production code path
responsible for the accept/reserve/order triple (Section 8.2). Every other write here
is a single, self-contained, self-committing operation -- `append_execution`/
`append_equity_snapshot` (append-only, idempotent on retry) and
`update_reservation_status` (the ONE narrow, frozen-design-specified transition on
slot_reservations -- RESERVED -> CONVERTED on fill, RESERVED -> RELEASED on expiry,
Section 8.3 -- not a generic update API).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from stock_analyzer.sandbox.exp005.domain.admission import PortfolioAdmission, SlotReservation
from stock_analyzer.sandbox.exp005.domain.equity_snapshot import PortfolioEquitySnapshot
from stock_analyzer.sandbox.exp005.domain.execution import Execution


class AdmissionConflictError(RuntimeError):
    """Raised when a row already exists with DIFFERENT content than what is being
    written now, for any of the four EXP-005 tables' idempotent-insert methods --
    never silently overwritten. An identical repeat (the safe resume case) is a
    no-op instead. Mirrors the pattern already established for
    stock_analyzer.sandbox.infrastructure.sqlite_repository.RankedCandidateConflictError."""


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _floats_close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _admissions_match(existing: PortfolioAdmission, new: PortfolioAdmission) -> bool:
    return (
        existing.replay_id == new.replay_id
        and existing.candidate_id == new.candidate_id
        and existing.symbol == new.symbol
        and existing.as_of_date == new.as_of_date
        and existing.decision == new.decision
        and existing.rank_at_admission == new.rank_at_admission
        and _floats_close(existing.slot_budget, new.slot_budget)
        and existing.reason == new.reason
    )


def _reservations_match(existing: SlotReservation, new: SlotReservation) -> bool:
    return (
        existing.replay_id == new.replay_id
        and existing.candidate_id == new.candidate_id
        and existing.symbol == new.symbol
        and _floats_close(existing.reserved_amount, new.reserved_amount)
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
        and _floats_close(existing.raw_market_fill_price, new.raw_market_fill_price)
        and _floats_close(existing.effective_fill_price, new.effective_fill_price)
        and _floats_close(existing.quantity, new.quantity)
        and _floats_close(existing.gross_notional, new.gross_notional)
        and _floats_close(existing.commission, new.commission)
        and _floats_close(existing.slippage_rate, new.slippage_rate)
        and _floats_close(existing.slippage_cost, new.slippage_cost)
        and _floats_close(existing.net_cash_flow, new.net_cash_flow)
        and existing.fill_reason == new.fill_reason
        and existing.market_data_snapshot_id == new.market_data_snapshot_id
    )


def _snapshots_match(existing: PortfolioEquitySnapshot, new: PortfolioEquitySnapshot) -> bool:
    return (
        existing.replay_id == new.replay_id
        and _floats_close(existing.cash, new.cash)
        and _floats_close(existing.reserved_capital, new.reserved_capital)
        and _floats_close(existing.open_position_market_value, new.open_position_market_value)
        and _floats_close(existing.total_equity, new.total_equity)
        and existing.open_position_count == new.open_position_count
        and existing.reserved_order_count == new.reserved_order_count
        and _floats_close(existing.cumulative_commissions, new.cumulative_commissions)
        and _floats_close(existing.cumulative_slippage_cost, new.cumulative_slippage_cost)
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
            " decision, rank_at_admission, slot_budget, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                admission.admission_id,
                admission.replay_id,
                admission.candidate_id,
                admission.symbol,
                admission.as_of_date.isoformat(),
                admission.decision,
                admission.rank_at_admission,
                admission.slot_budget,
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
            slot_budget=row["slot_budget"],
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
            " reserved_amount, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                reservation.reservation_id,
                reservation.replay_id,
                reservation.admission_id,
                reservation.candidate_id,
                reservation.symbol,
                reservation.reserved_amount,
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

    def update_reservation_status(self, reservation_id: str, status: str, resolved_at: datetime) -> None:
        """The one, explicit, narrow status transition Section 8.3 specifies:
        RESERVED -> CONVERTED (fill) or RESERVED -> RELEASED (expiry). Not a generic
        update -- callers pass only one of those two target statuses. Commits
        immediately: this happens as part of a single, self-contained fill/expire
        event, not a multi-table transaction."""

        if status not in ("CONVERTED", "RELEASED"):
            raise ValueError(f"update_reservation_status only transitions to CONVERTED or RELEASED, got {status!r}")
        self._conn.execute(
            "UPDATE slot_reservations SET status = ?, resolved_at = ? WHERE reservation_id = ? AND status = 'RESERVED'",
            (status, resolved_at.isoformat(), reservation_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_reservation(row: sqlite3.Row) -> SlotReservation:
        return SlotReservation(
            reservation_id=row["reservation_id"],
            replay_id=row["replay_id"],
            admission_id=row["admission_id"],
            candidate_id=row["candidate_id"],
            symbol=row["symbol"],
            reserved_amount=row["reserved_amount"],
            status=row["status"],
            created_at=_dt(row["created_at"]),
            resolved_at=_dt(row["resolved_at"]),
        )

    # ---------------------------------------------------------------- executions
    def append_execution(self, execution: Execution) -> bool:
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
            " raw_market_fill_price, effective_fill_price, quantity, gross_notional, commission, "
            " slippage_rate, slippage_cost, net_cash_flow, fill_reason, market_data_snapshot_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                execution.raw_market_fill_price,
                execution.effective_fill_price,
                execution.quantity,
                execution.gross_notional,
                execution.commission,
                execution.slippage_rate,
                execution.slippage_cost,
                execution.net_cash_flow,
                execution.fill_reason,
                execution.market_data_snapshot_id,
                execution.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return True

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
            raw_market_fill_price=row["raw_market_fill_price"],
            effective_fill_price=row["effective_fill_price"],
            quantity=row["quantity"],
            gross_notional=row["gross_notional"],
            commission=row["commission"],
            slippage_rate=row["slippage_rate"],
            slippage_cost=row["slippage_cost"],
            net_cash_flow=row["net_cash_flow"],
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
            "INSERT INTO portfolio_equity_snapshots (snapshot_id, replay_id, as_of_date, cash, "
            " reserved_capital, open_position_market_value, total_equity, open_position_count, "
            " reserved_order_count, cumulative_commissions, cumulative_slippage_cost, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot.snapshot_id,
                snapshot.replay_id,
                snapshot.as_of_date.isoformat(),
                snapshot.cash,
                snapshot.reserved_capital,
                snapshot.open_position_market_value,
                snapshot.total_equity,
                snapshot.open_position_count,
                snapshot.reserved_order_count,
                snapshot.cumulative_commissions,
                snapshot.cumulative_slippage_cost,
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
            cash=row["cash"],
            reserved_capital=row["reserved_capital"],
            open_position_market_value=row["open_position_market_value"],
            total_equity=row["total_equity"],
            open_position_count=row["open_position_count"],
            reserved_order_count=row["reserved_order_count"],
            cumulative_commissions=row["cumulative_commissions"],
            cumulative_slippage_cost=row["cumulative_slippage_cost"],
            created_at=_dt(row["created_at"]),
        )
