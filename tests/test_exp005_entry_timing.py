"""Tests for EXP-005's entry-timing diagnostics -- Revision 5, Section 23, Stage 13."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import (
    EXPIRED,
    FILLED,
    FILLED_AT_CEILING,
    FILLED_AT_OPEN,
    NO_FILL,
    EntryOrder,
    EntryOrderAttempt,
)
from stock_analyzer.sandbox.domain.position import OPEN, VirtualPosition
from stock_analyzer.sandbox.exp005.diagnostics._shared import END_OF_EXPERIMENT
from stock_analyzer.sandbox.exp005.diagnostics.entry_timing import (
    EntryTimingComputationError,
    compute_entry_timing_for_expired_order,
    compute_entry_timing_for_filled_order,
)
from stock_analyzer.sandbox.exp005.domain.execution import BUY, Execution
from stock_analyzer.sandbox.exp005.domain.units import to_money_units, to_price_units, to_quantity_units, to_rate_units

REPLAY_ID = "replay-1"
NOW = datetime.now(timezone.utc)


def _bar(symbol: str, d: date, o: float, h: float, low: float, c: float) -> dict:
    return {"symbol": symbol, "date": pd.Timestamp(d), "Open": o, "High": h, "Low": low, "Close": c}


class _FakeManifest:
    def __init__(self, outcome_data_end_date: date) -> None:
        self.outcome_data_end_date = outcome_data_end_date


class _FakePortfolioRepo:
    def __init__(self, executions: list[Execution]) -> None:
        self._executions = executions

    def list_executions_for_position(self, position_id: str) -> list[Execution]:
        return [e for e in self._executions if e.position_id == position_id]


class _FakeSandboxRepo:
    def __init__(self, candidates: dict[str, RankedCandidate], attempts: dict[str, list[EntryOrderAttempt]]) -> None:
        self._candidates = candidates
        self._attempts = attempts

    def get_candidate(self, candidate_id: str) -> RankedCandidate | None:
        return self._candidates.get(candidate_id)

    def get_attempts_for_order(self, order_id: str) -> list[EntryOrderAttempt]:
        return self._attempts.get(order_id, [])


class _FakeContext:
    def __init__(
        self, prices_df: pd.DataFrame, executions: list[Execution], outcome_data_end_date: date,
        candidates: dict[str, RankedCandidate] | None = None, attempts: dict[str, list[EntryOrderAttempt]] | None = None,
    ) -> None:
        self.manifest = _FakeManifest(outcome_data_end_date)
        self.replay_id = REPLAY_ID
        self.prices_df = prices_df
        self.portfolio_repo = _FakePortfolioRepo(executions)
        self.sandbox_repo = _FakeSandboxRepo(candidates or {}, attempts or {})


def _candidate(candidate_id: str, symbol: str, signal_close: float) -> RankedCandidate:
    return RankedCandidate(
        candidate_id=candidate_id, run_id="run-1", as_of_date=date(2026, 1, 5), symbol=symbol, daily_rank=1,
        model_score=0.5, signal_close=signal_close, atr14=0.5, max_entry_price=10.1, shadow_top10=True,
        actionable=True, exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
    )


def _buy_execution(position_id: str, order_id: str, raw_price: float, effective_price: float, d: date, fill_reason: str) -> Execution:
    return Execution(
        execution_id=f"{position_id}:BUY", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
        order_id=order_id, candidate_id=position_id, position_id=position_id, symbol="AAA",
        side=BUY, decision_date=d, execution_date=d,
        raw_market_fill_price_units=to_price_units(raw_price), effective_fill_price_units=to_price_units(effective_price),
        quantity_units=to_quantity_units(10.0), gross_notional_units=to_money_units(effective_price * 10.0),
        commission_units=to_money_units(1.0), slippage_rate_units=to_rate_units(0.0006),
        slippage_cost_units=to_money_units(0.6), net_cash_flow_units=-to_money_units(effective_price * 10.0 + 1.6),
        fill_reason=fill_reason, market_data_snapshot_id="snap-1", created_at=NOW,
    )


# ------------------------------------------------------------------------- filled


def test_hand_computed_filled_at_open_scenario():
    candidate_id = "p1"
    order_id = f"{candidate_id}:order"
    signal_date = date(2026, 1, 5)
    entry_date = date(2026, 1, 6)

    prices = pd.DataFrame(
        [
            _bar("AAA", signal_date, 10.0, 10.5, 9.5, 10.0),
            _bar("AAA", entry_date, 10.05, 10.6, 9.9, 10.3),
            _bar("AAA", date(2026, 1, 7), 10.3, 11.0, 10.1, 10.8),
            _bar("AAA", date(2026, 1, 8), 10.8, 12.0, 10.6, 11.5),
            _bar("AAA", date(2026, 1, 9), 11.5, 11.6, 11.2, 11.4),
            _bar("AAA", date(2026, 1, 10), 11.4, 11.5, 11.0, 11.3),
        ]
    )
    calendar = tuple(sorted(pd.to_datetime(prices["date"]).dt.date))

    order = EntryOrder(
        order_id=order_id, candidate_id=candidate_id, symbol="AAA", signal_date=signal_date,
        created_date=signal_date, valid_until=date(2026, 1, 7), max_entry_price=10.1, status=FILLED,
        fill_date=entry_date, fill_price=10.05, fill_reason=FILLED_AT_OPEN,
    )
    position = VirtualPosition(
        position_id=candidate_id, symbol="AAA", candidate_id=candidate_id, order_id=order_id,
        signal_date=signal_date, entry_date=entry_date, entry_price=10.05, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=10.0, max_entry_price=10.1,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=11.5,
        planned_time_exit_date=date(2026, 1, 10), status=OPEN,
    )
    executions = [_buy_execution(candidate_id, order_id, 10.05, 10.06, entry_date, FILLED_AT_OPEN)]
    candidates = {candidate_id: _candidate(candidate_id, "AAA", 10.0)}
    attempts = {order_id: [
        EntryOrderAttempt(
            attempt_id=EntryOrderAttempt.make_id(order_id, entry_date), order_id=order_id, symbol="AAA",
            attempt_date=entry_date, session_open=10.05, session_high=10.6, session_low=9.9, session_close=10.3,
            max_entry_price=10.1, outcome=FILLED_AT_OPEN, fill_price=10.05, reason="next_day_open<=max_entry_price",
        )
    ]}
    context = _FakeContext(prices, executions, date(2026, 1, 10), candidates=candidates, attempts=attempts)

    result = compute_entry_timing_for_filled_order(context, order, position, calendar)

    assert result.signal_close == pytest.approx(10.0)
    assert result.next_session_open == pytest.approx(10.05)
    assert result.entry_gap_pct == pytest.approx(0.005)
    assert result.raw_fill_price == pytest.approx(10.05)
    assert result.effective_fill_price == pytest.approx(10.06)
    assert result.slippage_cost == pytest.approx(0.6)
    assert result.slippage_rate_pct == pytest.approx(0.0006)
    assert result.fill_percentile == pytest.approx((10.05 - 9.9) / (10.6 - 9.9))

    h1 = result.horizons[0]
    assert h1.horizon_sessions == 1
    assert h1.is_censored is False
    assert h1.sessions_observed == 1  # entry session itself, FILLED_AT_OPEN inclusion rule
    assert h1.mfe_price == pytest.approx(10.6)
    assert h1.mfe_pct == pytest.approx((10.6 - 10.06) / 10.06)
    assert h1.mae_price == pytest.approx(9.9)
    assert h1.forward_return_pct == pytest.approx((10.3 - 10.06) / 10.06)

    h5 = result.horizons[1]
    assert h5.is_censored is False
    assert h5.sessions_observed == 5
    assert h5.mfe_price == pytest.approx(12.0)  # Jan 8's High
    assert h5.sessions_to_mfe == 3
    assert h5.mae_price == pytest.approx(9.9)  # Jan 6's Low (entry session, included)
    assert h5.forward_return_pct == pytest.approx((11.3 - 10.06) / 10.06)  # last close, Jan 10

    h10 = result.horizons[2]
    assert h10.is_censored is True
    assert h10.censoring_reason == END_OF_EXPERIMENT
    assert h10.sessions_observed == 5

    h20 = result.horizons[3]
    assert h20.is_censored is True
    assert h20.sessions_observed == 5

    assert result.target_reached_within_actual_holding_horizon is True  # Jan 8's High=12 >= target 11.5


def test_filled_at_ceiling_excludes_entry_session_from_horizons():
    candidate_id = "p2"
    order_id = f"{candidate_id}:order"
    signal_date = date(2026, 1, 5)
    entry_date = date(2026, 1, 6)

    prices = pd.DataFrame(
        [
            _bar("AAA", signal_date, 10.0, 10.5, 9.5, 10.0),
            _bar("AAA", entry_date, 10.2, 10.6, 9.9, 10.3),
            _bar("AAA", date(2026, 1, 7), 10.3, 11.0, 10.1, 10.8),
        ]
    )
    calendar = tuple(sorted(pd.to_datetime(prices["date"]).dt.date))

    order = EntryOrder(
        order_id=order_id, candidate_id=candidate_id, symbol="AAA", signal_date=signal_date,
        created_date=signal_date, valid_until=date(2026, 1, 7), max_entry_price=10.0, status=FILLED,
        fill_date=entry_date, fill_price=10.0, fill_reason=FILLED_AT_CEILING,
    )
    position = VirtualPosition(
        position_id=candidate_id, symbol="AAA", candidate_id=candidate_id, order_id=order_id,
        signal_date=signal_date, entry_date=entry_date, entry_price=10.0, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=10.0, max_entry_price=10.0,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=99.0,
        planned_time_exit_date=date(2026, 1, 7), status=OPEN,
    )
    executions = [_buy_execution(candidate_id, order_id, 10.0, 10.0, entry_date, FILLED_AT_CEILING)]
    candidates = {candidate_id: _candidate(candidate_id, "AAA", 10.0)}
    attempts = {order_id: [
        EntryOrderAttempt(
            attempt_id=EntryOrderAttempt.make_id(order_id, entry_date), order_id=order_id, symbol="AAA",
            attempt_date=entry_date, session_open=10.2, session_high=10.6, session_low=9.9, session_close=10.3,
            max_entry_price=10.0, outcome=FILLED_AT_CEILING, fill_price=10.0, reason="gap_above_ceiling_but_session_low_touched_ceiling",
        )
    ]}
    context = _FakeContext(prices, executions, date(2026, 1, 7), candidates=candidates, attempts=attempts)

    result = compute_entry_timing_for_filled_order(context, order, position, calendar)

    h1 = result.horizons[0]
    assert h1.sessions_observed == 1
    # entry session (Jan 6, High=10.6/Low=9.9) excluded -- only Jan 7 counted.
    assert h1.mfe_price == pytest.approx(11.0)
    assert h1.mae_price == pytest.approx(10.1)


def test_degenerate_flat_session_gives_none_fill_percentile():
    candidate_id = "p3"
    order_id = f"{candidate_id}:order"
    signal_date = date(2026, 1, 5)
    entry_date = date(2026, 1, 6)
    prices = pd.DataFrame(
        [
            _bar("AAA", signal_date, 10.0, 10.5, 9.5, 10.0),
            _bar("AAA", entry_date, 10.0, 10.0, 10.0, 10.0),
        ]
    )
    calendar = tuple(sorted(pd.to_datetime(prices["date"]).dt.date))
    order = EntryOrder(
        order_id=order_id, candidate_id=candidate_id, symbol="AAA", signal_date=signal_date,
        created_date=signal_date, valid_until=date(2026, 1, 7), max_entry_price=10.1, status=FILLED,
        fill_date=entry_date, fill_price=10.0, fill_reason=FILLED_AT_OPEN,
    )
    position = VirtualPosition(
        position_id=candidate_id, symbol="AAA", candidate_id=candidate_id, order_id=order_id,
        signal_date=signal_date, entry_date=entry_date, entry_price=10.0, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=10.0, max_entry_price=10.1,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=99.0,
        planned_time_exit_date=date(2026, 1, 6), status=OPEN,
    )
    executions = [_buy_execution(candidate_id, order_id, 10.0, 10.0, entry_date, FILLED_AT_OPEN)]
    candidates = {candidate_id: _candidate(candidate_id, "AAA", 10.0)}
    attempts = {order_id: [
        EntryOrderAttempt(
            attempt_id=EntryOrderAttempt.make_id(order_id, entry_date), order_id=order_id, symbol="AAA",
            attempt_date=entry_date, session_open=10.0, session_high=10.0, session_low=10.0, session_close=10.0,
            max_entry_price=10.1, outcome=FILLED_AT_OPEN, fill_price=10.0, reason="next_day_open<=max_entry_price",
        )
    ]}
    context = _FakeContext(prices, executions, date(2026, 1, 6), candidates=candidates, attempts=attempts)

    result = compute_entry_timing_for_filled_order(context, order, position, calendar)

    assert result.fill_percentile is None


def test_missing_buy_execution_raises():
    candidate_id = "p4"
    order_id = f"{candidate_id}:order"
    prices = pd.DataFrame([_bar("AAA", date(2026, 1, 5), 10, 10, 10, 10)])
    order = EntryOrder(
        order_id=order_id, candidate_id=candidate_id, symbol="AAA", signal_date=date(2026, 1, 5),
        created_date=date(2026, 1, 5), valid_until=date(2026, 1, 7), max_entry_price=10.1, status=FILLED,
    )
    position = VirtualPosition(
        position_id=candidate_id, symbol="AAA", candidate_id=candidate_id, order_id=order_id,
        signal_date=date(2026, 1, 5), entry_date=date(2026, 1, 6), entry_price=10.0, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=10.0, max_entry_price=10.1,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=99.0,
        planned_time_exit_date=date(2026, 1, 6), status=OPEN,
    )
    context = _FakeContext(prices, [], date(2026, 1, 6))

    with pytest.raises(EntryTimingComputationError):
        compute_entry_timing_for_filled_order(context, order, position, (date(2026, 1, 5),))


# ------------------------------------------------------------------------ expired


def test_hand_computed_expired_order_scenario():
    candidate_id = "q1"
    order_id = f"{candidate_id}:order"
    signal_date = date(2026, 1, 5)

    prices = pd.DataFrame(
        [
            _bar("BBB", signal_date, 10.0, 10.2, 9.8, 10.0),
            _bar("BBB", date(2026, 1, 6), 10.3, 10.6, 10.2, 10.4),
            _bar("BBB", date(2026, 1, 7), 10.5, 10.8, 10.5, 10.6),
            _bar("BBB", date(2026, 1, 8), 10.6, 11.0, 10.4, 10.9),
            _bar("BBB", date(2026, 1, 9), 10.9, 11.2, 10.7, 11.0),
        ]
    )
    calendar = tuple(sorted(pd.to_datetime(prices["date"]).dt.date))

    order = EntryOrder(
        order_id=order_id, candidate_id=candidate_id, symbol="BBB", signal_date=signal_date,
        created_date=signal_date, valid_until=date(2026, 1, 7), max_entry_price=10.0, status=EXPIRED,
        no_fill_reason="entire_session_above_ceiling",
    )
    attempts = {order_id: [
        EntryOrderAttempt(
            attempt_id=EntryOrderAttempt.make_id(order_id, date(2026, 1, 6)), order_id=order_id, symbol="BBB",
            attempt_date=date(2026, 1, 6), session_open=10.3, session_high=10.6, session_low=10.2,
            session_close=10.4, max_entry_price=10.0, outcome=NO_FILL, fill_price=None,
            reason="entire_session_above_ceiling",
        ),
        EntryOrderAttempt(
            attempt_id=EntryOrderAttempt.make_id(order_id, date(2026, 1, 7)), order_id=order_id, symbol="BBB",
            attempt_date=date(2026, 1, 7), session_open=10.5, session_high=10.8, session_low=10.5,
            session_close=10.6, max_entry_price=10.0, outcome=NO_FILL, fill_price=None,
            reason="entire_session_above_ceiling",
        ),
    ]}
    context = _FakeContext(prices, [], date(2026, 1, 9), attempts=attempts)

    result = compute_entry_timing_for_expired_order(context, order, calendar)

    assert result.expiry_date == date(2026, 1, 7)
    assert result.min_distance_to_ceiling_pct == pytest.approx((10.2 - 10.0) / 10.0)

    h1 = result.horizons[0]
    assert h1.is_censored is False
    assert h1.sessions_observed == 1
    assert h1.mfe_price == pytest.approx(11.0)  # Jan 8's High
    assert h1.mfe_pct == pytest.approx((11.0 - 10.0) / 10.0)
    assert h1.mae_price == pytest.approx(10.4)  # Jan 8's Low
    assert h1.forward_return_pct == pytest.approx((10.9 - 10.0) / 10.0)

    h5 = result.horizons[1]
    assert h5.is_censored is True
    assert h5.censoring_reason == END_OF_EXPERIMENT
    assert h5.sessions_observed == 2  # only Jan 8/9 exist after expiry within the calendar
    assert h5.mfe_price == pytest.approx(11.2)  # Jan 9's High
    assert h5.mae_price == pytest.approx(10.4)  # Jan 8's Low
    assert h5.forward_return_pct == pytest.approx((11.0 - 10.0) / 10.0)  # last close, Jan 9


def test_expired_order_with_no_attempts_raises():
    order = EntryOrder(
        order_id="o1", candidate_id="c1", symbol="BBB", signal_date=date(2026, 1, 5),
        created_date=date(2026, 1, 5), valid_until=date(2026, 1, 7), max_entry_price=10.0, status=EXPIRED,
    )
    context = _FakeContext(pd.DataFrame([_bar("BBB", date(2026, 1, 5), 10, 10, 10, 10)]), [], date(2026, 1, 5))

    with pytest.raises(EntryTimingComputationError):
        compute_entry_timing_for_expired_order(context, order, (date(2026, 1, 5),))


def test_non_expired_order_raises():
    order = EntryOrder(
        order_id="o1", candidate_id="c1", symbol="BBB", signal_date=date(2026, 1, 5),
        created_date=date(2026, 1, 5), valid_until=date(2026, 1, 7), max_entry_price=10.0, status=FILLED,
    )
    context = _FakeContext(pd.DataFrame([_bar("BBB", date(2026, 1, 5), 10, 10, 10, 10)]), [], date(2026, 1, 5))

    with pytest.raises(EntryTimingComputationError):
        compute_entry_timing_for_expired_order(context, order, (date(2026, 1, 5),))
