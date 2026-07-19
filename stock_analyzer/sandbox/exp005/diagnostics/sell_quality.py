"""Post-exit diagnostics -- Revision 5, Section 21, Stage 13.

For every closed position, at fixed forward horizons of 1, 5, 10, and 20 trading
sessions after the exit session, computed from the frozen OHLCV starting the session
after exit: close-to-close return from `effective_exit_price`; the maximum subsequent
High and minimum subsequent Low relative to `effective_exit_price` (the best/worst
post-exit excursion); whether the position's own `target_price` would have been
reached after exit, within the horizon; `is_censored` (Section 27).

**These are diagnostic fields only and never feed replay decisions** -- they are
computed strictly after the replay for that period is complete, and positive
post-exit performance is never automatically labelled "wrong sell." They are
post-exit opportunity/regret evidence, a description of what happened next, not a
verdict on whether the sale was correct.

`effective_exit_price` always comes from EXP-005's own `executions` ledger, never
core's raw `virtual_positions.exit_price` (see the Stage 6 dual-accounting errata).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_analyzer.sandbox.domain.position import CLOSED, VirtualPosition
from stock_analyzer.sandbox.exp005.diagnostics._shared import compute_forward_horizon, symbol_sessions
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.domain.execution import BUY
from stock_analyzer.sandbox.exp005.domain.units import price_units_to_float

HORIZONS_SESSIONS = (1, 5, 10, 20)


class SellQualityComputationError(RuntimeError):
    """Raised for a position that is not closed, or has no SELL execution --
    post-exit diagnostics are only defined for an actual exit."""


@dataclass(frozen=True)
class SellQualityHorizonResult:
    horizon_sessions: int
    sessions_observed: int
    is_censored: bool
    censoring_reason: str | None
    close_to_close_return_pct: float | None
    max_high_price: float | None
    max_high_pct: float | None
    min_low_price: float | None
    min_low_pct: float | None
    target_reached: bool
    sessions_to_target: int | None


@dataclass(frozen=True)
class SellQualityResult:
    position_id: str
    candidate_id: str
    symbol: str
    exit_date: date
    exit_reason: str
    target_price: float
    effective_exit_price: float
    horizons: tuple[SellQualityHorizonResult, ...]


def _horizon_result(horizon: int, fh, effective_exit_price: float, target_price: float) -> SellQualityHorizonResult:
    if fh.sessions_observed == 0:
        return SellQualityHorizonResult(
            horizon_sessions=horizon, sessions_observed=0, is_censored=fh.is_censored,
            censoring_reason=fh.censoring_reason, close_to_close_return_pct=None,
            max_high_price=None, max_high_pct=None, min_low_price=None, min_low_pct=None,
            target_reached=False, sessions_to_target=None,
        )

    window = fh.window
    last_close = float(window.iloc[-1]["Close"])
    close_to_close_return_pct = (last_close - effective_exit_price) / effective_exit_price

    max_high_price = float(window["High"].max())
    max_high_pct = (max_high_price - effective_exit_price) / effective_exit_price
    min_low_price = float(window["Low"].min())
    min_low_pct = (min_low_price - effective_exit_price) / effective_exit_price

    target_hits = window.index[window["High"] >= target_price]
    target_reached = len(target_hits) > 0
    sessions_to_target = (list(window.index).index(target_hits[0]) + 1) if target_reached else None

    return SellQualityHorizonResult(
        horizon_sessions=horizon, sessions_observed=fh.sessions_observed, is_censored=fh.is_censored,
        censoring_reason=fh.censoring_reason, close_to_close_return_pct=close_to_close_return_pct,
        max_high_price=max_high_price, max_high_pct=max_high_pct,
        min_low_price=min_low_price, min_low_pct=min_low_pct,
        target_reached=target_reached, sessions_to_target=sessions_to_target,
    )


def compute_sell_quality(
    context: DiagnosticsContext, position: VirtualPosition, calendar: tuple[date, ...]
) -> SellQualityResult:
    if position.status != CLOSED:
        raise SellQualityComputationError(f"position {position.position_id} is not closed -- no exit to diagnose.")

    executions = context.portfolio_repo.list_executions_for_position(position.position_id)
    sell_execution = next((e for e in executions if e.side != BUY), None)
    if sell_execution is None:
        raise SellQualityComputationError(f"closed position {position.position_id} has no SELL execution.")

    effective_exit_price = price_units_to_float(sell_execution.effective_fill_price_units)
    sessions = symbol_sessions(context.prices_df, position.symbol)

    horizons = tuple(
        _horizon_result(
            h,
            compute_forward_horizon(sessions, calendar, position.exit_date, h, context.manifest.outcome_data_end_date),
            effective_exit_price,
            position.target_price,
        )
        for h in HORIZONS_SESSIONS
    )

    return SellQualityResult(
        position_id=position.position_id,
        candidate_id=position.candidate_id,
        symbol=position.symbol,
        exit_date=position.exit_date,
        exit_reason=position.exit_reason,
        target_price=position.target_price,
        effective_exit_price=effective_exit_price,
        horizons=horizons,
    )
