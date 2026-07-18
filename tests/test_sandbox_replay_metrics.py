"""Focused tests for reporting/replay_metrics.py's aggregation logic, using directly
constructed repository state rather than running a full replay -- precise control
over the exact holding-day/MFE/MAE values needed to catch the EXP-004 review finding
(metrics reading virtual_positions' current-state columns instead of the final
position_snapshots row)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.position import PositionSnapshot, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import HOLD, SELL_TARGET
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository
from stock_analyzer.sandbox.reporting.replay_metrics import build_replay_metrics

SIGNAL_DATE = date(2026, 1, 5)
ENTRY_DATE = date(2026, 1, 6)


@pytest.fixture
def repo() -> SandboxRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return SandboxRepository(conn)


def _seed_one_closed_position(repo: SandboxRepository, symbol: str, snapshots: list[tuple[date, int, float, float, str]]) -> str:
    """`snapshots` is [(as_of_date, holding_day_count, mfe, mae, recommendation), ...]
    in order. The position is closed using the values from a DIFFERENT (earlier, i.e.
    stale) snapshot for its current-state columns, mirroring exactly the bug this
    guards against -- so a test that reads virtual_positions directly would get the
    wrong answer, and only reading the final position_snapshots row is correct."""

    candidate = RankedCandidate(
        candidate_id=RankedCandidate.make_id(SIGNAL_DATE, symbol),
        run_id=SandboxRun.make_id(SIGNAL_DATE, "generate-candidates"),
        as_of_date=SIGNAL_DATE,
        symbol=symbol,
        daily_rank=1,
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
    repo.create_run(
        SandboxRun(
            run_id=candidate.run_id,
            as_of_date=SIGNAL_DATE,
            command="generate-candidates",
            started_at=datetime.now(timezone.utc),
            configuration_hash="test",
        )
    )
    repo.insert_ranked_candidate(candidate)
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate.candidate_id),
        candidate_id=candidate.candidate_id,
        symbol=symbol,
        signal_date=SIGNAL_DATE,
        created_date=SIGNAL_DATE,
        valid_until=date.fromordinal(SIGNAL_DATE.toordinal() + 2),
        max_entry_price=10.2,
        status="FILLED",
    )
    repo.create_entry_order(order)
    position = VirtualPosition(
        position_id=VirtualPosition.make_id(symbol, ENTRY_DATE),
        symbol=symbol,
        candidate_id=candidate.candidate_id,
        order_id=order.order_id,
        signal_date=SIGNAL_DATE,
        entry_date=ENTRY_DATE,
        entry_price=10.0,
        quantity=100.0,
        initial_rank=1,
        initial_model_score=0.5,
        signal_close=10.0,
        max_entry_price=10.2,
        initial_adv_quintile="adv_q1",
        initial_market_regime="Bull_Normal",
        target_price=12.0,
        planned_time_exit_date=date.fromordinal(ENTRY_DATE.toordinal() + 30),
    )
    repo.create_position(position)

    for as_of_date, holding_day_count, mfe, mae, recommendation in snapshots:
        repo.insert_position_snapshot(
            PositionSnapshot(
                snapshot_id=PositionSnapshot.make_id(position.position_id, as_of_date),
                position_id=position.position_id,
                symbol=symbol,
                as_of_date=as_of_date,
                close_price=10.5,
                daily_return=None,
                cumulative_unrealized_return=0.05,
                holding_day_count=holding_day_count,
                mfe=mfe,
                mae=mae,
                distance_to_target=0.1,
                current_rank=1,
                current_model_score=0.5,
                rank_change_from_entry=0,
                current_adv_quintile="adv_q1",
                current_market_regime="Bull_Normal",
                data_quality_status="OK",
                recommendation=recommendation,
            )
        )

    # Simulate the bug: update_position_state called with the PRIOR (second-to-last)
    # snapshot's values, then close_position called WITHOUT the final values -- i.e.
    # exactly what the old code path did before the fix. This test asserts
    # build_replay_metrics is correct regardless of what virtual_positions holds,
    # because it reads position_snapshots.
    stale = snapshots[-2] if len(snapshots) > 1 else snapshots[-1]
    repo.update_position_state(
        position.position_id,
        current_holding_day_count=stale[1],
        current_close=10.3,
        unrealized_return=0.03,
        mfe=stale[2],
        mae=stale[3],
    )
    final = snapshots[-1]
    repo.close_position(
        position.position_id,
        exit_date=final[0],
        exit_price=12.0,
        exit_reason="SELL_TARGET",
        realized_return=0.20,
        final_holding_day_count=final[1],
        final_mfe=final[2],
        final_mae=final[3],
    )
    return position.position_id


def test_replay_metrics_holding_mfe_mae_use_final_snapshot(repo: SandboxRepository):
    day2 = date.fromordinal(ENTRY_DATE.toordinal() + 1)
    day3 = date.fromordinal(ENTRY_DATE.toordinal() + 2)
    _seed_one_closed_position(
        repo,
        "AAA",
        snapshots=[
            (ENTRY_DATE, 1, 0.03, -0.01, HOLD),
            (day2, 2, 0.05, -0.02, HOLD),
            (day3, 3, 0.21, -0.02, SELL_TARGET),  # the correct, final values
        ],
    )

    metrics = build_replay_metrics(repo, SIGNAL_DATE, SIGNAL_DATE, day3)

    assert metrics["position_lifecycle"]["holding_days_mean"] == pytest.approx(3)
    assert metrics["position_lifecycle"]["mfe_mean"] == pytest.approx(0.21)
    assert metrics["position_lifecycle"]["mae_mean"] == pytest.approx(-0.02)
