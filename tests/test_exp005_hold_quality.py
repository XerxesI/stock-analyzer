"""Tests for EXP-005's HOLD-decision diagnostics -- Revision 5, Section 22, Stage 13."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.position import CLOSED, OPEN, VirtualPosition
from stock_analyzer.sandbox.domain.position import PositionSnapshot
from stock_analyzer.sandbox.domain.recommendation import HOLD, SELL_TIME
from stock_analyzer.sandbox.exp005.diagnostics._shared import END_OF_EXPERIMENT
from stock_analyzer.sandbox.exp005.diagnostics.hold_quality import (
    ADVERSE,
    PROFITABLE,
    UNRESOLVED,
    HoldQualityComputationError,
    compute_hold_quality,
    compute_hold_quality_for_position,
)
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import to_money_units, to_price_units, to_quantity_units, to_rate_units

REPLAY_ID = "replay-1"
NOW = datetime.now(timezone.utc)
SYMBOL = "AAA"


def _bar(symbol: str, d: date, o: float, h: float, low: float, c: float) -> dict:
    return {"symbol": symbol, "date": pd.Timestamp(d), "Open": o, "High": h, "Low": low, "Close": c}


STANDARD_PRICES = pd.DataFrame(
    [
        _bar(SYMBOL, date(2026, 1, 5), 10.0, 11.0, 9.0, 10.0),
        _bar(SYMBOL, date(2026, 1, 6), 10.5, 11.0, 10.0, 10.5),
        _bar(SYMBOL, date(2026, 1, 7), 10.6, 12.0, 10.4, 11.0),
        _bar(SYMBOL, date(2026, 1, 8), 11.0, 13.0, 10.9, 12.5),
        _bar(SYMBOL, date(2026, 1, 9), 12.5, 12.6, 12.0, 12.4),
    ]
)
STANDARD_CALENDAR = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8), date(2026, 1, 9))


class _FakeManifest:
    def __init__(self, outcome_data_end_date: date) -> None:
        self.outcome_data_end_date = outcome_data_end_date


class _FakePortfolioRepo:
    def __init__(self, executions: list[Execution]) -> None:
        self._executions = executions

    def list_executions_for_position(self, position_id: str) -> list[Execution]:
        return [e for e in self._executions if e.position_id == position_id]


class _FakeSandboxRepo:
    def __init__(self, snapshots: list[PositionSnapshot]) -> None:
        self._snapshots = snapshots

    def get_snapshots_for_position(self, position_id: str) -> list[PositionSnapshot]:
        return [s for s in self._snapshots if s.position_id == position_id]


class _FakeContext:
    def __init__(
        self, prices_df: pd.DataFrame, executions: list[Execution], outcome_data_end_date: date,
        snapshots: list[PositionSnapshot] | None = None,
    ) -> None:
        self.manifest = _FakeManifest(outcome_data_end_date)
        self.replay_id = REPLAY_ID
        self.prices_df = prices_df
        self.portfolio_repo = _FakePortfolioRepo(executions)
        self.sandbox_repo = _FakeSandboxRepo(snapshots or [])


def _buy_execution(position_id: str, effective_price: float, d: date) -> Execution:
    return Execution(
        execution_id=f"{position_id}:BUY", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
        order_id=f"{position_id}:order", candidate_id=position_id, position_id=position_id, symbol=SYMBOL,
        side=BUY, decision_date=d, execution_date=d,
        raw_market_fill_price_units=to_price_units(effective_price), effective_fill_price_units=to_price_units(effective_price),
        quantity_units=to_quantity_units(10.0), gross_notional_units=to_money_units(effective_price * 10.0),
        commission_units=to_money_units(1.0), slippage_rate_units=to_rate_units(0.0),
        slippage_cost_units=0, net_cash_flow_units=-to_money_units(effective_price * 10.0 + 1.0),
        fill_reason="FILLED_AT_OPEN", market_data_snapshot_id="snap-1", created_at=NOW,
    )


def _sell_execution(position_id: str, effective_price: float, d: date) -> Execution:
    return Execution(
        execution_id=f"{position_id}:SELL", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
        order_id=None, candidate_id=position_id, position_id=position_id, symbol=SYMBOL,
        side=SELL, decision_date=d, execution_date=d,
        raw_market_fill_price_units=to_price_units(effective_price), effective_fill_price_units=to_price_units(effective_price),
        quantity_units=to_quantity_units(10.0), gross_notional_units=to_money_units(effective_price * 10.0),
        commission_units=to_money_units(1.0), slippage_rate_units=to_rate_units(0.0),
        slippage_cost_units=0, net_cash_flow_units=to_money_units(effective_price * 10.0 - 1.0),
        fill_reason=SELL_TIME, market_data_snapshot_id="snap-1", created_at=NOW,
    )


def _position(position_id: str, status: str = OPEN, exit_date: date | None = None, target_price: float = 13.0) -> VirtualPosition:
    return VirtualPosition(
        position_id=position_id, symbol=SYMBOL, candidate_id=position_id, order_id=f"{position_id}:order",
        signal_date=date(2026, 1, 5), entry_date=date(2026, 1, 5), entry_price=10.0, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=10.0, max_entry_price=10.1,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=target_price,
        planned_time_exit_date=date(2026, 2, 2), status=status, exit_date=exit_date,
        exit_reason=SELL_TIME if status == CLOSED else None,
    )


def _snapshot(position_id: str, as_of_date: date, close_price: float, recommendation: str = HOLD) -> PositionSnapshot:
    return PositionSnapshot(
        snapshot_id=PositionSnapshot.make_id(position_id, as_of_date), position_id=position_id, symbol=SYMBOL,
        as_of_date=as_of_date, close_price=close_price, daily_return=None, cumulative_unrealized_return=0.05,
        holding_day_count=2, mfe=0.05, mae=-0.01, distance_to_target=0.2, current_rank=1, current_model_score=0.5,
        rank_change_from_entry=0, current_adv_quintile="adv_q1", current_market_regime="Bull_Normal",
        data_quality_status="OK", recommendation=recommendation,
    )


def test_hand_computed_horizons_with_end_of_experiment_censoring():
    position_id = "p1"
    position = _position(position_id, status=OPEN)
    snapshot = _snapshot(position_id, date(2026, 1, 6), close_price=10.5)
    executions = [_buy_execution(position_id, 10.0, date(2026, 1, 5))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 9))

    result = compute_hold_quality(context, snapshot, position, STANDARD_CALENDAR)

    h1 = result.horizons[0]
    assert h1.horizon_sessions == 1
    assert h1.is_censored is False
    assert h1.forward_close_return_pct == pytest.approx((11.0 - 10.5) / 10.5)
    assert h1.max_high_price == pytest.approx(12.0)
    assert h1.min_low_price == pytest.approx(10.4)
    assert h1.target_reached is False

    h5 = result.horizons[1]
    assert h5.horizon_sessions == 5
    assert h5.is_censored is True
    assert h5.censoring_reason == END_OF_EXPERIMENT
    assert h5.sessions_observed == 3
    assert h5.target_reached is True
    assert h5.sessions_to_target == 2

    h10 = result.horizons[2]
    assert h10.is_censored is True
    assert h10.sessions_observed == 3

    assert result.eventual_outcome == UNRESOLVED  # still open


def test_eventual_outcome_profitable():
    position_id = "p2"
    position = _position(position_id, status=CLOSED, exit_date=date(2026, 1, 9))
    snapshot = _snapshot(position_id, date(2026, 1, 6), close_price=10.5)
    executions = [_buy_execution(position_id, 10.0, date(2026, 1, 5)), _sell_execution(position_id, 12.4, date(2026, 1, 9))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 9))

    result = compute_hold_quality(context, snapshot, position, STANDARD_CALENDAR)

    assert result.eventual_outcome == PROFITABLE


def test_eventual_outcome_adverse():
    position_id = "p3"
    position = _position(position_id, status=CLOSED, exit_date=date(2026, 1, 9))
    snapshot = _snapshot(position_id, date(2026, 1, 6), close_price=10.5)
    executions = [_buy_execution(position_id, 10.0, date(2026, 1, 5)), _sell_execution(position_id, 9.0, date(2026, 1, 9))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 9))

    result = compute_hold_quality(context, snapshot, position, STANDARD_CALENDAR)

    assert result.eventual_outcome == ADVERSE


def test_non_hold_snapshot_raises():
    position_id = "p4"
    position = _position(position_id, status=OPEN)
    snapshot = _snapshot(position_id, date(2026, 1, 6), close_price=10.5, recommendation=SELL_TIME)
    context = _FakeContext(STANDARD_PRICES, [], date(2026, 1, 9))

    with pytest.raises(HoldQualityComputationError):
        compute_hold_quality(context, snapshot, position, STANDARD_CALENDAR)


def test_batch_helper_filters_to_hold_snapshots_only():
    position_id = "p5"
    position = _position(position_id, status=OPEN)
    snapshots = [
        _snapshot(position_id, date(2026, 1, 5), close_price=10.0, recommendation="BUY_FILLED"),
        _snapshot(position_id, date(2026, 1, 6), close_price=10.5, recommendation=HOLD),
        _snapshot(position_id, date(2026, 1, 7), close_price=11.0, recommendation=HOLD),
    ]
    executions = [_buy_execution(position_id, 10.0, date(2026, 1, 5))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 9), snapshots=snapshots)

    results = compute_hold_quality_for_position(context, position, STANDARD_CALENDAR)

    assert len(results) == 2
    assert [r.as_of_date for r in results] == [date(2026, 1, 6), date(2026, 1, 7)]
