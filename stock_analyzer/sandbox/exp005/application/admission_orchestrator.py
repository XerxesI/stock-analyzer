"""The single, atomic production code path for portfolio admission (Revision 5,
Section 8.2). `AdmissionTransactionService.admit_candidate` is the ONE place that
ever writes a `portfolio_admissions` row, its `slot_reservations` row, and its
`entry_orders` row -- always together, inside one explicit SQLite transaction that
this class alone opens, commits, and rolls back. No other code path may write these
three facts.

Capacity correctness under concurrent/interleaved admissions: `BEGIN IMMEDIATE` is
issued BEFORE the capacity count is read (not after), so the write lock is held for
the entire read-decide-write sequence. A second connection attempting its own
`BEGIN IMMEDIATE` concurrently blocks until this transaction commits or rolls back --
by the time it proceeds, it observes this transaction's already-committed reservation
and correctly recomputes remaining capacity. A "read count, then insert" sequence
without this ordering would not be safe (Stage 4 review).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.exp005.domain.admission import (
    ACCEPTED,
    NO_CAPACITY,
    RESERVED,
    PortfolioAdmission,
    SlotReservation,
)
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


@dataclass(frozen=True)
class AdmissionResult:
    admission: PortfolioAdmission
    reservation: SlotReservation | None  # None iff decision is NO_CAPACITY
    order: EntryOrder | None  # None iff decision is NO_CAPACITY
    created: bool  # False if this call was an idempotent no-op repeat


class AdmissionTransactionService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        portfolio_repo: PortfolioRepository,
        sandbox_repo: SandboxRepository,
        replay_id: str,
        max_slots: int,
        slot_budget: float,
    ) -> None:
        self._conn = conn
        self._portfolio_repo = portfolio_repo
        self._sandbox_repo = sandbox_repo
        self._replay_id = replay_id
        self._max_slots = max_slots
        self._slot_budget = slot_budget

    def admit_candidate(self, candidate: RankedCandidate, as_of_date: date, order: EntryOrder) -> AdmissionResult:
        """`order` is the fully-constructed EntryOrder to persist IF admitted --
        built by the caller from the candidate (Stage 7's responsibility, matching
        exactly how CandidateService._create_entry_order builds one today). This
        method owns only the capacity decision and its atomic persistence, not
        order-construction policy.

        admission_id == candidate.candidate_id (Section 8.2): a candidate can have
        at most one admission decision, ever -- enforced both by this method's own
        idempotency check and by the primary key on portfolio_admissions.
        """

        admission_id = candidate.candidate_id

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing_admission = self._portfolio_repo.get_admission(admission_id)
            if existing_admission is not None:
                reservation = self._portfolio_repo.get_reservation_for_admission(admission_id)
                existing_order = (
                    self._sandbox_repo.get_entry_order_by_candidate(admission_id) if reservation else None
                )
                self._conn.execute("COMMIT")
                return AdmissionResult(existing_admission, reservation, existing_order, created=False)

            occupied = self._count_occupied_slots()
            now = datetime.now(timezone.utc)

            if occupied >= self._max_slots:
                admission = PortfolioAdmission(
                    admission_id=admission_id,
                    replay_id=self._replay_id,
                    candidate_id=candidate.candidate_id,
                    symbol=candidate.symbol,
                    as_of_date=as_of_date,
                    decision=NO_CAPACITY,
                    rank_at_admission=candidate.daily_rank,
                    slot_budget=None,
                    reason=f"{occupied}/{self._max_slots} slots occupied",
                    created_at=now,
                )
                self._portfolio_repo.insert_admission(admission)
                self._conn.execute("COMMIT")
                return AdmissionResult(admission, None, None, created=True)

            admission = PortfolioAdmission(
                admission_id=admission_id,
                replay_id=self._replay_id,
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                as_of_date=as_of_date,
                decision=ACCEPTED,
                rank_at_admission=candidate.daily_rank,
                slot_budget=self._slot_budget,
                reason=None,
                created_at=now,
            )
            reservation = SlotReservation(
                reservation_id=SlotReservation.make_id(admission_id),
                replay_id=self._replay_id,
                admission_id=admission_id,
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                reserved_amount=self._slot_budget,
                status=RESERVED,
                created_at=now,
            )
            self._portfolio_repo.insert_admission(admission)
            self._portfolio_repo.insert_reservation(reservation)
            self._sandbox_repo._insert_entry_order_row(order)
            self._conn.execute("COMMIT")
            return AdmissionResult(admission, reservation, order, created=True)
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def _count_occupied_slots(self) -> int:
        """Section 8.3's invariant: count(open positions) + count(RESERVED
        reservations) <= max_slots. Both counts are read inside the same BEGIN
        IMMEDIATE transaction that will (if capacity allows) also write the new
        reservation -- see the module docstring on why this ordering matters."""

        reserved = len(self._portfolio_repo.list_active_reservations(self._replay_id))
        open_positions = len(self._sandbox_repo.get_open_positions())
        return reserved + open_positions
