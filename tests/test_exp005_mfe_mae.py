"""Tests for EXP-005's MFE/MAE diagnostics (Revision 5, Section 20, Stage 12) --
hand-computed fixtures covering the entry/exit-session ambiguity exclusion rule
for FILLED_AT_OPEN/FILLED_AT_CEILING entries and SELL_TIME/open-triggered-
SELL_TARGET/intraday-triggered-SELL_TARGET exits, plus open (unresolved)
positions.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.entry_order import FILLED_AT_CEILING, FILLED_AT_OPEN
from stock_analyzer.sandbox.domain.position import CLOSED, OPEN, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import SELL_TARGET, SELL_TIME
from stock_analyzer.sandbox.exp005.diagnostics.mfe_mae import MfeMaeComputationError, compute_mfe_mae
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import to_money_units, to_price_units, to_quantity_units, to_rate_units

REPLAY_ID = "replay-1"
NOW = datetime.now(timezone.utc)
SYMBOL = "AAA"


def _bar(d: date, o: float, h: float, low: float, c: float) -> dict:
    return {"symbol": SYMBOL, "date": pd.Timestamp(d), "Open": o, "High": h, "Low": low, "Close": c}


STANDARD_PRICES = pd.DataFrame(
    [
        _bar(date(2026, 1, 5), 100.0, 105.0, 98.0, 102.0),
        _bar(date(2026, 1, 6), 102.0, 110.0, 101.0, 108.0),
        _bar(date(2026, 1, 7), 108.0, 107.0, 95.0, 100.0),
    ]
)


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


def _buy_execution(position_id: str, fill_reason: str, effective_price: float, execution_date: date) -> Execution:
    return Execution(
        execution_id=f"{position_id}:BUY", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
        order_id=f"{position_id}:order", candidate_id=position_id, position_id=position_id, symbol=SYMBOL,
        side=BUY, decision_date=execution_date, execution_date=execution_date,
        raw_market_fill_price_units=to_price_units(effective_price), effective_fill_price_units=to_price_units(effective_price),
        quantity_units=to_quantity_units(10.0), gross_notional_units=to_money_units(effective_price * 10.0),
        commission_units=to_money_units(1.0), slippage_rate_units=to_rate_units(0.0),
        slippage_cost_units=0, net_cash_flow_units=-to_money_units(effective_price * 10.0 + 1.0),
        fill_reason=fill_reason, market_data_snapshot_id="snap-1", created_at=NOW,
    )


def _sell_execution(position_id: str, fill_reason: str, effective_price: float, execution_date: date) -> Execution:
    return Execution(
        execution_id=f"{position_id}:SELL", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
        order_id=None, candidate_id=position_id, position_id=position_id, symbol=SYMBOL,
        side=SELL, decision_date=execution_date, execution_date=execution_date,
        raw_market_fill_price_units=to_price_units(effective_price), effective_fill_price_units=to_price_units(effective_price),
        quantity_units=to_quantity_units(10.0), gross_notional_units=to_money_units(effective_price * 10.0),
        commission_units=to_money_units(1.0), slippage_rate_units=to_rate_units(0.0),
        slippage_cost_units=0, net_cash_flow_units=to_money_units(effective_price * 10.0 - 1.0),
        fill_reason=fill_reason, market_data_snapshot_id="snap-1", created_at=NOW,
    )


def _position(
    position_id: str, entry_date: date, target_price: float, status: str = OPEN,
    exit_date: date | None = None, exit_reason: str | None = None, current_close: float | None = None,
) -> VirtualPosition:
    return VirtualPosition(
        position_id=position_id, symbol=SYMBOL, candidate_id=position_id, order_id=f"{position_id}:order",
        signal_date=entry_date, entry_date=entry_date, entry_price=100.0, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=100.0, max_entry_price=101.0,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=target_price,
        planned_time_exit_date=date(2026, 2, 2), status=status, exit_date=exit_date, exit_reason=exit_reason,
        current_close=current_close,
    )


# ------------------------------------------------------- unambiguous entry + exit


def test_filled_at_open_and_sell_time_includes_full_window():
    position_id = "p1"
    position = _position(position_id, date(2026, 1, 5), target_price=999.0, status=CLOSED, exit_date=date(2026, 1, 7), exit_reason=SELL_TIME)
    executions = [
        _buy_execution(position_id, FILLED_AT_OPEN, 100.0, date(2026, 1, 5)),
        _sell_execution(position_id, SELL_TIME, 100.0, date(2026, 1, 7)),
    ]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    assert result.window_start_date == date(2026, 1, 5)
    assert result.window_end_date == date(2026, 1, 7)
    assert result.mfe_pct == pytest.approx(0.10)  # (110-100)/100, from Jan 6's High
    assert result.mfe_date == date(2026, 1, 6)
    assert result.sessions_to_mfe == 2
    assert result.mae_pct == pytest.approx(-0.05)  # (95-100)/100, from Jan 7's Low
    assert result.mae_date == date(2026, 1, 7)
    assert result.sessions_to_mae == 3
    assert result.realized_or_mtm_return_pct == pytest.approx(0.0)
    assert result.peak_to_exit_giveback_pct == pytest.approx(0.10)
    assert result.exit_efficiency == pytest.approx(0.0)
    assert result.is_closed is True


def test_filled_at_ceiling_excludes_entry_session():
    position_id = "p2"
    position = _position(position_id, date(2026, 1, 5), target_price=999.0, status=CLOSED, exit_date=date(2026, 1, 7), exit_reason=SELL_TIME)
    executions = [
        _buy_execution(position_id, FILLED_AT_CEILING, 100.0, date(2026, 1, 5)),
        _sell_execution(position_id, SELL_TIME, 100.0, date(2026, 1, 7)),
    ]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    assert result.window_start_date == date(2026, 1, 6)  # Jan 5 excluded
    assert result.mfe_pct == pytest.approx(0.10)  # still Jan 6's High=110
    assert result.sessions_to_mfe == 1  # first session in the (narrower) window
    assert result.mae_pct == pytest.approx(-0.05)  # Jan 7's Low=95 (Jan 5's Low=98 would have been closer but excluded)
    assert result.sessions_to_mae == 2


def test_sell_target_at_open_is_unambiguous_and_included():
    position_id = "p3"
    # Jan 7's own Open (108) >= target (105) -- unambiguous, matches _check_target's open branch.
    position = _position(position_id, date(2026, 1, 5), target_price=105.0, status=CLOSED, exit_date=date(2026, 1, 7), exit_reason=SELL_TARGET)
    executions = [
        _buy_execution(position_id, FILLED_AT_OPEN, 100.0, date(2026, 1, 5)),
        _sell_execution(position_id, SELL_TARGET, 108.0, date(2026, 1, 7)),
    ]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    assert result.window_end_date == date(2026, 1, 7)  # included
    assert result.mfe_pct == pytest.approx(0.10)
    assert result.mae_pct == pytest.approx(-0.05)  # Jan 7's Low still counted
    assert result.realized_or_mtm_return_pct == pytest.approx(0.08)  # (108-100)/100
    assert result.peak_to_exit_giveback_pct == pytest.approx(0.02)
    assert result.exit_efficiency == pytest.approx(0.8)


def test_sell_target_intraday_touch_is_ambiguous_and_excludes_exit_session():
    position_id = "p4"
    # Jan 7's own Open (101, using a lower-open variant) is BELOW a 105 target, but
    # the session's High (107) reached it -- order within the session unknown.
    prices = pd.DataFrame(
        [
            _bar(date(2026, 1, 5), 100.0, 105.0, 98.0, 102.0),
            _bar(date(2026, 1, 6), 102.0, 110.0, 101.0, 108.0),
            _bar(date(2026, 1, 7), 101.0, 107.0, 95.0, 106.0),  # open (101) < target (105)
        ]
    )
    position = _position(position_id, date(2026, 1, 5), target_price=105.0, status=CLOSED, exit_date=date(2026, 1, 7), exit_reason=SELL_TARGET)
    executions = [
        _buy_execution(position_id, FILLED_AT_OPEN, 100.0, date(2026, 1, 5)),
        _sell_execution(position_id, SELL_TARGET, 105.0, date(2026, 1, 7)),  # exit at the target price itself
    ]
    context = _FakeContext(prices, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    assert result.window_end_date == date(2026, 1, 6)  # Jan 7 excluded
    assert result.mfe_pct == pytest.approx(0.10)  # still Jan 6's High
    assert result.mfe_date == date(2026, 1, 6)
    assert result.mae_pct == pytest.approx(-0.02)  # Jan 5's Low=98 (Jan 7's Low=95 excluded)
    assert result.mae_date == date(2026, 1, 5)
    assert result.realized_or_mtm_return_pct == pytest.approx(0.05)  # (105-100)/100 -- the KNOWN exit price is still used
    assert result.exit_efficiency == pytest.approx(0.5)


# --------------------------------------------------------------------- open positions


def test_open_position_window_extends_to_outcome_data_end_date():
    position_id = "p5"
    position = _position(position_id, date(2026, 1, 5), target_price=999.0, status=OPEN, current_close=99.0)
    executions = [_buy_execution(position_id, FILLED_AT_OPEN, 100.0, date(2026, 1, 5))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    assert result.is_closed is False
    assert result.window_start_date == date(2026, 1, 5)
    assert result.window_end_date == date(2026, 1, 7)
    assert result.mfe_pct == pytest.approx(0.10)
    assert result.mae_pct == pytest.approx(-0.05)
    assert result.realized_or_mtm_return_pct == pytest.approx(-0.01)  # (99-100)/100, current_close used


def test_open_position_falls_back_to_entry_price_when_current_close_missing():
    position_id = "p6"
    position = _position(position_id, date(2026, 1, 5), target_price=999.0, status=OPEN, current_close=None)
    executions = [_buy_execution(position_id, FILLED_AT_OPEN, 100.0, date(2026, 1, 5))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    assert result.realized_or_mtm_return_pct == pytest.approx(0.0)  # falls back to effective_entry_price


# ------------------------------------------------------------------------- edge cases


def test_empty_window_falls_back_to_known_entry_exit_boundary_points():
    position_id = "p7"
    # FILLED_AT_CEILING entry on Jan 6 -> window starts Jan 7. SELL_TARGET-intraday
    # exit on Jan 7 -> window ends Jan 6 (previous session). start > end -> empty --
    # no observed session at all, so MFE/MAE fall back to the two KNOWN boundary
    # prices (entry, exit) rather than raising (Stage 11-15 closure, finding 4).
    prices = pd.DataFrame(
        [
            _bar(date(2026, 1, 5), 100.0, 105.0, 98.0, 102.0),
            _bar(date(2026, 1, 6), 102.0, 110.0, 101.0, 108.0),
            _bar(date(2026, 1, 7), 101.0, 107.0, 95.0, 106.0),
        ]
    )
    position = _position(position_id, date(2026, 1, 6), target_price=105.0, status=CLOSED, exit_date=date(2026, 1, 7), exit_reason=SELL_TARGET)
    executions = [
        _buy_execution(position_id, FILLED_AT_CEILING, 102.0, date(2026, 1, 6)),
        _sell_execution(position_id, SELL_TARGET, 105.0, date(2026, 1, 7)),
    ]
    context = _FakeContext(prices, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    assert result.mfe_price == pytest.approx(105.0)  # the known exit price -- the higher of the two known points
    assert result.mfe_date == date(2026, 1, 7)
    assert result.mfe_pct == pytest.approx((105.0 - 102.0) / 102.0)
    assert result.sessions_to_mfe == 2
    assert result.mae_price == pytest.approx(102.0)  # the known entry price -- the lower of the two known points
    assert result.mae_date == date(2026, 1, 6)
    assert result.mae_pct == pytest.approx(0.0)
    assert result.sessions_to_mae == 1
    assert result.realized_or_mtm_return_pct == pytest.approx((105.0 - 102.0) / 102.0)
    assert result.peak_to_exit_giveback_pct == pytest.approx(0.0)
    assert result.exit_efficiency == pytest.approx(1.0)


def test_ambiguous_target_exit_mfe_never_understates_the_known_realized_exit():
    """The reviewer's exact failure scenario: an intraday SELL_TARGET exit whose
    prior (non-excluded) window High is LOWER than the realized exit price. MFE
    must never be reported below the known realized return -- otherwise giveback
    goes negative and exit efficiency exceeds 1, both nonsensical for an ordinary
    profitable target exit."""

    position_id = "p7b"
    # Prior session (Jan 6, included) tops out at High=104 -- well below the
    # eventual target/exit price of 110. Exit session (Jan 7) is excluded (open
    # 100 < target 110 <= high 112, an ambiguous intraday touch).
    prices = pd.DataFrame(
        [
            _bar(date(2026, 1, 5), 100.0, 101.0, 98.0, 100.0),
            _bar(date(2026, 1, 6), 100.0, 104.0, 99.0, 103.0),
            _bar(date(2026, 1, 7), 100.0, 112.0, 95.0, 108.0),
        ]
    )
    position = _position(position_id, date(2026, 1, 5), target_price=110.0, status=CLOSED, exit_date=date(2026, 1, 7), exit_reason=SELL_TARGET)
    executions = [
        _buy_execution(position_id, FILLED_AT_OPEN, 100.0, date(2026, 1, 5)),
        _sell_execution(position_id, SELL_TARGET, 110.0, date(2026, 1, 7)),
    ]
    context = _FakeContext(prices, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    # Without the fix, mfe_price would be Jan 6's High (104) -- BELOW the known
    # realized exit (110), which is the exact defect the reviewer flagged.
    assert result.mfe_price == pytest.approx(110.0)
    assert result.mfe_date == date(2026, 1, 7)
    assert result.mfe_pct == pytest.approx(0.10)
    assert result.realized_or_mtm_return_pct == pytest.approx(0.10)
    assert result.mfe_pct >= result.realized_or_mtm_return_pct
    assert result.peak_to_exit_giveback_pct == pytest.approx(0.0)
    assert result.exit_efficiency == pytest.approx(1.0)
    # MAE is unaffected -- the exit price (110) is not the window's minimum.
    assert result.mae_price == pytest.approx(98.0)  # Jan 5's Low (entry session, included)


def test_exit_efficiency_none_when_mfe_is_exactly_zero():
    position_id = "p8"
    # entry_price=110 -- no session's High ever exceeds it, so MFE == 0 exactly.
    position = _position(position_id, date(2026, 1, 5), target_price=999.0, status=CLOSED, exit_date=date(2026, 1, 7), exit_reason=SELL_TIME)
    executions = [
        _buy_execution(position_id, FILLED_AT_OPEN, 110.0, date(2026, 1, 5)),
        _sell_execution(position_id, SELL_TIME, 100.0, date(2026, 1, 7)),
    ]
    prices = pd.DataFrame(
        [
            _bar(date(2026, 1, 5), 110.0, 110.0, 98.0, 102.0),
            _bar(date(2026, 1, 6), 102.0, 105.0, 101.0, 103.0),
            _bar(date(2026, 1, 7), 103.0, 108.0, 95.0, 100.0),
        ]
    )
    context = _FakeContext(prices, executions, date(2026, 1, 7))

    result = compute_mfe_mae(context, position)

    assert result.mfe_pct == pytest.approx(0.0)
    assert result.exit_efficiency is None


def test_missing_buy_execution_raises():
    position_id = "p9"
    position = _position(position_id, date(2026, 1, 5), target_price=999.0, status=OPEN)
    context = _FakeContext(STANDARD_PRICES, [], date(2026, 1, 7))

    with pytest.raises(MfeMaeComputationError):
        compute_mfe_mae(context, position)


def test_missing_sell_execution_for_closed_position_raises():
    position_id = "p10"
    position = _position(position_id, date(2026, 1, 5), target_price=999.0, status=CLOSED, exit_date=date(2026, 1, 7), exit_reason=SELL_TIME)
    executions = [_buy_execution(position_id, FILLED_AT_OPEN, 100.0, date(2026, 1, 5))]
    context = _FakeContext(STANDARD_PRICES, executions, date(2026, 1, 7))

    with pytest.raises(MfeMaeComputationError):
        compute_mfe_mae(context, position)
