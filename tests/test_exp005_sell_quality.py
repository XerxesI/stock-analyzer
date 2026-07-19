"""Tests for EXP-005's post-exit (SELL quality) diagnostics -- Revision 5,
Section 21, Stage 13.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.position import CLOSED, OPEN, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import SELL_TIME
from stock_analyzer.sandbox.exp005.diagnostics._shared import END_OF_EXPERIMENT, MISSING_MARKET_DATA
from stock_analyzer.sandbox.exp005.diagnostics.sell_quality import SellQualityComputationError, compute_sell_quality
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


class _FakeContext:
    def __init__(self, prices_df: pd.DataFrame, executions: list[Execution], outcome_data_end_date: date) -> None:
        self.manifest = _FakeManifest(outcome_data_end_date)
        self.replay_id = REPLAY_ID
        self.prices_df = prices_df
        self.portfolio_repo = _FakePortfolioRepo(executions)
        self.sandbox_repo = None


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


def _closed_position(position_id: str, exit_date: date, target_price: float = 13.0) -> VirtualPosition:
    return VirtualPosition(
        position_id=position_id, symbol=SYMBOL, candidate_id=position_id, order_id=f"{position_id}:order",
        signal_date=date(2026, 1, 5), entry_date=date(2026, 1, 5), entry_price=10.0, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=10.0, max_entry_price=10.1,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=target_price,
        planned_time_exit_date=date(2026, 2, 2), status=CLOSED, exit_date=exit_date, exit_reason=SELL_TIME,
    )


def test_hand_computed_horizons_with_end_of_experiment_censoring():
    position_id = "p1"
    position = _closed_position(position_id, date(2026, 1, 6))
    executions = [_buy_execution(position_id, 10.0, date(2026, 1, 5)), _sell_execution(position_id, 10.5, date(2026, 1, 6))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 9))

    result = compute_sell_quality(context, position, STANDARD_CALENDAR)

    assert result.effective_exit_price == pytest.approx(10.5)

    h1 = result.horizons[0]
    assert h1.horizon_sessions == 1
    assert h1.is_censored is False
    assert h1.sessions_observed == 1
    assert h1.close_to_close_return_pct == pytest.approx((11.0 - 10.5) / 10.5)
    assert h1.max_high_price == pytest.approx(12.0)
    assert h1.max_high_pct == pytest.approx((12.0 - 10.5) / 10.5)
    assert h1.min_low_price == pytest.approx(10.4)
    assert h1.min_low_pct == pytest.approx((10.4 - 10.5) / 10.5)
    assert h1.target_reached is False
    assert h1.sessions_to_target is None

    h5 = result.horizons[1]
    assert h5.horizon_sessions == 5
    assert h5.is_censored is True
    assert h5.censoring_reason == END_OF_EXPERIMENT
    assert h5.sessions_observed == 3  # only Jan 7/8/9 exist before/at outcome_data_end_date
    assert h5.close_to_close_return_pct == pytest.approx((12.4 - 10.5) / 10.5)
    assert h5.max_high_price == pytest.approx(13.0)
    assert h5.target_reached is True
    assert h5.sessions_to_target == 2  # Jan 8 is the 2nd session in the window

    h10 = result.horizons[2]
    assert h10.is_censored is True
    assert h10.censoring_reason == END_OF_EXPERIMENT
    assert h10.sessions_observed == 3

    h20 = result.horizons[3]
    assert h20.is_censored is True
    assert h20.sessions_observed == 3


def test_missing_market_data_censoring_takes_priority():
    position_id = "p2"
    # AAA has no bar for Jan 7 -- but the calendar (as if another symbol traded) does.
    prices = pd.DataFrame(
        [
            _bar(SYMBOL, date(2026, 1, 5), 10.0, 11.0, 9.0, 10.0),
            _bar(SYMBOL, date(2026, 1, 6), 10.5, 11.0, 10.0, 10.5),
            _bar(SYMBOL, date(2026, 1, 8), 11.0, 13.0, 10.9, 12.5),
        ]
    )
    calendar = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))
    position = _closed_position(position_id, date(2026, 1, 6))
    executions = [_buy_execution(position_id, 10.0, date(2026, 1, 5)), _sell_execution(position_id, 10.5, date(2026, 1, 6))]
    context = _FakeContext(prices, executions, date(2026, 1, 8))

    result = compute_sell_quality(context, position, calendar)

    h2 = result.horizons[0]  # horizon=1: only Jan 7 nominally -- missing for AAA
    assert h2.is_censored is True
    assert h2.censoring_reason == MISSING_MARKET_DATA
    assert h2.sessions_observed == 0


def test_open_position_raises():
    position_id = "p3"
    position = _closed_position(position_id, date(2026, 1, 6))
    position.status = OPEN
    context = _FakeContext(STANDARD_PRICES, [], date(2026, 1, 9))

    with pytest.raises(SellQualityComputationError):
        compute_sell_quality(context, position, STANDARD_CALENDAR)


def test_missing_sell_execution_raises():
    position_id = "p4"
    position = _closed_position(position_id, date(2026, 1, 6))
    executions = [_buy_execution(position_id, 10.0, date(2026, 1, 5))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 9))

    with pytest.raises(SellQualityComputationError):
        compute_sell_quality(context, position, STANDARD_CALENDAR)


def test_zero_observed_sessions_reports_none_fields_not_zero():
    position_id = "p5"
    position = _closed_position(position_id, date(2026, 1, 9))  # exit is the last available date
    executions = [_buy_execution(position_id, 10.0, date(2026, 1, 5)), _sell_execution(position_id, 12.4, date(2026, 1, 9))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 9))

    result = compute_sell_quality(context, position, STANDARD_CALENDAR)

    h1 = result.horizons[0]
    assert h1.sessions_observed == 0
    assert h1.is_censored is True
    assert h1.close_to_close_return_pct is None
    assert h1.max_high_price is None
    assert h1.target_reached is False
    assert h1.sessions_to_target is None
