from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.position import PositionSnapshot, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import BUY_FILLED, HOLD, SELL_TARGET, Recommendation
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.domain.transaction import BUY, VirtualTransaction
from stock_analyzer.sandbox.infrastructure.schema import connect, init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


@pytest.fixture
def repo() -> SandboxRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return SandboxRepository(conn)


def _ensure_run(repo: SandboxRepository, as_of: date) -> None:
    repo.create_run(
        SandboxRun(
            run_id=SandboxRun.make_id(as_of, "generate-candidates"),
            as_of_date=as_of,
            command="generate-candidates",
            started_at=datetime(as_of.year, as_of.month, as_of.day, 21, 0, 0),
            configuration_hash="test-config-hash",
        )
    )


def _make_candidate(as_of: date, symbol: str = "AAA", rank: int = 1) -> RankedCandidate:
    return RankedCandidate(
        candidate_id=RankedCandidate.make_id(as_of, symbol),
        run_id=SandboxRun.make_id(as_of, "generate-candidates"),
        as_of_date=as_of,
        symbol=symbol,
        daily_rank=rank,
        model_score=0.5,
        signal_close=10.0,
        atr14=0.5,
        max_entry_price=10.2,
        shadow_top10=True,
        actionable=True,
        exclusion_reason=None,
        adv_quintile="adv_q1",
        market_regime="Bull_Normal",
    )


def test_init_db_creates_all_tables():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        "sandbox_runs",
        "ranked_candidates",
        "entry_orders",
        "entry_order_attempts",
        "virtual_positions",
        "position_snapshots",
        "recommendations",
        "virtual_transactions",
        "data_quality_events",
    }
    assert expected.issubset(tables)


def test_connect_helper_initializes_schema(tmp_path):
    db_path = tmp_path / "sandbox.db"
    conn = connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "sandbox_runs" in tables
    conn.close()


def test_duplicate_daily_run_is_idempotent(repo: SandboxRepository):
    as_of = date(2026, 6, 15)
    run = SandboxRun(
        run_id=SandboxRun.make_id(as_of, "generate-candidates"),
        as_of_date=as_of,
        command="generate-candidates",
        started_at=datetime(2026, 6, 15, 21, 0, 0),
        configuration_hash="abc123",
    )

    first, created_first = repo.create_run(run)
    second, created_second = repo.create_run(run)

    assert created_first is True
    assert created_second is False
    assert first.run_id == second.run_id
    all_runs = repo._conn.execute("SELECT COUNT(*) FROM sandbox_runs").fetchone()[0]
    assert all_runs == 1


def test_duplicate_candidate_insert_is_ignored(repo: SandboxRepository):
    as_of = date(2026, 6, 15)
    _ensure_run(repo, as_of)
    candidate = _make_candidate(as_of)

    inserted_first = repo.insert_ranked_candidate(candidate)
    inserted_second = repo.insert_ranked_candidate(candidate)

    assert inserted_first is True
    assert inserted_second is False
    rows = repo.get_candidates_for_date(as_of)
    assert len(rows) == 1


def test_duplicate_recommendation_is_prevented(repo: SandboxRepository):
    as_of = date(2026, 6, 15)
    rec = Recommendation(
        recommendation_id=Recommendation.make_id("position", "AAA:2026-06-15", as_of),
        entity_type="position",
        entity_id="AAA:2026-06-15",
        symbol="AAA",
        as_of_date=as_of,
        recommendation=HOLD,
        reason=None,
    )

    first = repo.insert_recommendation(rec)
    second = repo.insert_recommendation(rec)

    assert first is True
    assert second is False
    history = repo.get_recommendations_for_entity("position", "AAA:2026-06-15")
    assert len(history) == 1


def test_duplicate_transaction_is_prevented(repo: SandboxRepository):
    as_of = date(2026, 6, 16)
    _ensure_run(repo, as_of)
    candidate = _make_candidate(as_of)
    repo.insert_ranked_candidate(candidate)
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate.candidate_id),
        candidate_id=candidate.candidate_id,
        symbol="AAA",
        signal_date=as_of,
        created_date=as_of,
        valid_until=date(2026, 6, 18),
        max_entry_price=10.2,
        status="FILLED",
    )
    repo.create_entry_order(order)
    position = VirtualPosition(
        position_id=VirtualPosition.make_id("AAA", as_of),
        symbol="AAA",
        candidate_id=candidate.candidate_id,
        order_id=order.order_id,
        signal_date=as_of,
        entry_date=as_of,
        entry_price=10.1,
        quantity=99.0099,
        initial_rank=1,
        initial_model_score=0.5,
        signal_close=10.0,
        max_entry_price=10.2,
        initial_adv_quintile="adv_q1",
        initial_market_regime="Bull_Normal",
        target_price=12.12,
        planned_time_exit_date=date(2026, 7, 14),
    )
    repo.create_position(position)
    txn = VirtualTransaction(
        transaction_id=VirtualTransaction.make_id(position.position_id, BUY, as_of),
        position_id=position.position_id,
        symbol="AAA",
        transaction_type=BUY,
        transaction_date=as_of,
        price=10.1,
        quantity=99.0099,
        notional=1000.0,
        reason="BUY_FILLED",
    )

    first = repo.insert_transaction(txn)
    second = repo.insert_transaction(txn)

    assert first is True
    assert second is False
    assert len(repo.get_transactions_for_position(position.position_id)) == 1


def test_position_snapshots_are_append_only_and_reconstruct_full_history(repo: SandboxRepository):
    as_of = date(2026, 6, 15)
    _ensure_run(repo, as_of)
    candidate = _make_candidate(as_of)
    repo.insert_ranked_candidate(candidate)
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate.candidate_id),
        candidate_id=candidate.candidate_id,
        symbol="AAA",
        signal_date=as_of,
        created_date=as_of,
        valid_until=date(2026, 6, 17),
        max_entry_price=10.2,
        status="FILLED",
    )
    repo.create_entry_order(order)
    position = VirtualPosition(
        position_id=VirtualPosition.make_id("AAA", date(2026, 6, 16)),
        symbol="AAA",
        candidate_id=candidate.candidate_id,
        order_id=order.order_id,
        signal_date=as_of,
        entry_date=date(2026, 6, 16),
        entry_price=10.1,
        quantity=99.0099,
        initial_rank=1,
        initial_model_score=0.5,
        signal_close=10.0,
        max_entry_price=10.2,
        initial_adv_quintile="adv_q1",
        initial_market_regime="Bull_Normal",
        target_price=12.12,
        planned_time_exit_date=date(2026, 7, 15),
    )
    repo.create_position(position)

    days_and_recs = [
        (date(2026, 6, 16), BUY_FILLED),
        (date(2026, 6, 17), HOLD),
        (date(2026, 6, 18), HOLD),
        (date(2026, 6, 19), SELL_TARGET),
    ]
    for holding_day, (day, rec) in enumerate(days_and_recs, start=1):
        snapshot = PositionSnapshot(
            snapshot_id=PositionSnapshot.make_id(position.position_id, day),
            position_id=position.position_id,
            symbol="AAA",
            as_of_date=day,
            close_price=10.1 + holding_day * 0.1,
            daily_return=0.01,
            cumulative_unrealized_return=0.01 * holding_day,
            holding_day_count=holding_day,
            mfe=0.02 * holding_day,
            mae=-0.01,
            distance_to_target=0.1,
            current_rank=holding_day,
            current_model_score=0.5,
            rank_change_from_entry=0,
            current_adv_quintile="adv_q1",
            current_market_regime="Bull_Normal",
            data_quality_status="OK",
            recommendation=rec,
        )
        repo.insert_position_snapshot(snapshot)

    # Attempting to re-insert an already-recorded day must not overwrite it or add a
    # second row (append-only, idempotent).
    duplicate = PositionSnapshot(
        snapshot_id=PositionSnapshot.make_id(position.position_id, date(2026, 6, 16)),
        position_id=position.position_id,
        symbol="AAA",
        as_of_date=date(2026, 6, 16),
        close_price=999.0,  # would be an obviously wrong overwrite if it succeeded
        daily_return=None,
        cumulative_unrealized_return=None,
        holding_day_count=1,
        mfe=0.0,
        mae=0.0,
        distance_to_target=None,
        current_rank=None,
        current_model_score=None,
        rank_change_from_entry=None,
        current_adv_quintile=None,
        current_market_regime=None,
        data_quality_status="OK",
        recommendation=BUY_FILLED,
    )
    inserted = repo.insert_position_snapshot(duplicate)
    assert inserted is False

    history = repo.get_snapshots_for_position(position.position_id)
    assert [s.recommendation for s in history] == [BUY_FILLED, HOLD, HOLD, SELL_TARGET]
    assert [s.holding_day_count for s in history] == [1, 2, 3, 4]
    assert history[0].close_price == pytest.approx(10.2)  # not overwritten to 999.0


def test_foreign_key_constraint_is_enforced(repo: SandboxRepository):
    order = EntryOrder(
        order_id="does-not-exist:order",
        candidate_id="does-not-exist",
        symbol="AAA",
        signal_date=date(2026, 6, 15),
        created_date=date(2026, 6, 15),
        valid_until=date(2026, 6, 17),
        max_entry_price=10.2,
        status="PENDING",
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_entry_order(order)


def test_unique_symbol_entry_date_constraint_on_positions(repo: SandboxRepository):
    as_of = date(2026, 6, 15)
    _ensure_run(repo, as_of)
    candidate_a = _make_candidate(as_of, symbol="AAA")
    repo.insert_ranked_candidate(candidate_a)
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate_a.candidate_id),
        candidate_id=candidate_a.candidate_id,
        symbol="AAA",
        signal_date=as_of,
        created_date=as_of,
        valid_until=date(2026, 6, 17),
        max_entry_price=10.2,
        status="FILLED",
    )
    repo.create_entry_order(order)
    entry_date = date(2026, 6, 16)
    position_1 = VirtualPosition(
        position_id=VirtualPosition.make_id("AAA", entry_date),
        symbol="AAA",
        candidate_id=candidate_a.candidate_id,
        order_id=order.order_id,
        signal_date=as_of,
        entry_date=entry_date,
        entry_price=10.1,
        quantity=99.0099,
        initial_rank=1,
        initial_model_score=0.5,
        signal_close=10.0,
        max_entry_price=10.2,
        initial_adv_quintile="adv_q1",
        initial_market_regime="Bull_Normal",
        target_price=12.12,
        planned_time_exit_date=date(2026, 7, 15),
    )
    repo.create_position(position_1)

    # A second, distinct position_id but the SAME (symbol, entry_date) must be
    # rejected by the schema's own UNIQUE constraint, not just app-level logic.
    position_2 = VirtualPosition(
        position_id="AAA:2026-06-16:duplicate",
        symbol="AAA",
        candidate_id=candidate_a.candidate_id,
        order_id=order.order_id,
        signal_date=as_of,
        entry_date=entry_date,
        entry_price=10.1,
        quantity=99.0099,
        initial_rank=1,
        initial_model_score=0.5,
        signal_close=10.0,
        max_entry_price=10.2,
        initial_adv_quintile="adv_q1",
        initial_market_regime="Bull_Normal",
        target_price=12.12,
        planned_time_exit_date=date(2026, 7, 15),
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_position(position_2)
