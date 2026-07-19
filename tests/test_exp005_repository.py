"""Tests for EXP-005's repository layer (Revision 5, Stage 3)."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.sandbox.exp005.domain.admission import (
    ACCEPTED,
    CONVERTED,
    NO_CAPACITY,
    RELEASED,
    RESERVED,
    PortfolioAdmission,
    SlotReservation,
)
from stock_analyzer.sandbox.exp005.domain.equity_snapshot import PortfolioEquitySnapshot
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.infrastructure.repository import AdmissionConflictError, PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db

NOW = datetime.now(timezone.utc)


def _insert_entry_order(conn: sqlite3.Connection, candidate_id: str, symbol: str) -> None:
    conn.execute(
        "INSERT INTO entry_orders (order_id, candidate_id, symbol, signal_date, created_date, valid_until, "
        " max_entry_price, status, fill_date, fill_price, fill_reason, no_fill_reason, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"{candidate_id}:order", candidate_id, symbol, "2026-01-05", "2026-01-05", "2026-01-07",
            101.0, "FILLED", "2026-01-06", 100.5, "next_day_open<=max_entry_price", None, NOW.isoformat(), NOW.isoformat(),
        ),
    )


def _insert_position(conn: sqlite3.Connection, position_id: str, symbol: str, candidate_id: str, entry_date: date) -> None:
    conn.execute(
        "INSERT INTO virtual_positions (position_id, symbol, candidate_id, order_id, signal_date, entry_date, "
        " entry_price, quantity, initial_rank, initial_model_score, signal_close, max_entry_price, "
        " initial_adv_quintile, initial_market_regime, status, current_holding_day_count, current_close, "
        " unrealized_return, mfe, mae, target_price, planned_time_exit_date, exit_date, exit_price, "
        " exit_reason, realized_return, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            position_id, symbol, candidate_id, f"{candidate_id}:order", "2026-01-05", entry_date.isoformat(),
            100.5, 99.826, 1, 5.0, 100.0, 101.0, "adv_q3", "Bull_Normal", "OPEN", 1, 100.5, 0.0, 0.0, 0.0,
            120.6, "2026-02-03", None, None, None, None, NOW.isoformat(), NOW.isoformat(),
        ),
    )


@pytest.fixture
def repo() -> PortfolioRepository:
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
    for i, symbol in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        conn.execute(
            "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
            " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
            " exclusion_reason, adv_quintile, market_regime, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"c{i}", "run-1", "2026-01-05", symbol, i + 1, 5.0, 100.0, 2.0, 101.0, 1, 1, None, "adv_q3", "Bull_Normal", NOW.isoformat()),
        )
    # Backing entry_orders/virtual_positions rows for c0's executions -- executions
    # FK-reference these tables (order_id, position_id), so tests that append
    # executions for c0 need real rows to point at.
    _insert_entry_order(conn, "c0", "AAA")
    for d in (6, 7, 8, 10):
        _insert_position(conn, f"AAA:2026-01-{d:02d}", "AAA", "c0", date(2026, 1, d))
    conn.commit()
    return PortfolioRepository(conn)


def _admission(candidate_id: str, symbol: str, rank: int, *, decision: str = ACCEPTED, slot_budget=10_000.0) -> PortfolioAdmission:
    return PortfolioAdmission(
        admission_id=candidate_id,
        replay_id="replay-1",
        candidate_id=candidate_id,
        symbol=symbol,
        as_of_date=date(2026, 1, 5),
        decision=decision,
        rank_at_admission=rank,
        slot_budget=slot_budget if decision == ACCEPTED else None,
        reason=None if decision == ACCEPTED else "10/10 slots reserved",
        created_at=NOW,
    )


def _reservation(admission_id: str, candidate_id: str, symbol: str) -> SlotReservation:
    return SlotReservation(
        reservation_id=f"{admission_id}:reservation",
        replay_id="replay-1",
        admission_id=admission_id,
        candidate_id=candidate_id,
        symbol=symbol,
        reserved_amount=10_000.0,
        status=RESERVED,
        created_at=NOW,
    )


def _execution(candidate_id: str, symbol: str, side: str, execution_date: date, **overrides) -> Execution:
    base = dict(
        execution_id=f"{candidate_id}:{side}:{execution_date.isoformat()}",
        replay_id="replay-1",
        variant_id="B",
        control_seed=None,
        order_id=f"{candidate_id}:order" if side == BUY else None,
        candidate_id=candidate_id,
        position_id=f"{symbol}:{execution_date.isoformat()}",
        symbol=symbol,
        side=side,
        decision_date=execution_date,
        execution_date=execution_date,
        raw_market_fill_price=100.1234,
        effective_fill_price=100.1734 if side == BUY else 100.0734,
        quantity=99.826,
        gross_notional=9998.14,
        commission=1.0,
        slippage_rate=0.0005,
        slippage_cost=5.0,
        net_cash_flow=-9999.14 if side == BUY else 9997.14,
        fill_reason="FILLED_AT_OPEN" if side == BUY else "SELL_TARGET",
        market_data_snapshot_id="snap-1",
        created_at=NOW,
    )
    base.update(overrides)
    return Execution(**base)


def _snapshot(as_of_date: date, **overrides) -> PortfolioEquitySnapshot:
    base = dict(
        snapshot_id=f"replay-1:{as_of_date.isoformat()}",
        replay_id="replay-1",
        as_of_date=as_of_date,
        cash=90_000.0,
        reserved_capital=10_000.0,
        open_position_market_value=0.0,
        total_equity=100_000.0,
        open_position_count=0,
        reserved_order_count=1,
        cumulative_commissions=1.0,
        cumulative_slippage_cost=5.0,
        created_at=NOW,
    )
    base.update(overrides)
    return PortfolioEquitySnapshot(**base)


# ------------------------------------------------------------------------ admissions


def test_insert_and_get_admission_round_trips(repo: PortfolioRepository):
    admission = _admission("c0", "AAA", 1)
    assert repo.insert_admission(admission) is True
    fetched = repo.get_admission("c0")
    assert fetched == admission


def test_insert_admission_identical_repeat_is_noop(repo: PortfolioRepository):
    admission = _admission("c0", "AAA", 1)
    assert repo.insert_admission(admission) is True
    assert repo.insert_admission(replace(admission)) is False


def test_insert_admission_conflicting_repeat_raises(repo: PortfolioRepository):
    admission = _admission("c0", "AAA", 1)
    repo.insert_admission(admission)
    conflicting = replace(admission, rank_at_admission=2)
    with pytest.raises(AdmissionConflictError):
        repo.insert_admission(conflicting)


def test_list_admissions_for_session_orders_by_rank_regardless_of_insert_order(repo: PortfolioRepository):
    admissions = [_admission(f"c{i}", s, i + 1) for i, s in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"])]
    shuffled = admissions[:]
    random.Random(42).shuffle(shuffled)
    for a in shuffled:
        repo.insert_admission(a)

    result = repo.list_admissions_for_session("replay-1", date(2026, 1, 5))
    assert [a.rank_at_admission for a in result] == [1, 2, 3, 4, 5]


def test_no_capacity_admission_round_trips_with_null_slot_budget(repo: PortfolioRepository):
    admission = _admission("c0", "AAA", 1, decision=NO_CAPACITY)
    repo.insert_admission(admission)
    fetched = repo.get_admission("c0")
    assert fetched.decision == NO_CAPACITY
    assert fetched.slot_budget is None


# ------------------------------------------------------------------------ reservations


def test_insert_and_get_reservation_round_trips(repo: PortfolioRepository):
    repo.insert_admission(_admission("c0", "AAA", 1))
    reservation = _reservation("c0", "c0", "AAA")
    assert repo.insert_reservation(reservation) is True
    fetched = repo.get_reservation_for_admission("c0")
    assert fetched == reservation


def test_insert_reservation_identical_repeat_is_noop(repo: PortfolioRepository):
    repo.insert_admission(_admission("c0", "AAA", 1))
    reservation = _reservation("c0", "c0", "AAA")
    repo.insert_reservation(reservation)
    assert repo.insert_reservation(replace(reservation)) is False


def test_insert_reservation_conflicting_repeat_raises(repo: PortfolioRepository):
    repo.insert_admission(_admission("c0", "AAA", 1))
    reservation = _reservation("c0", "c0", "AAA")
    repo.insert_reservation(reservation)
    with pytest.raises(AdmissionConflictError):
        repo.insert_reservation(replace(reservation, reserved_amount=5_000.0))


def test_list_active_reservations_returns_only_reserved_in_deterministic_order(repo: PortfolioRepository):
    ids = ["c0", "c1", "c2"]
    for i, cid in enumerate(ids):
        repo.insert_admission(_admission(cid, ["AAA", "BBB", "CCC"][i], i + 1))
        repo.insert_reservation(_reservation(cid, cid, ["AAA", "BBB", "CCC"][i]))
    repo.update_reservation_status("c1:reservation", RELEASED, NOW)

    active = repo.list_active_reservations("replay-1")
    assert [r.candidate_id for r in active] == ["c0", "c2"]


def test_update_reservation_status_transitions_and_sets_resolved_at(repo: PortfolioRepository):
    repo.insert_admission(_admission("c0", "AAA", 1))
    repo.insert_reservation(_reservation("c0", "c0", "AAA"))
    repo.update_reservation_status("c0:reservation", CONVERTED, NOW)
    fetched = repo.get_reservation_for_admission("c0")
    assert fetched.status == CONVERTED
    assert fetched.resolved_at is not None


def test_update_reservation_status_only_transitions_from_reserved(repo: PortfolioRepository):
    repo.insert_admission(_admission("c0", "AAA", 1))
    repo.insert_reservation(_reservation("c0", "c0", "AAA"))
    repo.update_reservation_status("c0:reservation", CONVERTED, NOW)
    # A second transition attempt matches no RESERVED row -- silently affects zero
    # rows rather than corrupting an already-resolved reservation.
    repo.update_reservation_status("c0:reservation", RELEASED, NOW)
    fetched = repo.get_reservation_for_admission("c0")
    assert fetched.status == CONVERTED  # unchanged -- the second call had no target row


def test_update_reservation_status_rejects_invalid_target():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    repo = PortfolioRepository(conn)
    with pytest.raises(ValueError, match="CONVERTED or RELEASED"):
        repo.update_reservation_status("x", "RESERVED", NOW)


# -------------------------------------------------------------------------- executions


def test_append_and_get_execution_round_trips_exactly(repo: PortfolioRepository):
    execution = _execution("c0", "AAA", BUY, date(2026, 1, 6))
    assert repo.append_execution(execution) is True
    fetched = repo.get_execution(execution.execution_id)
    assert fetched.raw_market_fill_price == execution.raw_market_fill_price
    assert fetched.effective_fill_price == execution.effective_fill_price
    assert fetched.quantity == execution.quantity
    assert fetched.commission == execution.commission
    assert fetched.slippage_cost == execution.slippage_cost
    assert fetched.gross_notional == execution.gross_notional
    assert fetched.net_cash_flow == execution.net_cash_flow


def test_append_execution_identical_repeat_is_noop(repo: PortfolioRepository):
    execution = _execution("c0", "AAA", BUY, date(2026, 1, 6))
    repo.append_execution(execution)
    assert repo.append_execution(replace(execution)) is False


def test_append_execution_conflicting_repeat_raises(repo: PortfolioRepository):
    execution = _execution("c0", "AAA", BUY, date(2026, 1, 6))
    repo.append_execution(execution)
    with pytest.raises(AdmissionConflictError):
        repo.append_execution(replace(execution, quantity=50.0))


def test_list_executions_for_order_deterministic_regardless_of_insert_order(repo: PortfolioRepository):
    dates = [date(2026, 1, d) for d in (8, 6, 7)]
    shuffled = dates[:]
    random.Random(7).shuffle(shuffled)
    for d in shuffled:
        # simulate multiple attempts on the same order across sessions -- each needs
        # a distinct execution_id, so vary by execution_date only (same order_id).
        repo.append_execution(
            _execution("c0", "AAA", BUY, d, execution_id=f"c0:attempt:{d.isoformat()}", order_id="c0:order")
        )
    result = repo.list_executions_for_order("c0:order")
    assert [e.execution_date for e in result] == sorted(dates)


def test_list_executions_for_position_and_experiment(repo: PortfolioRepository):
    buy = _execution("c0", "AAA", BUY, date(2026, 1, 6))
    sell = _execution("c0", "AAA", SELL, date(2026, 1, 10), position_id=buy.position_id)
    repo.append_execution(buy)
    repo.append_execution(sell)

    by_position = repo.list_executions_for_position(buy.position_id)
    assert [e.side for e in by_position] == [BUY, SELL]

    by_experiment = repo.list_executions_for_experiment("replay-1")
    assert len(by_experiment) == 2


# ---------------------------------------------------------------------- equity snapshots


def test_append_and_get_equity_snapshot_round_trips(repo: PortfolioRepository):
    snapshot = _snapshot(date(2026, 1, 5))
    assert repo.append_equity_snapshot(snapshot) is True
    fetched = repo.get_equity_snapshot("replay-1", date(2026, 1, 5))
    assert fetched == snapshot


def test_append_equity_snapshot_identical_repeat_is_noop(repo: PortfolioRepository):
    snapshot = _snapshot(date(2026, 1, 5))
    repo.append_equity_snapshot(snapshot)
    assert repo.append_equity_snapshot(replace(snapshot)) is False


def test_append_equity_snapshot_conflicting_repeat_raises(repo: PortfolioRepository):
    snapshot = _snapshot(date(2026, 1, 5))
    repo.append_equity_snapshot(snapshot)
    with pytest.raises(AdmissionConflictError):
        repo.append_equity_snapshot(replace(snapshot, cash=50_000.0))


def test_list_equity_snapshots_deterministic_regardless_of_insert_order(repo: PortfolioRepository):
    dates = [date(2026, 1, d) for d in (7, 5, 6)]
    shuffled = dates[:]
    random.Random(3).shuffle(shuffled)
    for d in shuffled:
        repo.append_equity_snapshot(_snapshot(d, snapshot_id=f"replay-1:{d.isoformat()}"))
    result = repo.list_equity_snapshots("replay-1")
    assert [s.as_of_date for s in result] == sorted(dates)


# ------------------------------------------------------------------- architecture check


def test_no_generic_mutation_api_exposed():
    """Section 3 (Stage 3): no save(table, dict)/update_anything() API. The only
    UPDATE-capable method permitted is the one narrow, frozen-design-specified
    reservation transition."""

    public_methods = {name for name in dir(PortfolioRepository) if not name.startswith("_")}
    forbidden_substrings = ("save", "delete", "remove")
    for name in public_methods:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"{name} looks like a generic mutation method"
    update_methods = {name for name in public_methods if name.startswith("update_")}
    assert update_methods == {"update_reservation_status"}
