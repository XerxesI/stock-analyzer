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
and correctly recomputes remaining capacity.

Stage 2-5 corrective-cycle fixes:
  - Cross-entity validation (`_validate_order_matches_candidate`) rejects a
    mismatched order/candidate/date BEFORE the transaction opens -- an admission can
    never bind the wrong order.
  - Idempotency on an existing admission now compares the FULL logical content of
    the request against what is persisted (candidate/symbol/date/rank/replay), not
    merely "does a row exist" -- any mismatch raises AdmissionConflictError. An
    ACCEPTED admission missing its reservation or order is treated as an integrity
    failure (AdmissionIntegrityError), never silently returned as a successful
    no-op. A NO_CAPACITY admission is never re-decided into ACCEPTED on retry, even
    if capacity has since freed up -- the persisted decision is final.
  - Admission is no longer slot-count-only: a `CashAvailabilityProvider` is a
    REQUIRED constructor argument (no default that silently assumes unlimited cash)
    -- capacity requires BOTH a free slot AND sufficient unreserved cash. The
    decision value returned when cash is insufficient is still `NO_CAPACITY` (Stage
    6/a future frozen-document amendment may introduce a distinct value; inventing
    one here would be silent policy-making, which Revision 5's process explicitly
    prohibits) -- but its `reason` text distinguishes the two causes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import PENDING, EntryOrder
from stock_analyzer.sandbox.exp005.domain.admission import (
    ACCEPTED,
    CONVERTED,
    NO_CAPACITY,
    RELEASED,
    RESERVED,
    PortfolioAdmission,
    SlotReservation,
)
from stock_analyzer.sandbox.exp005.infrastructure.repository import AdmissionConflictError, PortfolioRepository
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


class AdmissionValidationError(ValueError):
    """Raised when the caller-supplied order does not match the candidate/date it
    is supposedly for -- checked BEFORE the transaction opens, so a bug in the
    caller (Stage 7) can never bind an admission to the wrong order."""


class AdmissionIntegrityError(RuntimeError):
    """Raised when an existing ACCEPTED admission is missing its reservation or
    order -- this is a data-integrity failure to investigate, never a state to
    silently treat as a successful idempotent no-op."""


class CashAvailabilityProvider(Protocol):
    """Required collaborator -- there is no default implementation, so a caller
    cannot accidentally wire AdmissionTransactionService without a real,
    ledger-backed cash source (Stage 6). Returns available UNRESERVED cash, in
    money_units (domain/units.py)."""

    def available_unreserved_cash_units(self) -> int: ...


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
        slot_budget_units: int,
        cash_provider: CashAvailabilityProvider,
    ) -> None:
        self._conn = conn
        self._portfolio_repo = portfolio_repo
        self._sandbox_repo = sandbox_repo
        self._replay_id = replay_id
        self._max_slots = max_slots
        self._slot_budget_units = slot_budget_units
        self._cash_provider = cash_provider

    def admit_candidate(self, candidate: RankedCandidate, as_of_date: date, order: EntryOrder) -> AdmissionResult:
        """`order` is the fully-constructed EntryOrder to persist IF admitted --
        built by the caller from the candidate exactly as
        CandidateService._create_entry_order already does. This method owns only
        the capacity decision and its atomic persistence, not order-construction
        policy -- but it DOES validate the order actually belongs to this candidate
        and date before touching the database.

        admission_id == candidate.candidate_id (Section 8.2): a candidate can have
        at most one admission decision, ever.
        """

        self._validate_order_matches_candidate(candidate, as_of_date, order)
        admission_id = candidate.candidate_id

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing_admission = self._portfolio_repo.get_admission(admission_id)
            if existing_admission is not None:
                result = self._resolve_existing_admission(existing_admission, candidate, as_of_date, order)
                self._conn.execute("COMMIT")
                return result

            occupied_slots = self._count_occupied_slots()
            has_slot = occupied_slots < self._max_slots
            available_cash_units = self._cash_provider.available_unreserved_cash_units() if has_slot else 0
            has_cash = available_cash_units >= self._slot_budget_units
            now = datetime.now(timezone.utc)

            if not (has_slot and has_cash):
                if not has_slot:
                    reason = f"{occupied_slots}/{self._max_slots} slots occupied"
                else:
                    reason = (
                        f"insufficient unreserved cash: available={available_cash_units}, "
                        f"required={self._slot_budget_units} (money_units)"
                    )
                admission = PortfolioAdmission(
                    admission_id=admission_id,
                    replay_id=self._replay_id,
                    candidate_id=candidate.candidate_id,
                    symbol=candidate.symbol,
                    as_of_date=as_of_date,
                    decision=NO_CAPACITY,
                    rank_at_admission=candidate.daily_rank,
                    slot_budget_units=None,
                    reason=reason,
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
                slot_budget_units=self._slot_budget_units,
                reason=None,
                created_at=now,
            )
            reservation = SlotReservation(
                reservation_id=SlotReservation.make_id(admission_id),
                replay_id=self._replay_id,
                admission_id=admission_id,
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                reserved_amount_units=self._slot_budget_units,
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

    def _resolve_existing_admission(
        self,
        existing_admission: PortfolioAdmission,
        candidate: RankedCandidate,
        as_of_date: date,
        order: EntryOrder,
    ) -> AdmissionResult:
        """Called with the write lock already held. Compares the request's full
        logical content against what is persisted; a NO_CAPACITY admission is never
        re-decided into ACCEPTED here, regardless of current capacity."""

        if (
            existing_admission.candidate_id != candidate.candidate_id
            or existing_admission.symbol != candidate.symbol
            or existing_admission.as_of_date != as_of_date
            or existing_admission.rank_at_admission != candidate.daily_rank
            or existing_admission.replay_id != self._replay_id
        ):
            raise AdmissionConflictError(
                f"admission {existing_admission.admission_id} already exists with different "
                f"candidate/session content than requested now -- existing={existing_admission!r}, "
                f"requested candidate={candidate!r}, as_of_date={as_of_date!r}."
            )

        if existing_admission.decision != ACCEPTED:
            return AdmissionResult(existing_admission, None, None, created=False)

        reservation = self._portfolio_repo.get_reservation_for_admission(existing_admission.admission_id)
        existing_order = self._sandbox_repo.get_entry_order_by_candidate(existing_admission.admission_id)
        if reservation is None or existing_order is None:
            raise AdmissionIntegrityError(
                f"admission {existing_admission.admission_id} is ACCEPTED but is missing its "
                f"reservation ({reservation!r}) or order ({existing_order!r}) -- this is a data-"
                "integrity failure, not a resumable state."
            )

        if not self._orders_match_immutable_fields(existing_order, order):
            raise AdmissionConflictError(
                f"admission {existing_admission.admission_id} already has an order with different "
                f"immutable content than the one supplied on this retry -- existing="
                f"{existing_order!r}, supplied={order!r}. A retry must supply the identical order; "
                "this looks like a caller bug (e.g. re-deriving max_entry_price/valid_until from "
                "since-changed inputs) rather than a safe resume."
            )

        expected_reservation_id = SlotReservation.make_id(existing_admission.admission_id)
        if (
            reservation.reservation_id != expected_reservation_id
            or reservation.replay_id != self._replay_id
            or reservation.admission_id != existing_admission.admission_id
            or reservation.candidate_id != candidate.candidate_id
            or reservation.symbol != candidate.symbol
            or reservation.reserved_amount_units != self._slot_budget_units
            or reservation.status not in (RESERVED, CONVERTED, RELEASED)
        ):
            raise AdmissionConflictError(
                f"admission {existing_admission.admission_id}'s persisted reservation does not match "
                f"the content/configuration expected on this retry -- persisted={reservation!r}, "
                f"expected admission_id={existing_admission.admission_id!r}, candidate_id="
                f"{candidate.candidate_id!r}, symbol={candidate.symbol!r}, "
                f"slot_budget_units={self._slot_budget_units!r}."
            )
        return AdmissionResult(existing_admission, reservation, existing_order, created=False)

    @staticmethod
    def _orders_match_immutable_fields(existing: EntryOrder, supplied: EntryOrder) -> bool:
        """Compares only the entry-time facts that are set once and never changed
        (order_id, candidate_id, symbol, signal_date, created_date, valid_until,
        max_entry_price) -- deliberately NOT the mutable lifecycle fields (status,
        fill_date, fill_price, fill_reason, no_fill_reason), which legitimately
        diverge between a freshly-constructed order (always PENDING) and one that
        has since been filled/expired by EntryService on a genuine resume."""

        return (
            existing.order_id == supplied.order_id
            and existing.candidate_id == supplied.candidate_id
            and existing.symbol == supplied.symbol
            and existing.signal_date == supplied.signal_date
            and existing.created_date == supplied.created_date
            and existing.valid_until == supplied.valid_until
            and existing.max_entry_price == supplied.max_entry_price
        )

    def _count_occupied_slots(self) -> int:
        """Section 8.3's invariant: count(open positions) + count(RESERVED
        reservations) <= max_slots. Both counts are read inside the same BEGIN
        IMMEDIATE transaction that will (if capacity allows) also write the new
        reservation -- see the module docstring on why this ordering matters."""

        reserved = len(self._portfolio_repo.list_active_reservations(self._replay_id))
        open_positions = len(self._sandbox_repo.get_open_positions())
        return reserved + open_positions

    @staticmethod
    def _validate_order_matches_candidate(candidate: RankedCandidate, as_of_date: date, order: EntryOrder) -> None:
        expected_order_id = EntryOrder.make_id(candidate.candidate_id)
        if order.order_id != expected_order_id:
            raise AdmissionValidationError(
                f"order.order_id={order.order_id!r} does not match the deterministic id expected "
                f"from candidate_id ({expected_order_id!r})"
            )
        if order.candidate_id != candidate.candidate_id:
            raise AdmissionValidationError(
                f"order.candidate_id={order.candidate_id!r} != candidate.candidate_id={candidate.candidate_id!r}"
            )
        if order.symbol != candidate.symbol:
            raise AdmissionValidationError(f"order.symbol={order.symbol!r} != candidate.symbol={candidate.symbol!r}")
        if order.signal_date != as_of_date:
            raise AdmissionValidationError(
                f"order.signal_date={order.signal_date!r} != as_of_date={as_of_date!r}"
            )
        if (
            order.status != PENDING
            or order.fill_date is not None
            or order.fill_price is not None
            or order.fill_reason is not None
            or order.no_fill_reason is not None
        ):
            raise AdmissionValidationError(
                f"order={order!r} does not have the initial lifecycle state expected of a freshly-"
                "constructed order (status=PENDING, no fill_date/fill_price/fill_reason/"
                "no_fill_reason) -- admit_candidate must always be called with a newly-built order, "
                "never one already carrying a resolved outcome."
            )
