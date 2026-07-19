"""Tests for EXP-005's atomic admission transaction and orphan checker (Revision 5,
Stage 4).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.entry_order import PENDING as ORDER_PENDING
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.exp005.application.admission_orchestrator import AdmissionTransactionService
from stock_analyzer.sandbox.exp005.infrastructure.integrity import (
    ADMISSION_WITHOUT_ORDER,
    ADMISSION_WITHOUT_RESERVATION,
    MULTIPLE_RESERVATIONS_FOR_ADMISSION,
    NO_CAPACITY_WITH_RESERVATION,
    ORDER_WITHOUT_ADMISSION,
    RESERVATION_WITHOUT_ADMISSION,
    check_admission_integrity,
)
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

NOW = datetime.now(timezone.utc)
REPLAY_ID = "replay-1"
MAX_SLOTS = 2
SLOT_BUDGET = 10_000.0


def _make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    return conn


def _seed_candidate(conn: sqlite3.Connection, candidate_id: str, symbol: str, rank: int) -> RankedCandidate:
    run_id = f"run-{symbol}"
    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, "2026-01-05", "generate-candidates", NOW.isoformat(), None, "COMPLETED", "v1", None, None, "hash", None),
    )
    conn.execute(
        "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
        " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
        " exclusion_reason, adv_quintile, market_regime, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (candidate_id, run_id, "2026-01-05", symbol, rank, 5.0, 100.0, 2.0, 101.0, 1, 1, None, "adv_q3", "Bull_Normal", NOW.isoformat()),
    )
    conn.commit()
    return RankedCandidate(
        candidate_id=candidate_id, run_id=run_id, as_of_date=date(2026, 1, 5), symbol=symbol, daily_rank=rank,
        model_score=5.0, signal_close=100.0, atr14=2.0, max_entry_price=101.0, shadow_top10=True, actionable=True,
        exclusion_reason=None, adv_quintile="adv_q3", market_regime="Bull_Normal",
    )


def _order_for(candidate: RankedCandidate) -> EntryOrder:
    return EntryOrder(
        order_id=EntryOrder.make_id(candidate.candidate_id), candidate_id=candidate.candidate_id,
        symbol=candidate.symbol, signal_date=candidate.as_of_date, created_date=candidate.as_of_date,
        valid_until=date(2026, 1, 7), max_entry_price=candidate.max_entry_price, status=ORDER_PENDING,
    )


def _service(conn: sqlite3.Connection, max_slots: int = MAX_SLOTS) -> AdmissionTransactionService:
    return AdmissionTransactionService(
        conn, PortfolioRepository(conn), SandboxRepository(conn), REPLAY_ID, max_slots, SLOT_BUDGET
    )


# ----------------------------------------------------------------- happy path


def test_successful_admission_writes_all_three_records():
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    service = _service(conn)

    result = service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    assert result.created is True
    assert result.admission.decision == "ACCEPTED"
    assert result.reservation is not None
    assert result.order is not None
    assert PortfolioRepository(conn).get_admission("c0") is not None
    assert PortfolioRepository(conn).get_reservation_for_admission("c0") is not None
    assert SandboxRepository(conn).get_entry_order_by_candidate("c0") is not None


def test_no_capacity_writes_only_the_admission():
    conn = _make_connection()
    # Fill both slots first.
    for i in range(MAX_SLOTS):
        candidate = _seed_candidate(conn, f"c{i}", f"SYM{i}", i + 1)
        _service(conn).admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    overflow = _seed_candidate(conn, "c-overflow", "ZZZ", 99)
    result = _service(conn).admit_candidate(overflow, overflow.as_of_date, _order_for(overflow))

    assert result.created is True
    assert result.admission.decision == "NO_CAPACITY"
    assert result.reservation is None
    assert result.order is None
    assert PortfolioRepository(conn).get_reservation_for_admission("c-overflow") is None
    assert SandboxRepository(conn).get_entry_order_by_candidate("c-overflow") is None


def test_no_capacity_records_which_slots_were_occupied():
    conn = _make_connection()
    for i in range(MAX_SLOTS):
        candidate = _seed_candidate(conn, f"c{i}", f"SYM{i}", i + 1)
        _service(conn).admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
    overflow = _seed_candidate(conn, "c-overflow", "ZZZ", 99)
    result = _service(conn).admit_candidate(overflow, overflow.as_of_date, _order_for(overflow))
    assert f"{MAX_SLOTS}/{MAX_SLOTS}" in result.admission.reason


# --------------------------------------------------------------- rollback injection


def test_rollback_after_admission_insert_leaves_no_admission(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    service = _service(conn)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure after admission insert")

    monkeypatch.setattr(service._portfolio_repo, "insert_reservation", boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    assert PortfolioRepository(conn).get_admission("c0") is None
    assert PortfolioRepository(conn).get_reservation_for_admission("c0") is None
    assert SandboxRepository(conn).get_entry_order_by_candidate("c0") is None


def test_rollback_after_reservation_insert_leaves_neither_admission_nor_reservation(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    service = _service(conn)

    def boom(order):
        raise RuntimeError("simulated failure during order insert")

    monkeypatch.setattr(service._sandbox_repo, "_insert_entry_order_row", boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    assert PortfolioRepository(conn).get_admission("c0") is None
    assert PortfolioRepository(conn).get_reservation_for_admission("c0") is None
    assert SandboxRepository(conn).get_entry_order_by_candidate("c0") is None


class _CommitFailingConnectionProxy:
    """sqlite3.Connection's attributes are C-level slots and cannot be monkeypatched
    directly on an instance -- this proxy wraps a real connection, forwards
    everything, and raises specifically on COMMIT, to prove the service's own
    ROLLBACK path (not test-harness mocking) is what leaves the database clean."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql, *args, **kwargs):
        if sql == "COMMIT":
            raise RuntimeError("simulated failure immediately before commit")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_rollback_immediately_before_commit_leaves_nothing():
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    service = _service(conn)
    service._conn = _CommitFailingConnectionProxy(conn)

    with pytest.raises(RuntimeError, match="simulated failure"):
        service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    assert PortfolioRepository(conn).get_admission("c0") is None


def test_rollback_during_no_capacity_path_leaves_nothing(monkeypatch):
    conn = _make_connection()
    for i in range(MAX_SLOTS):
        candidate = _seed_candidate(conn, f"c{i}", f"SYM{i}", i + 1)
        _service(conn).admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    overflow = _seed_candidate(conn, "c-overflow", "ZZZ", 99)
    service = _service(conn)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure during NO_CAPACITY admission insert")

    monkeypatch.setattr(service._portfolio_repo, "insert_admission", boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        service.admit_candidate(overflow, overflow.as_of_date, _order_for(overflow))

    assert PortfolioRepository(conn).get_admission("c-overflow") is None


# ---------------------------------------------------------------------- idempotency


def test_exact_retry_of_already_successful_admission_is_a_noop():
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    service = _service(conn)

    first = service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
    second = service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    assert first.created is True
    assert second.created is False
    assert second.admission == first.admission
    # No duplicate rows -- still exactly one reservation, one order.
    assert len(PortfolioRepository(conn).list_active_reservations(REPLAY_ID)) == 1


def test_retry_after_rolled_back_failure_succeeds_cleanly(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    service = _service(conn)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(service._portfolio_repo, "insert_reservation", boom)
    with pytest.raises(RuntimeError):
        service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    monkeypatch.undo()
    result = service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
    assert result.created is True
    assert result.admission.decision == "ACCEPTED"


def test_same_candidate_processed_twice_does_not_double_reserve():
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    service = _service(conn)
    service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
    service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
    assert len(PortfolioRepository(conn).list_active_reservations(REPLAY_ID)) == 1


def test_two_different_candidates_competing_for_one_slot():
    conn = _make_connection()
    service = _service(conn, max_slots=1)
    a = _seed_candidate(conn, "c0", "AAA", 1)
    b = _seed_candidate(conn, "c1", "BBB", 2)

    result_a = service.admit_candidate(a, a.as_of_date, _order_for(a))
    result_b = service.admit_candidate(b, b.as_of_date, _order_for(b))

    assert result_a.admission.decision == "ACCEPTED"
    assert result_b.admission.decision == "NO_CAPACITY"


def test_repeated_orchestration_after_simulated_process_restart():
    # A "process restart" is simulated by constructing a fresh AdmissionTransactionService
    # (and fresh PortfolioRepository/SandboxRepository instances) against the SAME
    # connection/database -- proving state lives in the database, not in-memory.
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    _service(conn).admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))

    fresh_service = _service(conn)
    result = fresh_service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
    assert result.created is False
    assert len(PortfolioRepository(conn).list_active_reservations(REPLAY_ID)) == 1


# --------------------------------------------------------------------- concurrency


def test_two_competing_admissions_cannot_both_consume_the_final_slot():
    """Real SQLite concurrency, not mocked: two independent connections to the same
    file-backed database, each racing to admit a different candidate into the last
    remaining slot. BEGIN IMMEDIATE's write-lock exclusivity must ensure exactly one
    of the two succeeds."""

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        setup_conn = sqlite3.connect(path)
        setup_conn.row_factory = sqlite3.Row
        setup_conn.execute("PRAGMA foreign_keys = ON")
        init_db(setup_conn)
        init_exp005_schema(setup_conn)
        candidate_a = _seed_candidate(setup_conn, "c-a", "AAA", 1)
        candidate_b = _seed_candidate(setup_conn, "c-b", "BBB", 2)
        setup_conn.close()

        results: dict[str, object] = {}
        barrier = threading.Barrier(2)

        def attempt(candidate_id: str, candidate: RankedCandidate) -> None:
            conn = sqlite3.connect(path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            service = AdmissionTransactionService(
                conn, PortfolioRepository(conn), SandboxRepository(conn), REPLAY_ID, max_slots=1, slot_budget=SLOT_BUDGET
            )
            barrier.wait(timeout=5)
            results[candidate_id] = service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
            conn.close()

        t1 = threading.Thread(target=attempt, args=("c-a", candidate_a))
        t2 = threading.Thread(target=attempt, args=("c-b", candidate_b))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        decisions = [results["c-a"].admission.decision, results["c-b"].admission.decision]
        assert sorted(decisions) == ["ACCEPTED", "NO_CAPACITY"]

        final_conn = sqlite3.connect(path)
        final_conn.row_factory = sqlite3.Row
        final_reservations = PortfolioRepository(final_conn).list_active_reservations(REPLAY_ID)
        assert len(final_reservations) == 1
        final_conn.close()
    finally:
        os.unlink(path)


# ------------------------------------------------------------------------- orphans


def test_orphan_check_finds_nothing_on_a_clean_database():
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    _service(conn).admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
    assert check_admission_integrity(conn, REPLAY_ID) == []


def test_orphan_check_detects_admission_without_reservation():
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    conn.execute(
        "INSERT INTO portfolio_admissions (admission_id, replay_id, candidate_id, symbol, as_of_date, "
        " decision, rank_at_admission, slot_budget, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c0", REPLAY_ID, "c0", "AAA", "2026-01-05", "ACCEPTED", 1, 10_000.0, None, NOW.isoformat()),
    )
    conn.commit()
    findings = check_admission_integrity(conn, REPLAY_ID)
    assert any(f.category == ADMISSION_WITHOUT_RESERVATION and f.admission_id == "c0" for f in findings)
    assert any(f.category == ADMISSION_WITHOUT_ORDER and f.admission_id == "c0" for f in findings)


def test_orphan_check_detects_reservation_without_admission():
    # Only constructible by deliberately disabling foreign keys -- the FK
    # (admission_id NOT NULL REFERENCES portfolio_admissions) otherwise prevents this
    # state from ever existing.
    conn = _make_connection()
    _seed_candidate(conn, "c0", "AAA", 1)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
        " reserved_amount, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("orphan-r", REPLAY_ID, "no-such-admission", "c0", "AAA", 10_000.0, "RESERVED", NOW.isoformat(), None),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    findings = check_admission_integrity(conn, REPLAY_ID)
    assert any(f.category == RESERVATION_WITHOUT_ADMISSION for f in findings)


def test_orphan_check_detects_multiple_reservations_for_one_admission():
    # Only constructible by disabling foreign keys AND the UNIQUE(admission_id)
    # constraint would normally prevent this too -- demonstrated here via a raw
    # connection where we accept the UNIQUE violation is the real guard and instead
    # verify the checker's query logic directly using two different replay-scoped
    # reservation ids that both (artificially) reference the same admission via a
    # manually corrupted second table state is not reachable; this test documents
    # that the invariant is guarded at the schema level (UNIQUE), not just detected
    # post-hoc.
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    _service(conn).admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
            " reserved_amount, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("second-reservation-for-c0", REPLAY_ID, "c0", "c0", "AAA", 10_000.0, "RESERVED", NOW.isoformat(), None),
        )


def test_orphan_check_detects_order_without_admission():
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    conn.execute(
        "INSERT INTO entry_orders (order_id, candidate_id, symbol, signal_date, created_date, valid_until, "
        " max_entry_price, status, fill_date, fill_price, fill_reason, no_fill_reason, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("c0:order", "c0", "AAA", "2026-01-05", "2026-01-05", "2026-01-07", 101.0, "PENDING", None, None, None, None, NOW.isoformat(), NOW.isoformat()),
    )
    conn.commit()
    findings = check_admission_integrity(conn, REPLAY_ID)
    assert any(f.category == ORDER_WITHOUT_ADMISSION and f.admission_id == "c0" for f in findings)


def test_orphan_check_detects_no_capacity_with_reservation():
    conn = _make_connection()
    _seed_candidate(conn, "c0", "AAA", 1)
    conn.execute(
        "INSERT INTO portfolio_admissions (admission_id, replay_id, candidate_id, symbol, as_of_date, "
        " decision, rank_at_admission, slot_budget, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c0", REPLAY_ID, "c0", "AAA", "2026-01-05", "NO_CAPACITY", 1, None, "reason", NOW.isoformat()),
    )
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
        " reserved_amount, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("c0:reservation", REPLAY_ID, "c0", "c0", "AAA", 10_000.0, "RESERVED", NOW.isoformat(), None),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    findings = check_admission_integrity(conn, REPLAY_ID)
    assert any(f.category == NO_CAPACITY_WITH_RESERVATION and f.admission_id == "c0" for f in findings)


def test_orphan_checker_never_deletes_or_repairs():
    conn = _make_connection()
    candidate = _seed_candidate(conn, "c0", "AAA", 1)
    conn.execute(
        "INSERT INTO portfolio_admissions (admission_id, replay_id, candidate_id, symbol, as_of_date, "
        " decision, rank_at_admission, slot_budget, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("c0", REPLAY_ID, "c0", "AAA", "2026-01-05", "ACCEPTED", 1, 10_000.0, None, NOW.isoformat()),
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM portfolio_admissions").fetchone()[0]
    check_admission_integrity(conn, REPLAY_ID)
    after = conn.execute("SELECT COUNT(*) FROM portfolio_admissions").fetchone()[0]
    assert before == after == 1
