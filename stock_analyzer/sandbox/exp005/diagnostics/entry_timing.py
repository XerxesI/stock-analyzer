"""Entry-timing diagnostics -- Revision 5, Section 23, Stage 13.

For every filled BUY: signal close vs. the next session's own open (frozen OHLCV,
independent of which session actually filled -- see the entry-gap formula below);
raw and effective fill price and slippage (`executions`); the fill price's location
within the execution session's own range (0 = filled at the session low, 1 = at the
session high); forward return, MFE, and MAE (Section 20's entry-session-ambiguity
rule, applied per horizon) at 1/5/10/20 sessions, with time to MFE/MAE within each
horizon; whether the +20% target was reached within the position's actual 20-session
holding horizon.

For every unfilled/expired order: the ceiling (`max_entry_price`); the closest
distance the price came to the ceiling without triggering, over the sessions
actually attempted (`entry_order_attempts`, reused unchanged); subsequent MFE/MAE
computed from the ceiling price as the hypothetical reference, tracked forward from
the order's expiry date, at the same four horizons.

This distinguishes "the ranking picked a bad stock" from "the ranking picked a good
stock the entry rule couldn't reach at the permitted price" -- two different failure
modes a raw fill-rate number alone cannot tell apart.

`is_censored` (Section 27) is reported for every fixed-horizon value in both cases,
per Section 27's general rule for Sections 21-24 even though Section 23's own prose
only restates it for the expired-order case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_analyzer.sandbox.domain.entry_order import EXPIRED, FILLED_AT_CEILING, FILLED_AT_OPEN, EntryOrder
from stock_analyzer.sandbox.domain.position import CLOSED, VirtualPosition
from stock_analyzer.sandbox.exp005.diagnostics._shared import compute_forward_horizon, next_session, symbol_sessions
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.domain.execution import BUY
from stock_analyzer.sandbox.exp005.domain.units import money_units_to_float, price_units_to_float, rate_units_to_float

HORIZONS_SESSIONS = (1, 5, 10, 20)


class EntryTimingComputationError(RuntimeError):
    """Raised for a position/order whose data is inconsistent with this
    computation's preconditions (missing BUY execution, unrecognized fill_reason,
    an expired order with no attempts, or an order that is neither filled nor
    expired)."""


@dataclass(frozen=True)
class EntryTimingHorizonResult:
    horizon_sessions: int
    sessions_observed: int
    is_censored: bool
    censoring_reason: str | None
    forward_return_pct: float | None
    mfe_pct: float | None
    mfe_price: float | None
    mfe_date: date | None
    sessions_to_mfe: int | None
    mae_pct: float | None
    mae_price: float | None
    mae_date: date | None
    sessions_to_mae: int | None


@dataclass(frozen=True)
class EntryTimingFilledResult:
    order_id: str
    position_id: str
    candidate_id: str
    symbol: str
    signal_date: date
    entry_date: date
    fill_reason: str
    signal_close: float
    next_session_open: float | None
    entry_gap_pct: float | None
    raw_fill_price: float
    effective_fill_price: float
    slippage_cost: float
    slippage_rate_pct: float
    fill_percentile: float | None
    target_reached_within_actual_holding_horizon: bool
    horizons: tuple[EntryTimingHorizonResult, ...]


@dataclass(frozen=True)
class EntryTimingExpiredResult:
    order_id: str
    candidate_id: str
    symbol: str
    signal_date: date
    max_entry_price: float
    expiry_date: date
    min_distance_to_ceiling_pct: float | None
    horizons: tuple[EntryTimingHorizonResult, ...]


def _horizon_result(horizon: int, fh, reference_price: float) -> EntryTimingHorizonResult:
    if fh.sessions_observed == 0:
        return EntryTimingHorizonResult(
            horizon_sessions=horizon, sessions_observed=0, is_censored=fh.is_censored,
            censoring_reason=fh.censoring_reason, forward_return_pct=None,
            mfe_pct=None, mfe_price=None, mfe_date=None, sessions_to_mfe=None,
            mae_pct=None, mae_price=None, mae_date=None, sessions_to_mae=None,
        )

    window = fh.window
    last_close = float(window.iloc[-1]["Close"])
    forward_return_pct = (last_close - reference_price) / reference_price

    ordered_dates = list(window.index)
    mfe_date = window["High"].idxmax()
    mfe_price = float(window.loc[mfe_date, "High"])
    mfe_pct = (mfe_price - reference_price) / reference_price
    sessions_to_mfe = ordered_dates.index(mfe_date) + 1

    mae_date = window["Low"].idxmin()
    mae_price = float(window.loc[mae_date, "Low"])
    mae_pct = (mae_price - reference_price) / reference_price
    sessions_to_mae = ordered_dates.index(mae_date) + 1

    return EntryTimingHorizonResult(
        horizon_sessions=horizon, sessions_observed=fh.sessions_observed, is_censored=fh.is_censored,
        censoring_reason=fh.censoring_reason, forward_return_pct=forward_return_pct,
        mfe_pct=mfe_pct, mfe_price=mfe_price, mfe_date=mfe_date, sessions_to_mfe=sessions_to_mfe,
        mae_pct=mae_pct, mae_price=mae_price, mae_date=mae_date, sessions_to_mae=sessions_to_mae,
    )


def _inclusive_window_start(sessions, entry_date: date, fill_reason: str) -> date | None:
    """Mirrors mfe_mae.py's entry-session-ambiguity rule (Section 20) -- duplicated
    here (not imported) since it is a small, frozen, 3-branch rule and entry_timing
    needs it in its OWN calendar-relative form (see _calendar_reference_date), not
    mfe_mae's own return type."""

    if fill_reason == FILLED_AT_OPEN:
        return entry_date
    if fill_reason == FILLED_AT_CEILING:
        return next_session(sessions, entry_date)
    raise EntryTimingComputationError(f"unrecognized BUY fill_reason {fill_reason!r}.")


def _calendar_reference_date(calendar: tuple[date, ...], entry_date: date, fill_reason: str) -> date:
    """The date to pass as compute_forward_horizon's `reference_date` so its
    (exclusive) `> reference_date` window starts exactly at
    _inclusive_window_start's own inclusive boundary."""

    if fill_reason == FILLED_AT_OPEN:
        earlier = [d for d in calendar if d < entry_date]
        return max(earlier) if earlier else date.min
    if fill_reason == FILLED_AT_CEILING:
        return entry_date
    raise EntryTimingComputationError(f"unrecognized BUY fill_reason {fill_reason!r}.")


def compute_entry_timing_for_filled_order(
    context: DiagnosticsContext, order: EntryOrder, position: VirtualPosition, calendar: tuple[date, ...]
) -> EntryTimingFilledResult:
    executions = context.portfolio_repo.list_executions_for_position(position.position_id)
    buy_execution = next((e for e in executions if e.side == BUY), None)
    if buy_execution is None:
        raise EntryTimingComputationError(f"position {position.position_id} has no BUY execution.")

    candidate = context.sandbox_repo.get_candidate(order.candidate_id)
    if candidate is None:
        raise EntryTimingComputationError(f"no ranked_candidates row for {order.candidate_id}.")

    raw_fill_price = price_units_to_float(buy_execution.raw_market_fill_price_units)
    effective_fill_price = price_units_to_float(buy_execution.effective_fill_price_units)
    slippage_cost = money_units_to_float(buy_execution.slippage_cost_units)
    slippage_rate_pct = rate_units_to_float(buy_execution.slippage_rate_units)

    sessions = symbol_sessions(context.prices_df, position.symbol)

    next_session_date = next_session(sessions, order.signal_date)
    next_session_open = float(sessions.loc[next_session_date, "Open"]) if next_session_date is not None else None
    entry_gap_pct = (
        (next_session_open - candidate.signal_close) / candidate.signal_close
        if next_session_open is not None
        else None
    )

    attempts = context.sandbox_repo.get_attempts_for_order(order.order_id)
    fill_attempt = next((a for a in attempts if a.attempt_date == position.entry_date), None)
    fill_percentile = None
    if fill_attempt is not None and fill_attempt.session_high is not None and fill_attempt.session_low is not None:
        session_range = fill_attempt.session_high - fill_attempt.session_low
        if session_range > 0:
            fill_percentile = (raw_fill_price - fill_attempt.session_low) / session_range

    fill_reason = buy_execution.fill_reason
    horizons = tuple(
        _horizon_result(
            h,
            compute_forward_horizon(
                sessions, calendar, _calendar_reference_date(calendar, position.entry_date, fill_reason),
                h, context.manifest.outcome_data_end_date,
            ),
            effective_fill_price,
        )
        for h in HORIZONS_SESSIONS
    )

    window_start = _inclusive_window_start(sessions, position.entry_date, fill_reason)
    window_end = position.exit_date if position.status == CLOSED else min(
        position.planned_time_exit_date, context.manifest.outcome_data_end_date
    )
    target_reached = False
    if window_start is not None and window_start <= window_end:
        window = sessions.loc[(sessions.index >= window_start) & (sessions.index <= window_end)]
        target_reached = bool((window["High"] >= position.target_price).any()) if not window.empty else False

    return EntryTimingFilledResult(
        order_id=order.order_id, position_id=position.position_id, candidate_id=order.candidate_id,
        symbol=order.symbol, signal_date=order.signal_date, entry_date=position.entry_date,
        fill_reason=fill_reason, signal_close=candidate.signal_close, next_session_open=next_session_open,
        entry_gap_pct=entry_gap_pct, raw_fill_price=raw_fill_price, effective_fill_price=effective_fill_price,
        slippage_cost=slippage_cost, slippage_rate_pct=slippage_rate_pct, fill_percentile=fill_percentile,
        target_reached_within_actual_holding_horizon=target_reached, horizons=horizons,
    )


def compute_entry_timing_for_expired_order(
    context: DiagnosticsContext, order: EntryOrder, calendar: tuple[date, ...]
) -> EntryTimingExpiredResult:
    if order.status != EXPIRED:
        raise EntryTimingComputationError(f"order {order.order_id} has status {order.status!r}, not EXPIRED.")

    attempts = context.sandbox_repo.get_attempts_for_order(order.order_id)
    if not attempts:
        raise EntryTimingComputationError(f"expired order {order.order_id} has no entry_order_attempts rows.")

    distances = [
        (a.session_low - order.max_entry_price) / order.max_entry_price
        for a in attempts
        if a.session_low is not None
    ]
    min_distance_to_ceiling_pct = min(distances) if distances else None
    expiry_date = attempts[-1].attempt_date

    sessions = symbol_sessions(context.prices_df, order.symbol)
    horizons = tuple(
        _horizon_result(
            h,
            compute_forward_horizon(sessions, calendar, expiry_date, h, context.manifest.outcome_data_end_date),
            order.max_entry_price,
        )
        for h in HORIZONS_SESSIONS
    )

    return EntryTimingExpiredResult(
        order_id=order.order_id, candidate_id=order.candidate_id, symbol=order.symbol,
        signal_date=order.signal_date, max_entry_price=order.max_entry_price, expiry_date=expiry_date,
        min_distance_to_ceiling_pct=min_distance_to_ceiling_pct, horizons=horizons,
    )
